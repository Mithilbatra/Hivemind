import logging

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
        with queue.task_lock(task_id), session_scope() as db:
            orchestrator.advance(db, task_id)
    except queue.TaskBusy:
        log.info("task %s is already being processed", task_id)


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
