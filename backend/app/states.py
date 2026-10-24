from datetime import UTC, datetime

from .services.audit import emit

QUEUED, PLANNING, ASSIGNED, RUNNING, VALIDATION = "queued", "planning", "assigned", "running", "validation"
BLOCKED, REVIEW, COMPLETED, FAILED, CANCELLED = "blocked", "waiting_for_review", "completed", "failed", "cancelled"

ALL = [QUEUED, PLANNING, ASSIGNED, RUNNING, VALIDATION, BLOCKED, REVIEW, COMPLETED, FAILED, CANCELLED]
TERMINAL = {COMPLETED, FAILED, CANCELLED}

ALLOWED: dict[str, set[str]] = {
    QUEUED: {PLANNING, BLOCKED, CANCELLED, FAILED},
    PLANNING: {ASSIGNED, BLOCKED, CANCELLED, FAILED},
    ASSIGNED: {RUNNING, BLOCKED, CANCELLED, FAILED},
    RUNNING: {VALIDATION, BLOCKED, CANCELLED, FAILED},
    VALIDATION: {RUNNING, REVIEW, CANCELLED, FAILED},
    BLOCKED: {ASSIGNED, QUEUED, CANCELLED, FAILED},
    REVIEW: {COMPLETED, RUNNING, CANCELLED, FAILED},
    FAILED: {QUEUED, COMPLETED},  # retry, or force_commit
    CANCELLED: {QUEUED},
    COMPLETED: set(),
}


class InvalidTransition(Exception):
    pass


def transition(db, task, new: str, reason: str = "", actor: str = "system") -> None:
    """The only place a task's status changes; writes an audit event."""
    old = task.status
    ok = new in ALLOWED.get(old, set())
    # Subtasks have no planning/validation/review stage of their own.
    if task.parent_id is not None and (old, new) in {(QUEUED, ASSIGNED), (RUNNING, COMPLETED)}:
        ok = True
    if not ok:
        raise InvalidTransition(f"{old} -> {new} is not allowed")
    now = datetime.now(UTC)
    task.status = new
    if new == RUNNING and task.started_at is None:
        task.started_at = now
    if new in TERMINAL:
        task.finished_at = now
    elif old in TERMINAL:
        task.finished_at = None
    emit(db, task.id, "status_changed", reason or f"{old} -> {new}", {"from": old, "to": new}, actor)
    db.commit()
