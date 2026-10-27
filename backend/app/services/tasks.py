"""Task creation and lifecycle commands shared by the API and tests."""
import uuid
from pathlib import Path

from .. import states
from ..config import get_settings
from ..models import BranchWorktree, Project, Repository, Task
from ..states import transition
from ..worker import queue
from . import orchestrator, safety
from .audit import emit
from .repo_state import git, resolve_ref

PRIORITIES = ("low", "normal", "high", "urgent")
RISKS = ("low", "medium", "high", "critical")


class BadRequest(ValueError):
    pass


def register_repo(db, path: str, project: Project, name: str | None = None, default_branch: str | None = None):
    try:
        p = safety.resolve_repo_path(path, project.allowed_roots)
    except safety.UnsafeRepo as e:
        raise BadRequest(str(e))
    repo = db.query(Repository).filter_by(path=str(p)).one_or_none()
    if repo:
        return repo
    if resolve_ref(p, "HEAD") is None:
        raise BadRequest(f"{p} has no commits yet")
    if default_branch is None:
        default_branch = repo_state_branch(p)
    repo = Repository(project_id=project.id, name=name or p.name, path=str(p), default_branch=default_branch)
    db.add(repo)
    db.commit()
    return repo


def repo_state_branch(p: Path) -> str:
    return git(p, "rev-parse", "--abbrev-ref", "HEAD", check=False).strip() or "main"


def create_tasks(db, *, title: str, description: str = "", repo_ids: list[int] | None = None,
                 repo_paths: list[str] | None = None, project_id: int | None = None, branch_target: str | None = None,
                 issue_ref: str | None = None, priority: str = "normal", risk_level: str = "low",
                 acceptance_criteria: str = "", validation_commands: list | None = None, user: str = "system") -> list[Task]:
    if priority not in PRIORITIES or risk_level not in RISKS:
        raise BadRequest("invalid priority or risk_level")
    s = get_settings()
    if s.agent_mode == "cli" and not s.agent_command.strip():  # else every session fails and burns its retries
        raise BadRequest("AGENT_COMMAND is not set; set it, or use AGENT_MODE=mock for the demo agent")
    repos = [db.get(Repository, i) for i in (repo_ids or [])]
    if any(r is None for r in repos):
        raise BadRequest("unknown repo id")
    if project_id is None:
        project_id = repos[0].project_id if repos else db.query(Project).order_by(Project.id).first().id
    project = db.get(Project, project_id)
    if project is None:
        raise BadRequest("unknown project")
    repos += [register_repo(db, p, project) for p in (repo_paths or [])]
    if not repos:
        raise BadRequest("at least one repo is required")
    for r in repos:  # re-check on every task: roots may have changed since the repo was registered
        try:
            safety.resolve_repo_path(r.path, project.allowed_roots)
        except safety.UnsafeRepo as e:
            raise BadRequest(str(e))
        ref = branch_target or r.default_branch
        if resolve_ref(r.path, ref) is None:
            raise BadRequest(f"branch '{ref}' does not exist in {r.name}")
    for c in validation_commands or []:
        try:
            safety.validate_command(c["command"])
        except (safety.UnsafeCommand, KeyError) as e:
            raise BadRequest(f"validation command rejected: {e}")
    group = uuid.uuid4().hex[:12] if len(repos) > 1 else None
    out = []
    for r in repos:
        t = Task(group_id=group, project_id=project.id, repo_id=r.id, title=title, description=description,
                 branch_target=branch_target, issue_ref=issue_ref, priority=priority, risk_level=risk_level,
                 acceptance_criteria=acceptance_criteria, validation_override=validation_commands,
                 max_retries=project.max_retries, created_by=user)
        db.add(t)
        db.flush()
        emit(db, t.id, "task_created", title, {"repo": r.path, "priority": priority, "risk_level": risk_level}, f"user:{user}")
        out.append(t)
    db.commit()
    return out


def request_cancel(db, task: Task, actor: str = "system") -> Task:
    """Cancel now if no worker holds the task; otherwise flag it and the worker stops at its next poll."""
    if task.parent_id is not None:
        raise BadRequest("cancel the top-level task")
    if task.status in states.TERMINAL:
        raise BadRequest(f"task is already {task.status}")
    try:
        with queue.task_lock(task.id):
            db.refresh(task)
            if task.status in states.TERMINAL:
                raise BadRequest(f"task is already {task.status}")
            emit(db, task.id, "cancel_requested", "", actor=actor)
            orchestrator.cancel_now(db, task, "cancelled by user", actor)
    except queue.TaskBusy:
        emit(db, task.id, "cancel_requested", "worker notified", actor=actor)
        task.cancel_requested = True
        db.commit()
    return task


def reset_for_retry(db, task: Task, actor: str = "system") -> Task:
    if task.parent_id is not None or task.status not in (states.FAILED, states.CANCELLED):
        raise BadRequest("only failed or cancelled top-level tasks can be retried")
    bw = db.query(BranchWorktree).filter_by(task_id=task.id).one_or_none()
    lost = bw is None or not bw.active or not Path(bw.path).exists()
    for c in orchestrator.children_of(db, task):
        if lost or c.status in (states.FAILED, states.CANCELLED):
            c.status, c.retry_count, c.error, c.finished_at = states.QUEUED, 0, None, None
    task.retry_count, task.error, task.dead_letter, task.cancel_requested = 0, None, False, False
    task.pending_prompt = task.pending_kind = None
    transition(db, task, states.QUEUED, "manual retry", actor)
    return task
