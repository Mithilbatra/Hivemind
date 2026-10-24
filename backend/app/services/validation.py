"""Run configured checks (allowlisted, no shell) and store results."""
import os
import signal
import subprocess
import time

from ..config import get_settings
from ..models import ValidationResult
from .audit import emit
from .context import suggest_validation
from .safety import UnsafeCommand, child_env, validate_command

TAIL = 8000


def effective_checks(top, project, ctx: dict) -> list[dict]:
    if top.validation_override is not None:
        return top.validation_override
    return project.validation_commands or suggest_validation(ctx)


def run_cmd(argv: list[str], cwd: str, timeout: int) -> tuple[int | None, str]:
    try:
        p = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                             env=child_env(), start_new_session=True)
    except OSError as e:
        return None, f"cannot run {argv[0]}: {e}"
    try:
        out, _ = p.communicate(timeout=timeout)
        return p.returncode, out
    except subprocess.TimeoutExpired:
        os.killpg(p.pid, signal.SIGKILL)
        out, _ = p.communicate()
        return None, (out or "") + f"\n[timed out after {timeout}s]"


def run_validation(db, top, bw, checks: list[dict], attempt: int) -> list[ValidationResult]:
    results = []
    for c in checks:
        cmd, kind, required = c["command"], c.get("kind", "test"), c.get("required", True)
        t0 = time.time()
        try:
            argv = validate_command(cmd)
        except UnsafeCommand as e:
            code, out = None, f"blocked by safety policy: {e}"
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
