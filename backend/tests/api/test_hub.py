from fastapi.testclient import TestClient

from app.api.hub import describe
from app.main import create_app
from app.models import AgentSession, OutputLog
from app.services.tasks import create_tasks
from app.worker import queue


def login(c):
    tok = c.post("/api/auth/login", json={"username": "admin", "password": "pw"}).json()["token"]
    c.headers["Authorization"] = f"Bearer {tok}"


def test_describe_stream_json_and_text():
    tool = '{"type":"assistant","message":{"content":[{"type":"tool_use","name":"Edit","input":{"file_path":"a.py"}}]}}'
    assert describe(tool) == 'Edit {"file_path": "a.py"}'
    assert describe('{"type":"result","result":"done"}') == "done"
    assert describe('{"type":"user","message":{"content":[{"type":"tool_result"}]}}') == ""
    assert describe("plain line  ") == "plain line"


def test_hub_snapshot(env, db, repo):
    with TestClient(create_app()) as c:
        login(c)
        assert c.get("/api/hub", headers={"Authorization": ""}).status_code == 401
        done = c.post("/api/tasks", json={"title": "Finished one", "repo_ids": [repo.id]}).json()[0]
        busy = create_tasks(db, title="In flight", repo_ids=[repo.id])[0]
        busy.status = "running"
        live = AgentSession(task_id=busy.id, top_task_id=busy.id, repo_id=repo.id, agent_role="writer", status="running")
        db.add(live)
        db.flush()
        db.add(OutputLog(session_id=live.id, seq=0, stream="stdout",
                         chunk='{"type":"assistant","message":{"content":[{"type":"text","text":"Editing greeter.py"}]}}'))
        db.commit()

        h = c.get("/api/hub").json()
        writer = next(a for a in h["agents"] if a["role"] == "writer")
        assert [s["top_title"] for s in writer["active"]] == ["In flight"]
        assert writer["active"][0]["doing"] == "Editing greeter.py"
        assert writer["recent"] and writer["stats"]["succeeded"] >= 1
        tester = next(a for a in h["agents"] if a["role"] == "tester")
        assert tester["test_files_only"] and not tester["active"]
        board = {t["id"]: t for t in h["projects"][0]["tasks"]}
        assert board[done["id"]]["progress"] == {"done": 5, "total": 5}
        assert board[busy.id]["agent"] == {"role": "writer", "subtask": "In flight"}
        assert h["events"][0]["id"] > h["events"][-1]["id"]  # newest first
        assert any(e["top_task_id"] == done["id"] for e in h["events"])
        assert h["workers"]["queued"] == 0


def test_projects_with_goal_and_duplicate_name(env):
    with TestClient(create_app()) as c:
        login(c)
        r = c.post("/api/projects", json={"name": "Booking app", "description": "Build a booking app end to end"})
        assert r.status_code == 201 and r.json()["description"].startswith("Build")
        assert c.post("/api/projects", json={"name": "Booking app"}).status_code == 409
        assert c.post("/api/projects", json={"name": ""}).status_code == 422


def test_decision_while_worker_holds_task_is_refused(env, db, repo):
    with TestClient(create_app()) as c:
        login(c)
        t = c.post("/api/tasks", json={"title": "x", "repo_ids": [repo.id]}).json()[0]
        with queue.task_lock(t["id"]):
            r = c.post(f"/api/tasks/{t['id']}/approval", json={"action": "approve"})
            assert r.status_code == 409 and "busy" in r.json()["detail"]
        assert c.post(f"/api/tasks/{t['id']}/approval", json={"action": "approve"}).json()["status"] == "completed"
