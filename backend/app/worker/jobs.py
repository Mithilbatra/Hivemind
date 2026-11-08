import logging

from sqlalchemy import event

from .. import states
from ..db import session_scope
from ..models import Task
from ..services import orchestrator
from ..services.audit import emit
from . import queue

log = logging.getLogger("hivemind.jobs")


def run_task_job(task_id: int) -> None:
    """Drive one top-level task as far as it can go. One worker per task via the task lock."""
    try:
        with queue.task_lock(task_id) as still_ours, session_scope() as db:
            # Every write first checks the lock is still ours: a worker frozen past the lock TTL must not write over
            # the worker that took the task over (it once moved a task to review under the other's feet).
            event.listen(db, "before_commit", lambda _db: still_ours())
            orchestrator.advance(db, task_id)
    except queue.TaskBusy:
        log.info("task %s is already being processed", task_id)
    except queue.LockLost as e:  # nothing was written after the loss; a fresh job resumes from the database
        log.warning("%s: stopped without writing", e)
        queue.enqueue_task(task_id)


def mark_dead(task_id: int, error: str) -> None:
    """Called when the job crashed past its retries: fail closed and park the task in the dead-letter list."""
    with session_scope() as db:
        t = db.get(Task, task_id)
        if t is None or t.status in states.TERMINAL:
            return
        orchestrator.close_stale_sessions(db, t.id, "worker crashed")
        t.error, t.dead_letter = error[:2000], True
        emit(db, t.id, "dead_letter", error[:500])
        orchestrator.transition(db, t, states.FAILED, "worker crashed: " + error[:200])
