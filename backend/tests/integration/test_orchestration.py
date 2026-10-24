import subprocess
from pathlib import Path

import pytest

from app import states
from app.config import get_settings
from app.db import session_scope
from app.models import (
    AgentSession,
    Approval,
    AuditEvent,
    BranchWorktree,
    Diff,
    Prompt,
    Task,
    ValidationResult,
)
from app.services import review
from app.services import runner as runner_mod
from app.services.runner import MockRunner, RunResult, default_mock
from app.services.tasks import BadRequest, create_tasks, request_cancel, reset_for_retry
from app.worker import queue


def submit(db, repo, **kw):
    t = create_tasks(db, title=kw.pop("title", "Add feature"), repo_ids=[repo.id], **kw)[0]
    queue.enqueue_task(t.id)
    db.refresh(t)
    return t


def events(db, t, type=None):
    ids = [t.id] + [c.id for c in db.query(Task).filter_by(parent_id=t.id)]
    q = db.query(AuditEvent).filter(AuditEvent.task_id.in_(ids))
    return [e for e in q.order_by(AuditEvent.id) if type in (None, e.type)]


def test_full_run_to_review_then_approve(db, repo):
    t = submit(db, repo, acceptance_criteria="VALUE must be 1")
    assert t.status == states.REVIEW
    kids = db.query(Task).filter_by(parent_id=t.id).order_by(Task.position).all()
    assert [k.role for k in kids] == ["researcher", "planner", "writer", "tester", "reviewer"]
    assert all(k.status == states.COMPLETED for k in kids)
    assert [r.passed for r in db.query(ValidationResult).filter_by(task_id=t.id)] == [True]
    diff = db.query(Diff).filter_by(task_id=t.id).order_by(Diff.id.desc()).first()
    assert {f["path"] for f in diff.files} == {"mock_feature.py", "tests/test_mock_feature.py"}
    prompt = db.query(Prompt).filter_by(task_id=kids[2].id).one().text
    assert "VALUE must be 1" in prompt and "python3 check.py" in prompt and "Earlier steps" in prompt
    assert db.query(Approval).filter_by(task_id=t.id, kind="pre_commit", status="pending").count() == 1
    assert 0 < t.risk_score <= 100

    review.decide(db, t, "approve", "mithil")
    assert t.status == states.COMPLETED
    log = subprocess.run(["git", "log", "--format=%an|%s", f"orch/task-{t.id}", "-1"], cwd=repo.path, capture_output=True,
                         text=True, check=True).stdout.strip()
    assert log == "Orchestrator|Add feature"
    shown = subprocess.run(["git", "show", f"orch/task-{t.id}:mock_feature.py"], cwd=repo.path, capture_output=True, text=True)
    assert "VALUE = 1" in shown.stdout
    assert not Path(get_settings().worktrees_dir / f"task-{t.id}").exists()
    assert not (Path(repo.path) / "mock_feature.py").exists()
    types = {e.type for e in events(db, t)}
    assert {"task_created", "plan_created", "worktree_created", "session_started", "command", "validation_result",
            "approval_decision", "committed"} <= types


def test_validation_failure_retries_with_debug_prompt(db, repo, monkeypatch):
    monkeypatch.setenv("MOCK_FAIL_FIRST", "true")
    get_settings.cache_clear()
    t = submit(db, repo)
    assert t.status == states.REVIEW and t.retry_count == 1
    res = db.query(ValidationResult).filter_by(task_id=t.id).order_by(ValidationResult.id).all()
    assert [(r.attempt, r.passed) for r in res] == [(1, False), (2, True)]
    assert "VALUE is 0" in res[0].output_tail
    debug = db.query(Prompt).filter_by(task_id=t.id, kind="debug").one().text
    assert "Validation failed" in debug and "VALUE is 0" in debug
    retry_ev = events(db, t, "retry")
    assert len(retry_ev) == 1 and "retry 1/2" in retry_ev[0].message


def test_retries_exhausted_fails_closed_then_force_commit(db, repo):
    runner_mod.set_runner(MockRunner(lambda req: _bad_writer(req)))
    t = submit(db, repo)
    assert t.status == states.FAILED and t.dead_letter and t.retry_count == 2
    assert len(db.query(ValidationResult).filter_by(task_id=t.id).all()) == 3
    assert events(db, t, "dead_letter")
    with pytest.raises(BadRequest):
        review.decide(db, t, "force_commit", "me", reason="ship it")  # confirm missing
    with pytest.raises(BadRequest):
        review.decide(db, t, "approve", "me")
    review.decide(db, t, "force_commit", "me", reason="known flaky check", confirm=True)
    assert t.status == states.COMPLETED
    assert events(db, t, "force_commit")[0].payload == {"validation_bypassed": True}


def _bad_writer(req):
    if req.role == "writer":
        (Path(req.cwd) / "mock_feature.py").write_text("VALUE = 0\n")
    return RunResult("succeeded", 0, "ok")


def test_retry_after_failure_resumes(db, repo):
    runner_mod.set_runner(MockRunner(_bad_writer))
    t = submit(db, repo)
    assert t.status == states.FAILED
    runner_mod.set_runner(MockRunner(default_mock))
    reset_for_retry(db, t)
    assert (t.status, t.dead_letter, t.retry_count) == (states.QUEUED, False, 0)
    queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.REVIEW
    assert db.query(Task).filter_by(parent_id=t.id).count() == 5  # plan reused, not recreated


def test_request_changes_loop(db, repo):
    t = submit(db, repo)
    with pytest.raises(BadRequest):
        review.decide(db, t, "request_changes", "me")  # needs feedback
    assert review.decide(db, t, "request_changes", "me", reason="rename VALUE to LEVEL") is True
    assert t.status == states.RUNNING
    queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.REVIEW
    fb = db.query(Prompt).filter_by(task_id=t.id, kind="feedback").one().text
    assert "rename VALUE to LEVEL" in fb
    assert [a.status for a in db.query(Approval).filter_by(task_id=t.id, kind="pre_commit").order_by(Approval.id)] == [
        "changes_requested", "pending"]


def test_reject_cancels_and_removes_branch(db, repo):
    t = submit(db, repo)
    review.decide(db, t, "reject", "me", reason="nope")
    assert t.status == states.CANCELLED
    branches = subprocess.run(["git", "branch", "--list", "orch/*"], cwd=repo.path, capture_output=True, text=True).stdout
    assert branches.strip() == ""


def test_high_risk_requires_pre_run_approval(db, repo):
    t = submit(db, repo, risk_level="high")
    assert t.status == states.BLOCKED
    assert db.query(Approval).filter_by(task_id=t.id, kind="pre_run", status="pending").count() == 1
    assert db.query(ValidationResult).filter_by(task_id=t.id).count() == 0
    assert db.query(AgentSession).filter_by(top_task_id=t.id).count() == 1  # only the read-only planner ran
    with pytest.raises(BadRequest):
        review.decide(db, t, "request_changes", "me")
    assert review.decide(db, t, "approve", "me") is True
    queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.REVIEW


def test_high_risk_pre_run_reject(db, repo):
    t = submit(db, repo, risk_level="critical")
    review.decide(db, t, "reject", "me", reason="too risky")
    assert t.status == states.CANCELLED


def test_cancel_in_review_and_when_flagged_mid_run(db, repo):
    t = submit(db, repo)
    request_cancel(db, t)
    assert t.status == states.CANCELLED
    assert all(c.status in states.TERMINAL for c in db.query(Task).filter_by(parent_id=t.id))
    with pytest.raises(BadRequest):
        request_cancel(db, t)

    def cancelling(req):
        with session_scope() as other:
            other.get(Task, t2.id).cancel_requested = True
            other.commit()
        return RunResult("cancelled", None, "stopped")

    t2 = create_tasks(db, title="slow", repo_ids=[repo.id])[0]
    runner_mod.set_runner(MockRunner(cancelling))
    queue.enqueue_task(t2.id)
    db.refresh(t2)
    assert t2.status == states.CANCELLED
    assert not Path(get_settings().worktrees_dir / f"task-{t2.id}").exists()


def test_read_only_agent_modifying_files_fails_task(db, repo):
    def handler(req):
        if req.role == "researcher":
            (Path(req.cwd) / "sneaky.txt").write_text("x")
        return RunResult("succeeded", 0, "ok")

    runner_mod.set_runner(MockRunner(handler))
    t = submit(db, repo)
    assert t.status == states.FAILED and "inspect" in t.error.lower()
    assert events(db, t, "policy_violation")


def test_tester_limited_to_test_paths(db, repo):
    def handler(req):
        if req.role == "tester":
            (Path(req.cwd) / "src/app.py").write_text("sabotage\n")
        return default_mock(req)

    runner_mod.set_runner(MockRunner(handler))
    t = submit(db, repo)
    assert t.status == states.FAILED
    assert "outside its allowed paths" in events(db, t, "policy_violation")[0].message


def test_session_retry_with_backoff_then_success(db, repo):
    calls = {"n": 0}

    def flaky(req):
        if req.role == "writer":
            calls["n"] += 1
            if calls["n"] == 1:
                return RunResult("failed", 1, "agent crashed")
        return default_mock(req)

    runner_mod.set_runner(MockRunner(flaky))
    t = submit(db, repo)
    assert t.status == states.REVIEW
    writer = db.query(Task).filter_by(parent_id=t.id, role="writer").one()
    assert writer.retry_count == 1 and calls["n"] == 2
    assert "agent crashed" in events(db, writer, "retry")[0].payload["reason"]


def test_unsafe_validation_command_blocks(db, repo, project):
    project.validation_commands = [{"kind": "test", "command": "rm -rf /tmp/x", "required": True}]
    db.commit()
    t = submit(db, repo)
    assert t.status == states.FAILED
    r = db.query(ValidationResult).filter_by(task_id=t.id).first()
    assert not r.passed and r.exit_code is None and "blocked by safety policy" in r.output_tail
    assert events(db, t, "command_blocked")


def test_no_validation_configured_still_requires_review(db, project, make_repo):
    from app.services.tasks import register_repo
    bare = register_repo(db, str(make_repo("bare", {"README.md": "x\n"})), project)
    project.validation_commands = []
    db.commit()
    t = submit(db, bare)
    assert t.status == states.REVIEW and events(db, t, "no_validation_configured")
    assert any("no validation" in r for r in t.risk_reasons)


def test_agent_plan_is_used_when_valid(db, repo):
    plan = '{"steps": [{"key": "a", "title": "Only step", "kind": "implement"}]}'

    def handler(req):
        return RunResult("succeeded", 0, plan, text=plan) if req.kind == "plan" else default_mock(req)

    runner_mod.set_runner(MockRunner(handler))
    t = submit(db, repo)
    assert [k.title for k in db.query(Task).filter_by(parent_id=t.id)] == ["Only step"]
    assert not events(db, t, "plan_fallback")


def test_concurrent_tasks_on_same_file_warn(db, repo):
    t1 = submit(db, repo, title="one")
    t2 = submit(db, repo, title="two")
    bw2 = db.query(BranchWorktree).filter_by(task_id=t2.id).one()
    assert bw2.conflict_with and bw2.conflict_with[0]["task_id"] == t1.id
    assert "mock_feature.py" in bw2.conflict_with[0]["files"]
    assert events(db, t2, "conflict_warning")
    assert any("overlapping" in r for r in t2.risk_reasons)


def test_job_crash_goes_to_dead_letter(db, repo):
    def boom(req):
        raise RuntimeError("worker exploded")

    runner_mod.set_runner(MockRunner(boom))
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    with pytest.raises(RuntimeError):
        queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.FAILED and t.dead_letter and "worker exploded" in t.error


def test_redis_lock_prevents_double_processing(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    queue.get_redis().set(queue.lock_key(t.id), "1")
    queue.enqueue_task(t.id)
    db.refresh(t)
    assert t.status == states.QUEUED
