"""One test per bug found in review (each fails on the old code), plus the shared project context."""
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace as NS

import pytest

from app import states
from app.agents import get_agent
from app.config import get_settings
from app.models import AgentSession, Prompt, RepoContext, Task, ValidationResult
from app.services import context, planner, repo_state, review, validation
from app.services import runner as runner_mod
from app.services.runner import MockRunner, RunResult, default_mock
from app.services.sessions import policy_violation
from app.services.tasks import BadRequest, create_tasks, request_cancel
from app.worker import queue
from app.worker.queue import _on_failure


def submit(db, repo, **kw):
    t = create_tasks(db, title=kw.pop("title", "Add feature"), repo_ids=[repo.id], **kw)[0]
    queue.enqueue_task(t.id)
    db.refresh(t)
    return t


def test_retries_do_not_need_a_scheduler(db, repo, monkeypatch):
    monkeypatch.setenv("QUEUE_INLINE", "false")
    get_settings.cache_clear()
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    queue.enqueue_task(t.id)
    [job] = queue.get_queue().jobs
    assert job.retries_left == 2 and not any(job.retry_intervals or [])  # a delay would wait for a scheduler


def test_rq_failure_callback_waits_for_last_retry(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    err = RuntimeError("db blip")
    _on_failure(NS(retries_left=1, args=[t.id]), None, RuntimeError, err, None)
    db.refresh(t)
    assert t.status == states.QUEUED and not t.dead_letter  # the retry will still run
    _on_failure(NS(retries_left=0, args=[t.id]), None, RuntimeError, err, None)
    db.refresh(t)
    assert t.status == states.FAILED and t.dead_letter


def test_saved_check_with_null_timeout_still_times_out(db, repo, env, monkeypatch):
    monkeypatch.setenv("VALIDATION_TIMEOUT_S", "1")
    get_settings.cache_clear()
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    (env / "slow.py").write_text("import time\ntime.sleep(30)\n")
    [r] = validation.run_validation(db, t, NS(path=str(env)), [{"command": "python3 slow.py", "timeout": None}], 1)
    assert r.exit_code is None and "timed out" in r.output_tail


def test_tester_glob_matches_file_names_not_substrings(db):
    tester = get_agent(db, "tester")
    assert policy_violation(tester, ["src/latest_utils.py"])  # contains "test_" but is production code
    assert policy_violation(tester, ["tests/a.py", "pkg/test_b.py", "conftest.py", "web/x.spec.ts",
                                     "pkg/tests/c.py", "go/thing_test.go"]) is None


def test_unknown_branch_and_empty_repo_rejected_up_front(db, repo, project, env):
    with pytest.raises(BadRequest, match="mian"):
        create_tasks(db, title="x", repo_ids=[repo.id], branch_target="mian")
    empty = env / "repos" / "empty"
    empty.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=empty, check=True)
    from app.services.tasks import register_repo
    with pytest.raises(BadRequest, match="no commits"):
        register_repo(db, str(empty), project)


def test_worktree_never_falls_back_to_head(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    t.branch_target = "gone"  # branch deleted after the task was created
    db.commit()
    with pytest.raises(repo_state.GitError, match="not found"):
        repo_state.create_worktree(db, t, repo)


def test_context_race_uses_the_other_workers_row(db, repo, monkeypatch):
    from app.db import session_scope
    real = context.build_context

    def build_while_other_worker_inserts(path):
        ctx = real(path)
        with session_scope() as other:  # the second worker commits first
            other.add(RepoContext(repo_id=repo.id, head_sha=ctx["head_sha"], summary={"by": "other"}))
            other.commit()
        return ctx

    monkeypatch.setattr(context, "build_context", build_while_other_worker_inserts)
    assert context.get_or_create_context(db, repo).summary == {"by": "other"}


def test_validation_attempts_stay_unique_after_request_changes(db, repo):
    t = submit(db, repo)
    review.decide(db, t, "request_changes", "me", reason="again")
    queue.enqueue_task(t.id)
    attempts = [r.attempt for r in db.query(ValidationResult).filter_by(task_id=t.id).order_by(ValidationResult.id)]
    assert attempts == [1, 2]


def test_planner_writing_files_fails_the_task(db, repo):
    def handler(req):
        if req.kind == "plan":
            (Path(req.cwd) / "notes.md").write_text("plan")
        return default_mock(req)

    runner_mod.set_runner(MockRunner(handler))
    t = submit(db, repo)
    assert t.status == states.FAILED and "read-only role 'planner'" in t.error
    assert db.query(Task).filter_by(parent_id=t.id).count() == 0  # no subtask ran on top of the stray file


def test_sessions_left_running_by_a_dead_worker_are_closed(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    ghost = AgentSession(task_id=t.id, top_task_id=t.id, repo_id=repo.id, agent_role="writer", status="running")
    db.add(ghost)
    db.commit()
    queue.enqueue_task(t.id)
    db.refresh(ghost)
    assert ghost.status == "abandoned" and ghost.ended_at is not None


def test_cancel_while_worker_holds_lock_only_flags(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    with queue.task_lock(t.id):
        request_cancel(db, t)
        assert t.status == states.QUEUED and t.cancel_requested
        with pytest.raises(queue.TaskBusy):
            with queue.task_lock(t.id):
                pass
    assert not queue.get_redis().exists(queue.lock_key(t.id))  # released
    queue.enqueue_task(t.id)  # the next worker to pick it up honours the flag
    db.refresh(t)
    assert t.status == states.CANCELLED


def test_lock_is_not_released_by_someone_else(env):
    conn = queue.get_redis()
    with queue.task_lock(99):
        conn.set(queue.lock_key(99), "intruder")  # e.g. our TTL lapsed and another holder took over
    assert conn.get(queue.lock_key(99)) == b"intruder"


def test_plan_json_followed_by_prose_with_braces():
    text = 'Plan: {"steps": [{"key": "a", "title": "A", "kind": "implement"}]} Ping me if {x} needs work.'
    assert [s.key for s in planner.parse_agent_plan(text)] == ["a"]


def test_placeholder_secret_is_replaced(env, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "change-me-to-a-long-random-string")
    get_settings.cache_clear()
    assert get_settings().secret_key != "change-me-to-a-long-random-string" and len(get_settings().secret_key) == 64


def test_rename_shows_real_paths(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    bw = repo_state.create_worktree(db, t, repo)
    os.rename(Path(bw.path) / "src/app.py", Path(bw.path) / "src/main.py")
    d = repo_state.capture_diff(db, t, bw)
    assert {f["path"] for f in d.files} == {"src/app.py", "src/main.py"}


def test_agents_see_project_goal_and_each_other(db, repo, project):
    project.description = "Ship a booking app"
    db.commit()
    first = create_tasks(db, title="Build calendar", repo_ids=[repo.id])[0]
    first.status = states.RUNNING  # another agent is mid-task
    db.commit()
    t = submit(db, repo, title="Build payments")
    text = db.query(Prompt).filter_by(task_id=t.id, kind="plan").one().text
    assert "Ship a booking app" in text and f"#{first.id} Build calendar" in text


def test_runner_cancelled_plan_is_not_replaced_by_template(db, repo):
    def handler(req):
        if req.kind == "plan":
            from app.db import session_scope
            with session_scope() as other:
                other.query(Task).filter_by(parent_id=None).update({"cancel_requested": True})
                other.commit()
            return RunResult("cancelled", None, "stopped")
        return default_mock(req)

    runner_mod.set_runner(MockRunner(handler))
    t = submit(db, repo)
    assert t.status == states.CANCELLED and db.query(Task).filter_by(parent_id=t.id).count() == 0
