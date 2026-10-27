"""Run configured checks (allowlisted, no shell) and store results."""
import contextlib
import os
import signal
import subprocess
import threading
import time
from collections import deque

from ..config import get_settings
from ..models import ValidationResult
from .audit import emit
from .context import suggest_validation
from .safety import UnsafeCommand, child_env, validate_command

TAIL = 8000
BLOCKED, CANNOT_RUN = "blocked by safety policy", "cannot run"


def unrunnable(r) -> bool:
    """The check never started (not allowlisted, or the executable is missing): no code change can fix that."""
    return r.exit_code is None and r.output_tail.startswith((BLOCKED, CANNOT_RUN))


def effective_checks(top, project, ctx: dict) -> list[dict]:
    if top.validation_override is not None:
        return top.validation_override
    return project.validation_commands or suggest_validation(ctx)


def run_cmd(argv: list[str], cwd: str, timeout: int) -> tuple[int | None, str]:
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             errors="replace", env=child_env(), start_new_session=True)
    except OSError as e:
        return None, f"{CANNOT_RUN} {argv[0]}: {e}"
    tail: deque[str] = deque(maxlen=TAIL)  # only the end is kept: a test stuck printing can't fill memory

    def read():
        for chunk in iter(lambda: p.stdout.read(65536), ""):
            tail.extend(chunk)

    reader = threading.Thread(target=read, daemon=True)
    reader.start()
    try:
        code = p.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):  # it may exit on its own between the timeout and the kill
            os.killpg(p.pid, signal.SIGKILL)
        p.wait()
        code = None
    reader.join(timeout=5)
    out = "".join(tail)
    return code, out if code is not None else out + f"\n[timed out after {timeout}s]"


def run_validation(db, top, bw, checks: list[dict], attempt: int) -> list[ValidationResult]:
    results = []
    for c in checks:
        cmd, kind, required = c["command"], c.get("kind", "test"), c.get("required", True)
        t0 = time.time()
        try:
            argv = validate_command(cmd)
        except UnsafeCommand as e:
            code, out = None, f"{BLOCKED}: {e}"
            emit(db, top.id, "command_blocked", cmd, {"reason": str(e)})
        else:
            emit(db, top.id, "command", cmd, {"cwd": bw.path, "kind": kind})
            # `or`, not a .get default: checks saved through the API carry "timeout": null
            code, out = run_cmd(argv, bw.path, c.get("timeout") or get_settings().validation_timeout_s)
        r = ValidationResult(task_id=top.id, attempt=attempt, kind=kind, command=cmd, required=required,
                             passed=code == 0, exit_code=code, duration_s=round(time.time() - t0, 2),
                             output_tail=out[-TAIL:])
        db.add(r)
        results.append(r)
        emit(db, top.id, "validation_result", f"{kind}: {'pass' if r.passed else 'FAIL'} ({cmd})",
             {"attempt": attempt, "exit_code": code})
    db.commit()
    return results
