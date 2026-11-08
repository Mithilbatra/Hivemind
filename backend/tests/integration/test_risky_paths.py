"""Recovery and failure paths that had no test: hold/run through the API, failed fix and failed step, the cancel-aware
backoff, a lost checkout, the runner's kill fallbacks and the session-log cap."""
import os
import shutil
import signal
import stat
import time
from pathlib import Path

from fastapi.testclient import TestClient

from app import states
from app.config import get_settings
from app.main import create_app
from app.models import AgentSession, BranchWorktree, OutputLog, Task
from app.services import orchestrator, repo_state
from app.services import runner as runner_mod
from app.services.runner import CLIRunner, MockRunner, RunRequest, RunResult, default_mock
from app.services.tasks import create_tasks
from app.worker import queue


def client():
    c = TestClient(create_app())
    c.__enter__()
    c.headers["Authorization"] = "Bearer " + c.post("/api/auth/login", json={"username": "admin", "password": "pw"}).json()["token"]
    return c


def bad_writer(req):
    if req.role == "writer":
        Path(req.cwd, "mock_feature.py").write_text("VALUE = 0\n")
        return RunResult("succeeded", 0, "wrote a wrong value")
    return default_mock(req)


# --- PATCH /status and POST /run --------------------------------------------------------------------------------

def test_hold_release_and_run_through_the_api(db, repo):
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]  # queued, not started
    c = client()
    assert c.patch(f"/api/tasks/{t.id}/status", json={"status": "blocked", "reason": "wait"}).json()["status"] == "blocked"
    r = c.patch(f"/api/tasks/{t.id}/status", json={"status": "blocked"})
    assert r.status_code == 409 and "cannot hold" in r.json()["detail"]
    assert c.post(f"/api/tasks/{t.id}/run").json()["status"] == "waiting_for_review"  # released from hold, then run
    r = c.post(f"/api/tasks/{t.id}/run")
    assert r.status_code == 409 and "cannot run a task that is waiting_for_review" in r.json()["detail"]
    child = db.query(Task).filter_by(parent_id=t.id).first()
    assert c.post(f"/api/tasks/{child.id}/run").json()["detail"] == "run the top-level task"


def test_run_refuses_a_task_waiting_for_pre_run_approval(db, repo):
    c = client()
    t = c.post("/api/tasks", json={"title": "Rotate the keys", "repo_ids": [repo.id], "risk_level": "high"}).json()[0]
    assert t["status"] == "blocked"
    r = c.post(f"/api/tasks/{t['id']}/run")
    assert r.status_code == 409 and "waiting for approval" in r.json()["detail"]


def test_cancel_and_retry_through_patch_status(db, repo):
    c = client()
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]
    assert c.patch(f"/api/tasks/{t.id}/status", json={"status": "cancelled"}).json()["status"] == "cancelled"
    runner_mod.set_runner(MockRunner(bad_writer))
    t2 = c.post("/api/tasks", json={"title": "Add feature", "repo_ids": [repo.id]}).json()[0]
    assert t2["status"] == "failed"  # the fix never lands
    runner_mod.set_runner(MockRunner(default_mock))
    assert c.patch(f"/api/tasks/{t2['id']}/status", json={"status": "queued"}).json()["status"] == "waiting_for_review"


def test_a_queue_outage_is_a_503_and_the_task_keeps_its_status(db, repo, monkeypatch):
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]
    c = client()

    def down(task_id):
        raise ConnectionError("redis unreachable")
    monkeypatch.setattr(queue, "enqueue_task", down)
    r = c.post(f"/api/tasks/{t.id}/run")
    assert r.status_code == 503 and "could not enqueue" in r.json()["detail"]
    db.refresh(t)
    assert t.status == states.QUEUED  # POST /run can enqueue it again later


# --- orchestrator failure paths ------------------------------------------------------------------------------------

def test_a_failed_fix_session_fails_the_task(db, repo):
    def fix_crashes(req):
        return RunResult("failed", 1, "crashed while fixing") if req.kind == "debug" else bad_writer(req)
    runner_mod.set_runner(MockRunner(fix_crashes))
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]
    queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.FAILED and t.error.startswith("fix session failed: crashed while fixing")


def test_a_failed_step_fails_the_task_also_when_resumed(db, repo):
    runner_mod.set_runner(MockRunner(lambda req: RunResult("failed", 1, "boom") if req.role == "writer" else default_mock(req)))
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]
    queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.FAILED and "subtask" in t.error and "failed" in t.error
    # A worker that died between failing the step and failing the task: resuming must not run past the failed step
    t.status, t.error, t.dead_letter = states.RUNNING, None, False
    db.commit()
    orchestrator.advance(db, t.id)
    db.refresh(t)
    assert t.status == states.FAILED and t.error.endswith("is failed")


def test_the_backoff_wait_stops_at_once_on_cancel(db, repo):
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]
    t.cancel_requested = True
    db.commit()
    t0 = time.time()
    orchestrator.sleep_interruptible(db, t, 30)
    assert time.time() - t0 < 2


# --- a checkout lost mid-task ---------------------------------------------------------------------------------------

def test_a_lost_checkout_is_rebuilt_on_its_own_branch(db, repo):
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]
    queue.enqueue_task(t.id)
    bw = db.query(BranchWorktree).filter_by(task_id=t.id).one()
    branch, base = bw.branch, bw.base_sha
    shutil.rmtree(bw.path)  # the volume lost the checkout
    repo_state.create_worktree(db, t, repo)
    head = repo_state.git(bw.path, "rev-parse", "--abbrev-ref", "HEAD").strip()
    assert (head, bw.branch, bw.base_sha, bw.active) == (branch, branch, base, True)


def test_a_directory_left_by_an_earlier_database_is_replaced(db, repo):
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]
    old = get_settings().worktrees_dir / f"task-{t.id}"  # ids restart at 1 with a new database
    old.mkdir(parents=True)
    (old / "stale").write_text("from a task of the old database")
    queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.REVIEW and not (old / "stale").exists()


# --- the runner's last-resort kills ---------------------------------------------------------------------------------

def stand_in(tmp_path, monkeypatch, body):
    script = tmp_path / "agent.sh"
    script.write_text(body)
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    monkeypatch.setenv("AGENT_COMMAND", str(script))
    monkeypatch.setenv("AGENT_TOOL_FLAGS", "false")
    get_settings.cache_clear()


def cancel_once_ready(tmp_path):
    """A request that cancels as soon as the stand-in agent prints 'ready' (its traps and children are set up)."""
    seen = []
    return RunRequest("p", str(tmp_path), "writer", "task", 60, {}, lambda: "ready" in seen, lambda s, line: seen.append(line))


def test_an_agent_that_ignores_sigterm_is_killed(tmp_path, monkeypatch, env):
    stand_in(tmp_path, monkeypatch, "#!/bin/sh\ntrap '' TERM\ncat > /dev/null\necho ready\nsleep 30\n")
    t0 = time.time()
    res = CLIRunner().run(cancel_once_ready(tmp_path))
    assert res.status == "cancelled" and 4 < time.time() - t0 < 12  # SIGTERM, 5 s grace, then SIGKILL


def test_a_detached_process_holding_the_pipes_does_not_hang_the_runner(tmp_path, monkeypatch, env):
    pidfile = tmp_path / "pid"
    stand_in(tmp_path, monkeypatch, f"""#!/bin/sh
cat > /dev/null
python3 -c "import os, time; os.setsid(); open('{pidfile}', 'w').write(str(os.getpid())); print('ready', flush=True); time.sleep(60)" &
sleep 30
""")
    t0 = time.time()
    try:
        res = CLIRunner().run(cancel_once_ready(tmp_path))
        assert res.status == "cancelled" and 9 < time.time() - t0 < 20  # gives up on the pipes 10 s after the kill
    finally:
        if pidfile.exists():
            os.kill(int(pidfile.read_text()), signal.SIGKILL)


# --- the session-log cap --------------------------------------------------------------------------------------------

def test_session_log_is_capped_in_the_database_but_whole_on_disk(db, repo, monkeypatch):
    monkeypatch.setenv("MAX_LOG_CHARS", "200")
    get_settings.cache_clear()

    def chatty(req):
        for i in range(40):
            req.on_output("stdout", f"line {i:02d} of a long agent log")
        return default_mock(req)
    runner_mod.set_runner(MockRunner(chatty))
    t = create_tasks(db, title="Add feature", repo_ids=[repo.id])[0]
    queue.enqueue_task(t.id)
    s = db.query(Task).filter_by(parent_id=t.id, role="writer").one()
    sess = db.query(AgentSession).filter_by(task_id=s.id).one()
    rows = [r.chunk for r in db.query(OutputLog).filter_by(session_id=sess.id).order_by(OutputLog.seq)]
    assert rows[-1].startswith("[log truncated here") and "line 39" not in "".join(rows)
    full = client().get(f"/api/tasks/{t.id}/sessions/{sess.id}/log").text
    assert "line 00" in full and "line 39" in full
