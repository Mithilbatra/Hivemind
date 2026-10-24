"""The task state machine driver. `advance` is idempotent: call it again after a crash, approval or retry."""
import time
from datetime import UTC, datetime

from sqlalchemy import func

from .. import states
from ..agents import get_agent, route
from ..config import get_settings
from ..models import (
    AgentSession,
    Approval,
    BranchWorktree,
    Project,
    Repository,
    Task,
    TaskDependency,
    ValidationResult,
)
from ..states import transition
from . import context, planner, prompts, repo_state, risk, validation
from .audit import emit
from .sessions import run_session

IN_FLIGHT = (states.PLANNING, states.ASSIGNED, states.RUNNING, states.VALIDATION, states.BLOCKED, states.REVIEW)


def close_stale_sessions(db, top_id: int, reason: str) -> None:
    """Sessions still marked running when we hold the task lock were left behind by a worker that died."""
    stale = db.query(AgentSession).filter_by(top_task_id=top_id, status="running").all()
    for s in stale:
        s.status, s.ended_at, s.summary = "abandoned", datetime.now(UTC), reason
        emit(db, s.task_id, "session_abandoned", f"{s.agent_role} session #{s.id}: {reason}")
    if stale:
        db.commit()


def team_lines(db, top) -> list[str]:
    """What the other in-flight tasks of this project are doing, so agents avoid each other's files."""
    others = (db.query(Task).filter(Task.project_id == top.project_id, Task.parent_id.is_(None), Task.id != top.id,
                                    Task.status.in_(IN_FLIGHT)).order_by(Task.id).limit(10).all())
    if not others:
        return []
    ids = [o.id for o in others]
    files = {b.task_id: b.touched_files or [] for b in db.query(BranchWorktree).filter(BranchWorktree.task_id.in_(ids))}
    repos = dict(db.query(Repository.id, Repository.name).filter(Repository.id.in_({o.repo_id for o in others})).all())
    return [f"- #{o.id} {o.title} [{repos.get(o.repo_id)}, {o.status}]"
            + (f" editing: {', '.join(files[o.id][:8])}" if files.get(o.id) else "") for o in others]


def children_of(db, top) -> list[Task]:
    return db.query(Task).filter_by(parent_id=top.id).order_by(Task.position).all()


def worktree_of(db, top) -> BranchWorktree:
    return db.query(BranchWorktree).filter_by(task_id=top.id).one()


def sleep_interruptible(db, top, seconds: float) -> None:
    end = time.time() + seconds
    while time.time() < end:
        db.refresh(top)
        if top.cancel_requested:
            return
        time.sleep(min(0.5, max(0.0, end - time.time())))


def backoff(n: int) -> float:
    return get_settings().retry_backoff_s * (2 ** max(0, n - 1))


def advance(db, task_id: int) -> Task:
    top = db.get(Task, task_id)
    if top is None or top.parent_id is not None:
        raise ValueError(f"task {task_id} is not a top-level task")
    repo, project = db.get(Repository, top.repo_id), db.get(Project, top.project_id)
    close_stale_sessions(db, top.id, "worker stopped before the session finished")
    phases = {states.QUEUED: _plan, states.PLANNING: _plan, states.ASSIGNED: _start,
              states.RUNNING: _run, states.VALIDATION: _validate}
    for _ in range(1000):  # guard against a phase that makes no progress
        db.refresh(top)
        if top.status in states.TERMINAL or top.status in (states.BLOCKED, states.REVIEW):
            return top
        if top.cancel_requested:
            cancel_now(db, top, "cancel requested")
            return top
        phases[top.status](db, top, repo, project)
    raise RuntimeError(f"task {task_id} made no progress")


def cancel_now(db, top, reason: str, actor: str = "system") -> None:
    """Terminal cancel: children, worktree and branch are cleaned up. The diff stays in the DB."""
    if top.status in states.TERMINAL:
        return
    close_stale_sessions(db, top.id, "task cancelled")
    for c in children_of(db, top):
        if c.status not in states.TERMINAL:
            transition(db, c, states.CANCELLED, reason, actor)
    top.cancel_requested = True
    transition(db, top, states.CANCELLED, reason, actor)
    release(db, top, delete_branch=True)


def release(db, top, delete_branch: bool) -> None:
    bw = db.query(BranchWorktree).filter_by(task_id=top.id).one_or_none()
    if bw and bw.active:
        repo_state.release_worktree(db, bw, db.get(Repository, top.repo_id), delete_branch)


def fail(db, top, reason: str) -> None:
    top.error, top.dead_letter = reason, True
    emit(db, top.id, "dead_letter", reason)
    transition(db, top, states.FAILED, reason)


# ---- phases ---------------------------------------------------------------------------------------------------

def _plan(db, top, repo, project):
    if top.status == states.QUEUED:
        transition(db, top, states.PLANNING, "planning started")
    bw = repo_state.create_worktree(db, top, repo)
    ctx = context.get_or_create_context(db, repo).summary
    if not children_of(db, top):
        steps = _make_plan(db, top, repo, project, bw, ctx)
        if steps is None:
            return
        ids = {}
        for i, st in enumerate(steps):
            c = Task(parent_id=top.id, project_id=top.project_id, repo_id=top.repo_id, title=st.title,
                     description=st.description, kind=st.kind, role=route(st.kind), position=i,
                     priority=top.priority, risk_level=top.risk_level, max_retries=top.max_retries,
                     created_by=top.created_by)
            db.add(c)
            db.flush()
            ids[st.key] = c.id
        for st in steps:
            for d in st.deps:
                db.add(TaskDependency(task_id=ids[st.key], depends_on_id=ids[d]))
        emit(db, top.id, "plan_created", f"{len(steps)} subtasks",
             {"steps": [{"key": s.key, "kind": s.kind, "deps": s.deps} for s in steps]})
    top.risk_score, top.risk_reasons = risk.assess(top)
    approvals = db.query(Approval).filter_by(task_id=top.id, kind="pre_run").all()
    if risk.needs_pre_run_approval(top, top.risk_score) and not any(a.status == "approved" for a in approvals):
        if not any(a.status == "pending" for a in approvals):
            db.add(Approval(task_id=top.id, kind="pre_run"))
        emit(db, top.id, "approval_required", f"pre-run approval required (risk {top.risk_score})",
             {"reasons": top.risk_reasons})
        transition(db, top, states.BLOCKED, "waiting for pre-run approval")
        return
    transition(db, top, states.ASSIGNED, "plan ready")


def _make_plan(db, top, repo, project, bw, ctx):
    """Steps in topological order, or None if the task was failed or cancelled while planning."""
    agent = get_agent(db, "planner")
    prompt = prompts.build_prompt(agent=agent, task=top, top=top, repo_text=context.render(ctx), files=[], validation=[],
                                  kind="plan", extra="\n" + planner.PLAN_INSTRUCTIONS,
                                  project_goal=project.description, team=team_lines(db, top))
    sess, res = run_session(db, task=top, top=top, repo=repo, bw=bw, agent=agent, prompt=prompt, kind="plan")
    if sess.status == "cancelled":
        return None
    if (sess.diff_summary or {}).get("violation"):  # fail closed, like any other read-only role
        fail(db, top, sess.diff_summary["violation"])
        return None
    try:
        if sess.status != "succeeded":
            raise planner.PlanError(f"planner session {sess.status}")
        return planner.parse_agent_plan(res.text)
    except planner.PlanError as e:
        emit(db, top.id, "plan_fallback", f"using template plan: {e}")
        return planner.validate_plan(planner.heuristic_plan(top.title, top.description, ctx))


def _start(db, top, repo, project):
    transition(db, top, states.RUNNING, "execution started")


def _session_prompt(db, top, repo, bw, ctx, agent, task, kind, project, failure="", feedback="", prior=None):
    checks = validation.effective_checks(top, project, ctx)
    files = prompts.select_relevant_files(bw.path, ctx.get("tree", []), f"{task.title} {task.description}")
    return prompts.build_prompt(agent=agent, task=task, top=top, repo_text=context.render(ctx), files=files,
                                validation=checks, kind=kind, failure=failure, feedback=feedback, prior=prior,
                                project_goal=project.description, team=team_lines(db, top))


def _run(db, top, repo, project):
    bw = worktree_of(db, top)
    ctx = context.get_or_create_context(db, repo).summary
    if top.pending_prompt:
        kind, text = top.pending_kind or "debug", top.pending_prompt
        agent = get_agent(db, "writer")
        prompt = _session_prompt(db, top, repo, bw, ctx, agent, top, kind, project,
                                 failure=text if kind == "debug" else "", feedback=text if kind == "feedback" else "")
        sess, _ = run_session(db, task=top, top=top, repo=repo, bw=bw, agent=agent, prompt=prompt, kind=kind,
                              attempt=top.retry_count + 1)
        if sess.status == "cancelled":
            return
        if sess.status != "succeeded":
            return fail(db, top, f"fix session {sess.status}: {sess.summary[:300]}")
        top.pending_prompt = top.pending_kind = None
        transition(db, top, states.VALIDATION, "fix applied, validating")
        return
    prior = []
    for child in children_of(db, top):
        if child.status == states.COMPLETED:
            prior.append(f"- {child.title}: {child.summary[:300]}")
            continue
        if child.status in (states.FAILED, states.CANCELLED):
            return fail(db, top, f"subtask '{child.title}' is {child.status}")
        outcome = _run_child(db, top, repo, project, bw, ctx, child, prior)
        if outcome == "cancelled":
            return
        if outcome == "failed":
            return fail(db, top, f"subtask '{child.title}' failed: {child.error}")
        prior.append(f"- {child.title}: {child.summary[:300]}")
    top.summary = "\n".join(prior)
    transition(db, top, states.VALIDATION, "all subtasks complete")


def _run_child(db, top, repo, project, bw, ctx, child, prior) -> str:
    if child.status == states.QUEUED:
        transition(db, child, states.ASSIGNED, f"routed to {child.role}")
    if child.status == states.ASSIGNED:
        transition(db, child, states.RUNNING, "started")
    agent = get_agent(db, child.role)
    for attempt in range(1, child.max_retries + 2):
        prompt = _session_prompt(db, top, repo, bw, ctx, agent, child, "task", project, prior=prior)
        sess, _ = run_session(db, task=child, top=top, repo=repo, bw=bw, agent=agent, prompt=prompt, kind="task",
                              attempt=attempt)
        if sess.status == "succeeded":
            child.summary = sess.summary
            transition(db, child, states.COMPLETED, "done")
            return "completed"
        if sess.status == "cancelled":
            return "cancelled"
        child.error = sess.summary or sess.status
        if (sess.diff_summary or {}).get("violation") or attempt > child.max_retries:
            break
        child.retry_count += 1
        emit(db, child.id, "retry", f"session {sess.status}; retry {child.retry_count}/{child.max_retries}",
             {"reason": child.error[:300]})
        sleep_interruptible(db, top, backoff(attempt))
    transition(db, child, states.FAILED, child.error or "session failed")
    return "failed"


def _validate(db, top, repo, project):
    bw = worktree_of(db, top)
    ctx = context.get_or_create_context(db, repo).summary
    checks = validation.effective_checks(top, project, ctx)
    # Not retry_count + 1: retry_count restarts at 0 after "request changes" or a manual retry.
    attempt = (db.query(func.max(ValidationResult.attempt)).filter_by(task_id=top.id).scalar() or 0) + 1
    if not checks:
        emit(db, top.id, "no_validation_configured", "no validation commands; review is mandatory")
    results = validation.run_validation(db, top, bw, checks, attempt)
    diff = repo_state.capture_diff(db, top, bw)
    failed = [r for r in results if r.required and not r.passed]
    top.risk_score, top.risk_reasons = risk.assess(top, diff.files, validation_failed=bool(failed),
                                                   no_validation=not checks, conflicts=len(bw.conflict_with or []))
    if not failed:
        db.add(Approval(task_id=top.id, kind="pre_commit"))
        transition(db, top, states.REVIEW, f"validation passed (risk {top.risk_score})")
        return
    if top.retry_count < top.max_retries:
        top.retry_count += 1
        top.pending_kind, top.pending_prompt = "debug", prompts.failure_report(failed)
        emit(db, top.id, "retry", f"validation failed ({', '.join(r.kind for r in failed)}); "
             f"retry {top.retry_count}/{top.max_retries}", {"reason": top.pending_prompt[:500]})
        transition(db, top, states.RUNNING, f"validation failed, retry {top.retry_count}/{top.max_retries}")
        sleep_interruptible(db, top, backoff(top.retry_count))
        return
    fail(db, top, f"validation failed after {top.max_retries} retries: " + "; ".join(r.command for r in failed))
