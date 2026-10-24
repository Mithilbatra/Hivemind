"""Human approval decisions."""
from datetime import UTC, datetime
from pathlib import Path

from .. import states
from ..models import Approval, BranchWorktree, Repository, Task
from ..states import transition
from . import orchestrator, repo_state
from .audit import emit
from .tasks import BadRequest

ACTIONS = ("approve", "reject", "request_changes", "force_commit")


def _pending(db, task, kind):
    return db.query(Approval).filter_by(task_id=task.id, kind=kind, status="pending").order_by(Approval.id.desc()).first()


def _decide(a: Approval, status: str, user: str, reason: str):
    a.status, a.decided_by, a.reason, a.decided_at = status, user, reason, datetime.now(UTC)


def decide(db, task: Task, action: str, user: str, reason: str = "", confirm: bool = False) -> bool:
    """Apply a decision. Returns True if the task should be handed back to a worker."""
    if action not in ACTIONS:
        raise BadRequest(f"action must be one of {ACTIONS}")
    if task.parent_id is not None:
        raise BadRequest("decide on the top-level task")
    actor = f"user:{user}"
    if task.status == states.BLOCKED:
        a = _pending(db, task, "pre_run")
        if a is None or action not in ("approve", "reject"):
            raise BadRequest("a blocked task accepts approve or reject")
        _decide(a, "approved" if action == "approve" else "rejected", user, reason)
        emit(db, task.id, "approval_decision", f"pre_run {action}", {"reason": reason}, actor)
        if action == "reject":
            orchestrator.cancel_now(db, task, f"pre-run approval rejected: {reason}", actor)
            return False
        transition(db, task, states.ASSIGNED, "pre-run approved", actor)
        return True
    if action == "force_commit":
        if task.status not in (states.FAILED, states.REVIEW):
            raise BadRequest("force_commit applies to failed or in-review tasks")
        if not (confirm and reason.strip()):
            raise BadRequest("force_commit needs confirm=true and a reason")
        bw = db.query(BranchWorktree).filter_by(task_id=task.id).one_or_none()
        if bw is None or not bw.active or not Path(bw.path).exists():
            raise BadRequest("no worktree to commit")
        a = _pending(db, task, "pre_commit") or Approval(task_id=task.id, kind="pre_commit")
        db.add(a)
        _decide(a, "forced", user, reason)
        emit(db, task.id, "force_commit", reason, {"validation_bypassed": task.status == states.FAILED}, actor)
        _finish(db, task, bw, actor)
        return False
    if task.status != states.REVIEW:
        raise BadRequest(f"task is {task.status}, not waiting_for_review")
    a = _pending(db, task, "pre_commit")
    if a is None:
        raise BadRequest("no pending approval")
    if action == "approve":
        _decide(a, "approved", user, reason)
        emit(db, task.id, "approval_decision", "approved", {"reason": reason}, actor)
        _finish(db, task, orchestrator.worktree_of(db, task), actor)
        return False
    if action == "reject":
        _decide(a, "rejected", user, reason)
        emit(db, task.id, "approval_decision", "rejected", {"reason": reason}, actor)
        orchestrator.cancel_now(db, task, f"rejected: {reason}", actor)
        return False
    if not reason.strip():
        raise BadRequest("request_changes needs feedback in reason")
    _decide(a, "changes_requested", user, reason)
    task.pending_kind, task.pending_prompt, task.retry_count = "feedback", reason, 0
    emit(db, task.id, "approval_decision", "changes requested", {"reason": reason}, actor)
    transition(db, task, states.RUNNING, "changes requested", actor)
    return True


def _finish(db, task, bw, actor):
    repo_state.commit(db, task, bw, f"{task.title}\n\nTask #{task.id} (orchestrator branch {bw.branch})")
    repo_state.release_worktree(db, bw, db.get(Repository, task.repo_id), delete_branch=False)
    task.error = None
    transition(db, task, states.COMPLETED, "committed after approval", actor)
