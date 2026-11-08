import threading
import uuid
from contextlib import contextmanager

import redis
import rq
from rq import Queue, Retry

from ..config import get_settings

QUEUE_NAME = "hivemind"
# Short TTL kept alive by a heartbeat: a worker that dies frees its task within a minute.
LOCK_TTL_S = 60
JOB_TIMEOUT_S = 12 * 3600  # sessions, checks and git calls have their own, shorter timeouts

_conn = None


class TaskBusy(RuntimeError):
    pass


class LockLost(RuntimeError):
    """The lock lapsed or passed to another worker while this one ran (it froze, e.g. a sleeping laptop pauses Docker)."""


def get_redis():
    global _conn
    if _conn is None:
        _conn = redis.Redis.from_url(get_settings().redis_url)
    return _conn


def set_redis(conn) -> None:
    """Tests inject fakeredis."""
    global _conn
    _conn = conn


def get_queue() -> Queue:
    return Queue(QUEUE_NAME, connection=get_redis())


class Worker(rq.Worker):
    """rq's defaults find a job whose worker died after 10-15 minutes (abandoned-job sweep every 10 min, and an idle
    worker only gets to it between 405 s waits for work). These find it within about two minutes, and RQ retries it."""

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("maintenance_interval", 30)  # the abandoned-job sweep
        kwargs.setdefault("worker_ttl", 45)  # waits for work 45 - 15 = 30 s at a time, so the sweep above runs on time
        kwargs.setdefault("job_monitoring_interval", 10)  # a dead job's registry entry expires 10 + 60 s after its last beat
        super().__init__(*args, **kwargs)


def lock_key(task_id: int) -> str:
    return f"hivemind:lock:{task_id}"


@contextmanager
def task_lock(task_id: int):
    """Exclusive right to change a task, shared by workers and API calls. Raises TaskBusy if someone holds it.
    Yields check(), which raises LockLost once the lock is no longer this holder's."""
    conn, key, token = get_redis(), lock_key(task_id), uuid.uuid4().hex
    if not conn.set(key, token, nx=True, ex=LOCK_TTL_S):
        raise TaskBusy(f"task {task_id} is being processed")
    stop = threading.Event()

    def check():
        if conn.get(key) != token.encode():
            raise LockLost(f"task {task_id}: the lock lapsed or passed to another worker")

    def heartbeat():
        while not stop.wait(LOCK_TTL_S / 3):
            try:
                if conn.get(key) != token.encode():
                    return  # lapsed or taken over: never take it back, another worker may have run the task since
                conn.expire(key, LOCK_TTL_S)
            except Exception:  # a Redis blip must not end the refresher (the lock would lapse for nothing)
                continue

    threading.Thread(target=heartbeat, daemon=True).start()
    try:
        yield check
    finally:
        stop.set()
        # shortcut: get-then-delete is not atomic; harmless while the heartbeat keeps the TTL far from expiry.
        if conn.get(key) == token.encode():
            conn.delete(key)


def enqueue_task(task_id: int) -> None:
    from .jobs import mark_dead, run_task_job

    if get_settings().queue_inline:
        try:
            run_task_job(task_id)
        except Exception as e:
            mark_dead(task_id, str(e))
            raise
        return
    # No retry interval: delayed retries need an RQ scheduler, and `rq worker-pool` never starts one, so they
    # sat in the scheduled registry forever. advance() resumes from the last finished step, so retrying at once is fine.
    get_queue().enqueue(run_task_job, task_id, job_timeout=JOB_TIMEOUT_S, retry=Retry(max=2),
                        on_failure=_on_failure, failure_ttl=7 * 86400, result_ttl=3600)


def _on_failure(job, connection, type, value, traceback):
    # RQ calls this on every failed attempt, before it schedules a retry. Only the last attempt dead-letters.
    if job.retries_left:
        return
    from .jobs import mark_dead

    mark_dead(job.args[0], f"{type.__name__}: {value}")
