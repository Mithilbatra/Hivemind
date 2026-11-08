"""The worker container's command: `python -m app.worker -n 4` runs `rq worker-pool hivemind` in a child process and
stays PID 1 as a tiny init."""
import ctypes
import os
import signal
import sys

from rq.cli import main

from .. import preflight
from ..config import get_settings

PR_SET_DUMPABLE = 4


def harden() -> None:
    """Agent and check processes run as this same user. Non-dumpable, this process's /proc/<pid>/environ and memory
    are closed to them. rq forks its workers, scheduler and job processes, so they all inherit it; exec'd children
    (agent, checks, git) do not, and they get an environment without secrets anyway."""
    if sys.platform.startswith("linux") and ctypes.CDLL(None, use_errno=True).prctl(PR_SET_DUMPABLE, 0, 0, 0, 0):
        raise OSError(ctypes.get_errno(), "prctl(PR_SET_DUMPABLE, 0) failed")


def init(pool: int) -> None:
    """PID 1's other job: a process a check leaves behind is re-parented here when its parent exits, and rq never
    reaps it, so each one killed stayed a zombie. Reap everything; pass stop signals on to rq."""
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda n, _frame: os.kill(pool, n))
    while True:
        pid, status = os.wait()
        if pid == pool:
            sys.exit(max(0, os.waitstatus_to_exitcode(status)))


if __name__ == "__main__":
    preflight.check(admin=False)
    harden()  # before the fork, so both processes are covered
    pool = os.fork()
    if pool:
        init(pool)
    # The Redis URL carries its password: hand it to rq in-process, never on a command line others can read.
    main(["worker-pool", "hivemind", "-u", get_settings().redis_url, "-w", "app.worker.queue.Worker", *sys.argv[1:]],
         prog_name="rq")
