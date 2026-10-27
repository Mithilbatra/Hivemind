"""One agent session: prompt record, log capture, policy checks, diff capture."""
import time
from datetime import UTC, datetime
from fnmatch import fnmatch
from posixpath import basename

from ..config import get_settings
from ..models import AgentSession, OutputLog, Prompt
from . import repo_state
from .audit import emit
from .runner import RunRequest, get_runner

LOG_FILE_CAP = 50_000_000  # chars per session log file on disk


def policy_violation(agent, changed: list[str]) -> str | None:
    if not changed:
        return None
    if not agent.can_write:
        return f"read-only role '{agent.role}' modified files: {changed[:5]}"
    if agent.writable_globs:
        bad = [p for p in changed
               if not any(fnmatch(p if "/" in g else basename(p), g) for g in agent.writable_globs)]
        if bad:
            return f"role '{agent.role}' modified files outside its allowed paths: {bad[:5]}"
    return None


def run_session(db, *, task, top, repo, bw, agent, prompt: str, kind: str, attempt: int = 1):
    """Run one agent. Returns (AgentSession, RunResult). Session status mirrors the result unless policy was violated."""
    s = get_settings()
    sess = AgentSession(task_id=task.id, top_task_id=top.id, repo_id=repo.id, branch=bw.branch, agent_role=agent.role,
                        attempt=attempt)
    db.add(sess)
    db.flush()
    log_file = s.data_dir / "logs" / f"session-{sess.id}.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)
    sess.log_path = str(log_file)
    db.add(Prompt(session_id=sess.id, task_id=task.id, kind=kind, text=prompt))
    emit(db, task.id, "session_started", f"{agent.role} ({kind}, attempt {attempt})", {"session_id": sess.id}, f"agent:{agent.role}")
    db.commit()

    before = repo_state.snapshot(bw.path)
    state = {"seq": 0, "kept": 0, "truncated": False, "polled": 0.0, "file": 0}
    fh = open(log_file, "a")

    def on_output(stream: str, text: str):
        if state["file"] < LOG_FILE_CAP:  # the file keeps far more than the DB, but not without limit
            entry = f"[{stream}] {text}\n"
            if state["file"] + len(entry) >= LOG_FILE_CAP:
                entry = "[log file size limit reached]\n"
                state["file"] = LOG_FILE_CAP
            fh.write(entry)
            state["file"] += len(entry)
        if state["kept"] < s.max_log_chars:
            db.add(OutputLog(session_id=sess.id, seq=state["seq"], stream=stream, chunk=text[:4000]))
            state["kept"] += len(text)
        elif not state["truncated"]:
            state["truncated"] = True
            db.add(OutputLog(session_id=sess.id, seq=state["seq"], stream="system",
                             chunk="[log truncated here; use 'Full log' for the rest]"))
        state["seq"] += 1

    def should_cancel() -> bool:
        # The runner calls this every line and every 0.5 s of silence. Once a second we publish buffered
        # log lines (so a quiet agent's output still shows up live) and re-read the cancel flag.
        if time.monotonic() - state["polled"] >= 1:
            state["polled"] = time.monotonic()
            db.commit()
            db.refresh(top)
        return top.cancel_requested

    req = RunRequest(prompt=prompt, cwd=bw.path, role=agent.role, kind=kind, timeout_s=s.session_timeout_s,
                     tool_policy=agent.tool_policy or {}, should_cancel=should_cancel, on_output=on_output)
    try:
        result = get_runner().run(req)
    finally:
        fh.close()

    changed = repo_state.changed_between(before, repo_state.snapshot(bw.path))
    violation = policy_violation(agent, changed)
    if violation:
        result.status = "failed"
        emit(db, task.id, "policy_violation", violation, {"files": changed}, f"agent:{agent.role}")
    diff = repo_state.capture_diff(db, top, bw, sess.id) if (changed or agent.can_write) else None
    sess.status = result.status
    sess.exit_code = result.exit_code
    sess.summary = result.summary
    sess.tool_calls = result.tool_calls
    sess.ended_at = datetime.now(UTC)
    sess.diff_summary = {"changed": changed, "additions": diff.additions if diff else 0,
                         "deletions": diff.deletions if diff else 0, "violation": violation}
    emit(db, task.id, "session_finished", f"{agent.role}: {result.status}",
         {"session_id": sess.id, "changed": changed, "tools": len(result.tool_calls)}, f"agent:{agent.role}")
    db.commit()
    return sess, result
