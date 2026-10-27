"""One test per audit finding; each reproduces the original failure and asserts the fixed behaviour."""
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import fakeredis
import pytest
from fastapi.testclient import TestClient

from app import states
from app.config import get_settings
from app.main import create_app
from app.models import AgentSession, Approval, BranchWorktree, Prompt, Task
from app.services import context, planner, prompts, repo_state, review, sessions, validation
from app.services import runner as runner_mod
from app.services.runner import CLIRunner, MockRunner, RunRequest, RunResult, default_mock
from app.services.tasks import BadRequest, create_tasks, register_repo
from app.worker import queue

GIT = {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t",
       "PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/")}


def sh(*a, cwd):
    return subprocess.run(a, cwd=cwd, check=True, capture_output=True, text=True, env=GIT).stdout


def submit(db, repo, **kw):
    t = create_tasks(db, title=kw.pop("title", "Add feature"), repo_ids=[repo.id], **kw)[0]
    queue.enqueue_task(t.id)
    db.refresh(t)
    return t


def agent_script(tmp_path, monkeypatch, body: str, timeout: int | None = None) -> Path:
    script = tmp_path / "agent.sh"
    script.write_text("#!/bin/sh\n" + body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("AGENT_COMMAND", str(script))
    monkeypatch.setenv("AGENT_TOOL_FLAGS", "false")
    if timeout:
        monkeypatch.setenv("SESSION_TIMEOUT_S", str(timeout))
    get_settings.cache_clear()
    return script


def run_cli(tmp_path, timeout=30, on_output=lambda s, t: None):
    return CLIRunner().run(RunRequest("p", str(tmp_path), "writer", "task", timeout, {}, lambda: False, on_output))


def client():
    c = TestClient(create_app())
    c.__enter__()
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "pw"}).json()["token"]
    c.headers["Authorization"] = f"Bearer {tok}"
    return c


# --- 2: approving never runs repo hooks; commit problems are a clear 409 -------------------------------------------

def test_commit_never_runs_hooks_the_agent_can_edit(db, repo, tmp_path):
    sh("git", "config", "core.hooksPath", ".husky", cwd=repo.path)
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    bw = repo_state.create_worktree(db, t, repo)
    marker = tmp_path / "HOOK_RAN"
    hook = Path(bw.path) / ".husky" / "pre-commit"
    hook.parent.mkdir()
    hook.write_text(f"#!/bin/sh\ntouch {marker}\nexit 1\n")
    hook.chmod(0o755)
    repo_state.commit(db, t, bw, "msg")  # a failing hook would raise here if it ran
    assert not marker.exists()


def test_approve_problems_are_409_with_a_reason(db, repo):
    t = submit(db, repo)
    bw = db.query(BranchWorktree).filter_by(task_id=t.id).one()
    gitdir = Path(sh("git", "rev-parse", "--git-dir", cwd=bw.path).strip())
    (gitdir if gitdir.is_absolute() else Path(bw.path) / gitdir).joinpath("index.lock").write_text("")
    c = client()
    r = c.post(f"/api/tasks/{t.id}/approval", json={"action": "approve"})
    assert r.status_code == 409 and "index.lock" in r.json()["detail"]
    db.refresh(t)
    assert t.status == states.REVIEW  # nothing half-applied
    shutil.rmtree(bw.path)
    r = c.post(f"/api/tasks/{t.id}/approval", json={"action": "approve"})
    assert r.status_code == 409 and "checkout is gone" in r.json()["detail"]


# --- 3: symlinks can't pull outside files into prompts --------------------------------------------------------------

def test_symlinks_in_repo_never_reach_the_prompt(db, project, make_repo, tmp_path):
    secret = tmp_path / "outside_secret.txt"
    secret.write_text("TOP-SECRET-TOKEN\n")
    p = make_repo("evil")
    (p / "README.md").unlink()
    (p / "README.md").symlink_to(secret)
    (p / "notes_latest.md").symlink_to(secret)
    sh("git", "add", "-A", cwd=p)
    sh("git", "commit", "-qm", "links", cwd=p)
    repo = register_repo(db, str(p), project)
    ctx = context.build_context(repo.path, repo_state.head_sha(repo.path))
    assert "TOP-SECRET" not in context.render(ctx)
    picked = prompts.select_relevant_files(repo.path, ctx["tree"], "update the latest notes")
    assert not any("TOP-SECRET" in c for _, c in picked)


# --- 4: odd agent output never crashes, hangs or orphans ------------------------------------------------------------

def test_runner_survives_odd_output(env, tmp_path, monkeypatch):
    agent_script(tmp_path, monkeypatch, "cat >/dev/null\necho 42\necho '[1,2]'\necho '{\"type\":\"assistant\",\"message\":null}'\n"
                 "printf 'bad \\377 byte\\n'\necho '{\"type\":\"result\",\"result\":\"ok\"}'\n")
    res = run_cli(tmp_path)
    assert res.status == "succeeded" and res.summary == "ok"


def test_runner_kills_the_agent_when_it_fails_itself(env, tmp_path, monkeypatch):
    pidfile = tmp_path / "pid"
    agent_script(tmp_path, monkeypatch, f"echo $$ > {pidfile}\ncat >/dev/null\necho hi\nsleep 30\n")

    def boom(stream, text):
        raise RuntimeError("database went away")

    with pytest.raises(RuntimeError):
        run_cli(tmp_path, on_output=boom)
    time.sleep(0.2)
    with pytest.raises(ProcessLookupError):
        os.kill(int(pidfile.read_text()), 0)


def test_error_result_then_hang_still_times_out(env, tmp_path, monkeypatch):
    agent_script(tmp_path, monkeypatch, "cat >/dev/null\necho '{\"type\":\"result\",\"is_error\":true,\"result\":\"x\"}'\nsleep 30\n")
    t0 = time.time()
    res = run_cli(tmp_path, timeout=3)  # margin so the error line is read before the deadline, even under load
    assert res.status == "failed" and time.time() - t0 < 20  # the old loop waited for the agent's full 30 s


# --- 5: a leftover branch from an earlier database is never adopted --------------------------------------------------

def test_leftover_branch_is_not_adopted(db, repo):
    sh("git", "branch", "hivemind/task-1", cwd=repo.path)
    leftover = Path(repo.path) / "old.txt"
    sh("git", "checkout", "-q", "hivemind/task-1", cwd=repo.path)
    leftover.write_text("old\n")
    sh("git", "add", "-A", cwd=repo.path)
    sh("git", "commit", "-qm", "old life", cwd=repo.path)
    sh("git", "checkout", "-q", "main", cwd=repo.path)
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    assert t.id == 1
    bw = repo_state.create_worktree(db, t, repo)
    assert bw.branch.startswith("hivemind/task-1-") and not (Path(bw.path) / "old.txt").exists()


# --- 6: the lock refresher survives a Redis error --------------------------------------------------------------------

def test_lock_survives_ttl_and_a_redis_error(env, monkeypatch):
    monkeypatch.setattr(queue, "LOCK_TTL_S", 2)
    base = fakeredis.FakeRedis()

    class Flaky:
        fail = False

        def __getattr__(self, name):
            f = getattr(base, name)
            if name != "get":
                return f

            def get(*a, **k):
                if Flaky.fail:
                    Flaky.fail = False
                    raise ConnectionError("redis restarting")
                return f(*a, **k)
            return get

    queue.set_redis(Flaky())
    with queue.task_lock(7):
        time.sleep(3)  # past one TTL: the heartbeat keeps it
        Flaky.fail = True
        time.sleep(4.5)  # two TTLs after the error
        assert base.exists(queue.lock_key(7))
    assert not base.exists(queue.lock_key(7))


# --- 7: runaway output is capped -------------------------------------------------------------------------------------

def test_check_output_keeps_only_the_tail(tmp_path):
    (tmp_path / "noisy.py").write_text("import sys\nfor i in range(200000): print('line', i)\nprint('THE END')\n")
    code, out = validation.run_cmd(["python3", "noisy.py"], str(tmp_path), 60)
    assert code == 0 and len(out) <= validation.TAIL and out.rstrip().endswith("THE END")


def test_session_log_file_is_capped(db, repo, monkeypatch):
    monkeypatch.setattr(sessions, "LOG_FILE_CAP", 2000)

    def chatty(req):
        for i in range(500):
            req.on_output("stdout", f"line {i:04d} " + "x" * 20)
        return default_mock(req)

    runner_mod.set_runner(MockRunner(chatty))
    t = submit(db, repo)
    log = Path(db.query(AgentSession).filter_by(top_task_id=t.id).first().log_path).read_text()
    assert len(log) < 2100 and log.count("[log file size limit reached]") == 1


# --- 8: a check that can't run fails at once instead of burning agent retries -------------------------------------

def test_unrunnable_check_fails_fast(db, repo, project):
    project.validation_commands = [{"kind": "test", "command": "go test ./...", "required": True}]
    db.commit()
    if shutil.which("go"):
        pytest.skip("needs a machine without Go")
    t = submit(db, repo)
    assert t.status == states.FAILED and "could not run" in t.error
    assert db.query(Prompt).filter_by(task_id=t.id, kind="debug").count() == 0


# --- 9: force commit clears the dead-letter flag ---------------------------------------------------------------------

def test_force_commit_leaves_dead_letter_list(db, repo):
    def bad(req):
        if req.role == "writer":
            (Path(req.cwd) / "mock_feature.py").write_text("VALUE = 0\n")
        return RunResult("succeeded", 0, "ok")

    runner_mod.set_runner(MockRunner(bad))
    t = submit(db, repo)
    assert t.dead_letter
    review.decide(db, t, "force_commit", "me", reason="known flaky", confirm=True)
    assert t.status == states.COMPLETED and not t.dead_letter


# --- 10: context describes the task's base commit --------------------------------------------------------------------

def test_context_follows_the_task_base_not_the_checkout(db, repo):
    sh("git", "checkout", "-q", "-b", "develop", cwd=repo.path)
    (Path(repo.path) / "only_on_develop.py").write_text("x=1\n")
    sh("git", "add", "-A", cwd=repo.path)
    sh("git", "commit", "-qm", "dev", cwd=repo.path)
    (Path(repo.path) / "README.md").write_text("UNCOMMITTED EDIT\n")
    t = submit(db, repo, branch_target="main")
    text = db.query(Prompt).filter_by(task_id=t.id, kind="plan").one().text
    assert "only_on_develop.py" not in text and "UNCOMMITTED EDIT" not in text


# --- 11: Host check, login limit, worker list needs login ------------------------------------------------------------

def test_foreign_host_and_login_limits(env):
    with TestClient(create_app()) as c:
        assert c.get("/api/health", headers={"Host": "evil.example"}).status_code == 400
        assert c.get("/api/health/workers").status_code == 401
        for _ in range(10):
            assert c.post("/api/auth/login", json={"username": "admin", "password": "nope"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "admin", "password": "pw"}).status_code == 429


# --- 14: open tasks never drop off the Hive --------------------------------------------------------------------------

def test_hub_keeps_every_open_task(db, repo, monkeypatch):
    from app.api import hub
    monkeypatch.setattr(hub, "TASK_LIMIT", 1)
    blocked = submit(db, repo, risk_level="high")
    for i in range(3):
        t = create_tasks(db, title=f"done {i}", repo_ids=[repo.id])[0]
        t.status = states.COMPLETED
    db.commit()
    c = client()
    board = c.get("/api/hub").json()["projects"][0]
    assert blocked.id in [t["id"] for t in board["tasks"]] and board["counts"]["completed"] == 3


# --- 16: the full log is reachable -----------------------------------------------------------------------------------

def test_full_log_endpoint(db, repo, make_repo, project):
    t = submit(db, repo)
    s = db.query(AgentSession).filter_by(top_task_id=t.id).first()
    c = client()
    r = c.get(f"/api/tasks/{t.id}/sessions/{s.id}/log")
    assert r.status_code == 200 and r.text == Path(s.log_path).read_text()
    other = submit(db, register_repo(db, str(make_repo("other")), project))
    assert c.get(f"/api/tasks/{other.id}/sessions/{s.id}/log").status_code == 404
    s.log_path = "/etc/hosts"  # never serve anything outside DATA_DIR
    db.commit()
    assert c.get(f"/api/tasks/{t.id}/sessions/{s.id}/log").status_code == 404


# --- 18: non-ASCII file names stay readable --------------------------------------------------------------------------

def test_non_ascii_paths(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    bw = repo_state.create_worktree(db, t, repo)
    (Path(bw.path) / "café.txt").write_text("a\n")
    d = repo_state.capture_diff(db, t, bw)
    assert d.files[0]["path"] == "café.txt" and "b/café.txt" in d.patch.splitlines()[0]


def test_latin1_file_in_diff_does_not_crash(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    bw = repo_state.create_worktree(db, t, repo)
    (Path(bw.path) / "legacy.txt").write_bytes(b"caf\xe9\n")
    assert repo_state.capture_diff(db, t, bw).files[0]["path"] == "legacy.txt"


# --- 20: the agent setup is checked up front --------------------------------------------------------------------------

def test_cli_mode_without_command_is_refused(db, repo, monkeypatch):
    monkeypatch.setenv("AGENT_MODE", "cli")
    monkeypatch.setenv("AGENT_COMMAND", "")
    get_settings.cache_clear()
    with pytest.raises(BadRequest, match="AGENT_COMMAND"):
        create_tasks(db, title="x", repo_ids=[repo.id])
    monkeypatch.setenv("AGENT_MODE", "clii")
    get_settings.cache_clear()
    with pytest.raises(ValueError):
        get_settings()


# --- smaller ones -----------------------------------------------------------------------------------------------------

def test_planner_odd_kind_falls_back(env):
    steps = planner.parse_agent_plan('{"steps": [{"key": "a", "title": "A", "kind": ["x"]}]}')
    assert steps[0].kind == "implement"


def test_pre_run_reject_leaves_nothing_behind(db, repo):
    t = submit(db, repo, risk_level="critical")
    review.decide(db, t, "reject", "me", reason="no")
    assert t.status == states.CANCELLED
    assert db.query(Approval).filter_by(task_id=t.id, status="rejected").count() == 1
    assert db.query(Task).filter_by(parent_id=t.id).count() == 0
