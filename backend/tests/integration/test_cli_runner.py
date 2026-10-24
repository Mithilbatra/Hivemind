import stat
import time

from app import states
from app.config import get_settings
from app.models import AgentSession
from app.services import runner as runner_mod
from app.services.runner import CLIRunner, RunRequest
from app.services.tasks import create_tasks
from app.worker import queue

AGENT = """#!/bin/sh
prompt=$(cat)
case "$AGENT_MODE_TEST" in
  hang) sleep 30;;
esac
case "$prompt" in
  *"implementation agent"*)
    echo '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Write","input":{"file_path":"mock_feature.py"}}]}}'
    echo 'VALUE = 1' > mock_feature.py;;
esac
echo '{"type":"result","result":"all done","is_error":false}'
"""


def setup_cli(tmp_path, monkeypatch, body=AGENT, timeout=None):
    script = tmp_path / "agent.sh"
    script.write_text(body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("AGENT_MODE", "cli")
    monkeypatch.setenv("AGENT_COMMAND", str(script))
    monkeypatch.setenv("AGENT_TOOL_FLAGS", "false")
    if timeout:
        monkeypatch.setenv("SESSION_TIMEOUT_S", str(timeout))
    get_settings.cache_clear()
    runner_mod.set_runner(None)


def test_cli_runner_end_to_end(db, repo, tmp_path, monkeypatch):
    setup_cli(tmp_path, monkeypatch)
    t = create_tasks(db, title="cli", repo_ids=[repo.id])[0]
    queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.REVIEW
    s = db.query(AgentSession).filter_by(top_task_id=t.id, agent_role="writer").one()
    assert s.summary == "all done" and s.tool_calls[0]["name"] == "Write"
    assert open(s.log_path).read().count("[stdout]") >= 2


def test_cli_runner_timeout_kills_process(tmp_path, monkeypatch, env):
    setup_cli(tmp_path, monkeypatch, timeout=1)
    monkeypatch.setenv("AGENT_MODE_TEST", "hang")
    out = []
    t0 = time.time()
    res = CLIRunner().run(RunRequest("p", str(tmp_path), "writer", "task", 1, {}, lambda: False, lambda s, t: out.append(t)))
    assert res.status == "timeout" and time.time() - t0 < 10


def test_cli_runner_cancel_and_missing_binary(tmp_path, monkeypatch, env):
    setup_cli(tmp_path, monkeypatch)
    monkeypatch.setenv("AGENT_MODE_TEST", "hang")
    res = CLIRunner().run(RunRequest("p", str(tmp_path), "writer", "task", 60, {}, lambda: True, lambda s, t: None))
    assert res.status == "cancelled"
    monkeypatch.setenv("AGENT_COMMAND", "/nonexistent/agent")
    get_settings.cache_clear()
    res = CLIRunner().run(RunRequest("p", str(tmp_path), "writer", "task", 5, {}, lambda: False, lambda s, t: None))
    assert res.status == "failed" and "cannot start" in res.summary


def test_tool_flags_appended(tmp_path, monkeypatch, env):
    argv_file = tmp_path / "argv.txt"
    setup_cli(tmp_path, monkeypatch, body=f'#!/bin/sh\ncat >/dev/null\necho "$@" > {argv_file}\n')
    monkeypatch.setenv("AGENT_TOOL_FLAGS", "true")
    get_settings.cache_clear()
    policy = {"allowed": ["Read", "Edit"], "disallowed": ["Bash(rm:*)"]}
    CLIRunner().run(RunRequest("p", str(tmp_path), "writer", "task", 5, policy, lambda: False, lambda s, t: None))
    assert argv_file.read_text().strip() == "--allowedTools Read,Edit --disallowedTools Bash(rm:*)"
