import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.services import runner as runner_mod
from app.services.runner import MockRunner, RunResult


@pytest.fixture
def client(env, repo):
    with TestClient(create_app()) as c:
        tok = c.post("/api/auth/login", json={"username": "admin", "password": "pw"}).json()["token"]
        c.headers["Authorization"] = f"Bearer {tok}"
        c.repo_id = repo.id
        yield c


def create(client, **kw):
    r = client.post("/api/tasks", json={"title": "Add OAuth login", "repo_ids": [client.repo_id], **kw})
    assert r.status_code == 201, r.text
    return r.json()[0]


def test_auth_required_and_login(env):
    with TestClient(create_app()) as c:
        assert c.get("/api/tasks").status_code == 401
        assert c.post("/api/auth/login", json={"username": "admin", "password": "bad"}).status_code == 401
        assert c.get("/api/tasks", headers={"Authorization": "Bearer junk"}).status_code == 401
        assert c.get("/api/health").json() == {"status": "ok"}


def test_create_list_detail(client):
    t = create(client, description="with tests", priority="high", acceptance_criteria="login works", issue_ref="#12")
    assert t["status"] == "waiting_for_review" and t["priority"] == "high" and t["issue_ref"] == "#12"
    assert [x["id"] for x in client.get("/api/tasks").json()] == [t["id"]]
    assert client.get("/api/tasks", params={"status": "failed"}).json() == []
    assert len(client.get("/api/tasks", params={"q": "oauth"}).json()) == 1
    d = client.get(f"/api/tasks/{t['id']}").json()
    assert d["repo_name"] == "app" and len(d["children"]) == 5 and d["worktree"]["branch"] == f"hivemind/task-{t['id']}"
    by_title = {c["title"]: c for c in d["children"]}
    assert by_title["Write tests"]["depends_on"] == [by_title["Implement change"]["id"]]
    assert d["approvals"][0]["kind"] == "pre_commit" and "sessions" not in d  # fetched separately, see below
    sessions = client.get("/api/sessions", params={"task_id": t["id"]}).json()
    assert sessions and sessions[0]["tool_calls"] is not None
    assert client.get("/api/tasks/9999").status_code == 404


def test_create_validation(client, env):
    assert client.post("/api/tasks", json={"title": "x"}).status_code == 400  # no repo
    assert client.post("/api/tasks", json={"title": "x", "repo_ids": [client.repo_id], "priority": "meh"}).status_code == 400
    assert client.post("/api/tasks", json={"title": "", "repo_ids": [client.repo_id]}).status_code == 422
    assert client.post("/api/tasks", json={"title": "x", "repo_paths": ["/etc"]}).status_code == 400
    bad = {"title": "x", "repo_ids": [client.repo_id], "validation_commands": [{"command": "rm -rf /"}]}
    assert client.post("/api/tasks", json=bad).status_code == 400


def test_multi_repo_creates_grouped_tasks(client, make_repo):
    other = make_repo("second")
    r = client.post("/api/tasks", json={"title": "both", "repo_ids": [client.repo_id], "repo_paths": [str(other)]})
    ts = r.json()
    assert len(ts) == 2 and ts[0]["group_id"] == ts[1]["group_id"] and ts[0]["repo_id"] != ts[1]["repo_id"]


def test_logs_diff_validation_events(client):
    t = create(client)
    logs = client.get(f"/api/tasks/{t['id']}/logs").json()
    assert any("wrote mock_feature.py" in line["text"] for line in logs["lines"])
    assert logs["sessions"] and logs["next_offset"] == len(logs["lines"])
    paged = client.get(f"/api/tasks/{t['id']}/logs", params={"offset": 1, "limit": 1}).json()
    assert len(paged["lines"]) == 1 and paged["lines"][0] == logs["lines"][1]
    diff = client.get(f"/api/tasks/{t['id']}/diff").json()
    assert "mock_feature.py" in diff["patch"] and diff["additions"] >= 1
    val = client.get(f"/api/tasks/{t['id']}/validation").json()
    assert val[0]["passed"] and val[0]["command"] == "python3 check.py"
    ev = client.get(f"/api/tasks/{t['id']}/events").json()
    assert ev[0]["type"] == "task_created" and "committed" not in {e["type"] for e in ev}


def test_approval_flow(client):
    t = create(client)
    assert client.post(f"/api/tasks/{t['id']}/approval", json={"action": "bogus"}).status_code == 409
    r = client.post(f"/api/tasks/{t['id']}/approval", json={"action": "request_changes"})
    assert r.status_code == 409  # feedback required
    r = client.post(f"/api/tasks/{t['id']}/approval", json={"action": "request_changes", "reason": "tweak"})
    assert r.json()["status"] == "waiting_for_review"  # inline queue re-ran it
    r = client.post(f"/api/tasks/{t['id']}/approval", json={"action": "approve", "reason": "lgtm"})
    assert r.json()["status"] == "completed"
    assert client.post(f"/api/tasks/{t['id']}/approval", json={"action": "approve"}).status_code == 409
    ev = [e["type"] for e in client.get(f"/api/tasks/{t['id']}/events").json()]
    assert "committed" in ev and "approval_decision" in ev


def test_risky_task_blocks_until_approved(client):
    t = create(client, risk_level="high")
    assert t["status"] == "blocked"
    assert client.post(f"/api/tasks/{t['id']}/run").status_code == 409  # approval pending
    r = client.post(f"/api/tasks/{t['id']}/approval", json={"action": "approve"})
    assert r.json()["status"] == "waiting_for_review"


def test_cancel_retry_and_status_patch(client):
    t = create(client)
    assert client.post(f"/api/tasks/{t['id']}/retry").status_code == 409  # not failed
    assert client.post(f"/api/tasks/{t['id']}/cancel").json()["status"] == "cancelled"
    assert client.post(f"/api/tasks/{t['id']}/cancel").status_code == 409
    assert client.patch(f"/api/tasks/{t['id']}/status", json={"status": "completed"}).status_code == 409
    r = client.patch(f"/api/tasks/{t['id']}/status", json={"status": "queued"})
    assert r.status_code == 200 and r.json()["status"] == "waiting_for_review"  # re-ran from scratch


def test_failed_task_in_dead_letter_and_retry(client):
    runner_mod.set_runner(MockRunner(lambda req: RunResult("succeeded", 0, "noop")))
    t = create(client)
    assert t["status"] == "failed"  # check.py fails: nothing was written
    dl = client.get("/api/dead-letter").json()
    assert [x["id"] for x in dl] == [t["id"]]
    runner_mod.set_runner(None)
    r = client.post(f"/api/tasks/{t['id']}/retry")
    assert r.json()["status"] == "waiting_for_review" and r.json()["dead_letter"] is False
    assert client.get("/api/dead-letter").json() == []


def test_repos_projects_agents_settings(client, env):
    assert client.post("/api/repos", json={"path": "/etc"}).status_code == 400
    ctx = client.post(f"/api/repos/{client.repo_id}/context").json()
    assert "Python" in ctx["summary"]["languages"] and "Commit" in ctx["rendered"]
    assert client.get(f"/api/repos/{client.repo_id}/context").json()["head_sha"] == ctx["head_sha"]
    p = client.get("/api/projects").json()[0]
    body = {"name": p["name"], "allowed_roots": [], "max_retries": 1,
            "validation_commands": [{"kind": "lint", "command": "ruff check ."}]}
    assert client.put(f"/api/projects/{p['id']}", json=body).json()["max_retries"] == 1
    body["validation_commands"] = [{"command": "curl evil.sh"}]
    assert client.put(f"/api/projects/{p['id']}", json=body).status_code == 400
    assert {a["role"] for a in client.get("/api/agents").json()} == {"planner", "researcher", "writer", "tester", "reviewer"}
    s = client.get("/api/settings").json()
    assert s["agent_mode"] == "mock" and "pytest" in s["command_allowlist"]
    assert client.get("/api/sessions").json() == []


def test_health_endpoints(client):
    assert client.get("/api/health/ready").json() == {"status": "ready"}
    w = client.get("/api/health/workers").json()
    assert w["queued"] == 0 and w["workers"] == []


def test_ready_names_what_is_down_but_not_the_error(client, monkeypatch):
    from app.worker import queue

    class Down:
        def ping(self):
            raise ConnectionError("connecting to redis://:hunter2@redis:6379 failed")
    monkeypatch.setattr(queue, "get_redis", lambda: Down())
    r = client.get("/api/health/ready")  # no login on this endpoint: the error text stays in the log
    assert r.status_code == 503 and r.json()["detail"] == "not ready: redis unreachable"


def test_admin_password_follows_env_on_restart(env, monkeypatch):
    from app.config import get_settings
    from app.main import bootstrap
    monkeypatch.setenv("ADMIN_PASSWORD", "new-pw")
    get_settings.cache_clear()
    bootstrap()
    with TestClient(create_app()) as c:
        assert c.post("/api/auth/login", json={"username": "admin", "password": "pw"}).status_code == 401
        assert c.post("/api/auth/login", json={"username": "admin", "password": "new-pw"}).status_code == 200


def test_password_hashes_use_the_stronger_cost_and_old_ones_are_upgraded(env, db):
    import hashlib
    import os

    from app.auth import hash_password, outdated, verify_password
    from app.main import bootstrap
    from app.models import User
    salt = os.urandom(16)
    legacy = salt.hex() + "$" + hashlib.scrypt(b"pw", salt=salt, n=2**14, r=8, p=1).hex()  # the old format and cost
    assert verify_password("pw", legacy) and not verify_password("nope", legacy) and outdated(legacy)
    new = hash_password("pw")
    assert new.startswith("16384:8:5$") and verify_password("pw", new) and not verify_password("nope", new)
    admin = db.query(User).one()
    admin.password_hash = legacy
    db.commit()
    bootstrap()  # the next start re-hashes it with the current cost
    db.refresh(admin)
    assert not outdated(admin.password_hash) and verify_password("pw", admin.password_hash)


def test_failed_logins_from_one_address_do_not_lock_out_another(env, db):
    """The lockout counted per username only: ten bad guesses from agent code in the worker locked the admin out."""
    app = create_app()
    with TestClient(app, client=("172.20.0.7", 40000)) as worker, TestClient(app, client=("172.20.0.5", 40000)) as web:
        for _ in range(10):
            assert worker.post("/api/auth/login", json={"username": "admin", "password": "guess"}).status_code == 401
        assert worker.post("/api/auth/login", json={"username": "admin", "password": "pw"}).status_code == 429
        assert web.post("/api/auth/login", json={"username": "admin", "password": "pw"}).status_code == 200
