"""Agent runners: CLIRunner shells out to AGENT_COMMAND; MockRunner is scripted for tests and demos."""
import json
import os
import queue
import shlex
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

from ..config import get_settings
from .safety import child_env


@dataclass
class RunRequest:
    prompt: str
    cwd: str
    role: str
    kind: str  # plan|task|debug|feedback
    timeout_s: int
    tool_policy: dict
    should_cancel: Callable[[], bool]
    on_output: Callable[[str, str], None]


@dataclass
class RunResult:
    status: str  # succeeded|failed|timeout|cancelled
    exit_code: int | None = None
    summary: str = ""
    text: str = ""
    tool_calls: list = field(default_factory=list)


def _kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGTERM)
        proc.wait(timeout=5)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


class CLIRunner:
    """Runs the agent command with the prompt on stdin; understands stream-json output."""

    def run(self, req: RunRequest) -> RunResult:
        s = get_settings()
        argv = shlex.split(s.agent_command)
        if not argv:
            req.on_output("stderr", "AGENT_COMMAND is not set")
            return RunResult("failed", summary="AGENT_COMMAND is not set")
        if s.agent_tool_flags:
            if req.tool_policy.get("allowed"):
                argv += ["--allowedTools", ",".join(req.tool_policy["allowed"])]
            if req.tool_policy.get("disallowed"):
                argv += ["--disallowedTools", ",".join(req.tool_policy["disallowed"])]
        try:
            # errors="replace": one invalid UTF-8 byte used to kill a reader thread and hang this loop forever
            proc = subprocess.Popen(argv, cwd=req.cwd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                    text=True, errors="replace", env=child_env(), start_new_session=True)
        except OSError as e:
            req.on_output("stderr", f"cannot start agent command {argv[0]!r}: {e}")
            return RunResult("failed", summary=f"cannot start agent: {e}")
        try:
            return self._watch(req, proc)
        finally:
            if proc.poll() is None:  # we are leaving because of an error: never leave the agent running
                _kill(proc)

    def _watch(self, req: RunRequest, proc) -> RunResult:
        q: queue.Queue = queue.Queue(maxsize=10_000)  # bounded: a flood of output waits for us instead of filling memory

        def pump(stream, name):
            for line in stream:
                q.put((name, line.rstrip("\n")))
            q.put((name, None))

        for st, name in ((proc.stdout, "stdout"), (proc.stderr, "stderr")):
            threading.Thread(target=pump, args=(st, name), daemon=True).start()
        try:
            proc.stdin.write(req.prompt)
            proc.stdin.close()
        except BrokenPipeError:
            pass

        tools, final, texts, tail, kept = [], "", [], [], 0
        deadline, open_streams, status, stopped = time.time() + req.timeout_s, 2, None, 0.0
        while open_streams or proc.poll() is None:
            if not stopped and req.should_cancel():
                status, stopped = "cancelled", time.time()
                _kill(proc)
            elif not stopped and time.time() > deadline:  # also after an error result: a hung agent still times out
                status, stopped = status or "timeout", time.time()
                _kill(proc)
            elif stopped and time.time() > stopped + 10:  # killed, but something it spawned still holds the pipes
                break
            try:
                name, line = q.get(timeout=0.5)
            except queue.Empty:
                if proc.poll() is not None and not open_streams:
                    break
                continue
            if line is None:
                open_streams -= 1
                continue
            req.on_output(name, line)
            if name == "stdout":
                tail = (tail + [line])[-20:]
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    ev = None
                said = []
                if not isinstance(ev, dict):  # plain text, or JSON that is not an event (`42`, `true`, a list)
                    said = [line]
                elif ev.get("type") == "assistant":
                    for c in (ev.get("message") or {}).get("content") or []:
                        if not isinstance(c, dict):
                            continue
                        if c.get("type") == "tool_use" and len(tools) < 200:
                            tools.append({"name": c.get("name"), "input": str(c.get("input"))[:300]})
                        elif c.get("type") == "text":
                            said.append(str(c.get("text") or ""))
                elif ev.get("type") == "result":
                    final = str(ev.get("result") or "")
                    if ev.get("is_error") and status is None:
                        status = "failed"
                for t in said:  # only the first 50k chars are ever used, so stop collecting there
                    if kept < 50_000:
                        texts.append(t)
                        kept += len(t)
        code = proc.wait()
        text = final or "\n".join(texts) or "\n".join(tail)
        if status is None:
            status = "succeeded" if code == 0 else "failed"
        # The summary is the end of what the agent said; past the 50k cap only the raw tail still has the end.
        summary = (final or ("\n".join(texts) if kept < 50_000 else "\n".join(tail)))[-2000:]
        return RunResult(status, code, summary, text[:50_000], tools)


MOCK_TEST = """import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from mock_feature import VALUE  # noqa: E402


class TestValue(unittest.TestCase):
    def test_value(self):
        self.assertEqual(VALUE, 1)
"""


def default_mock(req: RunRequest) -> RunResult:
    """Deterministic fake agent. With MOCK_FAIL_FIRST the first writer attempt is wrong until it sees a debug prompt."""
    cwd = Path(req.cwd)
    s = get_settings()
    tools = []

    def write(rel, content):
        p = cwd / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        tools.append({"name": "Write", "input": rel})
        req.on_output("stdout", f"wrote {rel}")

    # MOCK_STEP_S > 0 makes each mock session take a few seconds, so the hub has live work to show.
    title = next((line[9:] for line in req.prompt.splitlines() if line.startswith("## Task: ")), "the task")
    for step in (f"Reading the repository for: {title[:80]}", "Thinking about the change"):
        if req.should_cancel():
            return RunResult("cancelled", None, "cancelled")
        req.on_output("stdout", step)
        time.sleep(s.mock_step_s)
    if req.kind == "plan":
        req.on_output("stdout", "mock planner: no custom plan, deferring to the default template")
        return RunResult("succeeded", 0, "no plan")
    if req.role == "writer":
        good = req.kind in ("debug", "feedback") or not s.mock_fail_first
        write("mock_feature.py", f"VALUE = {1 if good else 0}\n")
    elif req.role == "tester":
        write("tests/test_mock_feature.py", MOCK_TEST)
    else:
        req.on_output("stdout", f"mock {req.role}: inspected the repository, nothing to change")
    return RunResult("succeeded", 0, f"mock {req.role} finished ({req.kind})", tool_calls=tools)


class MockRunner:
    def __init__(self, handler: Callable[[RunRequest], RunResult] | None = None):
        self.handler = handler or default_mock
        self.calls: list[RunRequest] = []

    def run(self, req: RunRequest) -> RunResult:
        self.calls.append(req)
        return self.handler(req)


_override = None


def set_runner(r) -> None:
    global _override
    _override = r


def get_runner():
    if _override is not None:
        return _override
    return MockRunner() if get_settings().agent_mode == "mock" else CLIRunner()
