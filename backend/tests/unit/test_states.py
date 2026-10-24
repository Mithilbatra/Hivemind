import pytest

from app import states
from app.models import Task
from app.services.tasks import create_tasks
from app.states import InvalidTransition, transition


def test_valid_and_invalid_transitions(db, repo):
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    transition(db, t, states.PLANNING)
    assert t.status == "planning"
    with pytest.raises(InvalidTransition):
        transition(db, t, states.COMPLETED)
    transition(db, t, states.CANCELLED)
    assert t.finished_at is not None
    with pytest.raises(InvalidTransition):
        transition(db, t, states.RUNNING)
    transition(db, t, states.QUEUED)  # retry clears finished_at
    assert t.finished_at is None


def test_subtask_can_complete_from_running(db, repo):
    top = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    c = Task(parent_id=top.id, project_id=top.project_id, repo_id=top.repo_id, title="c", status="running")
    db.add(c)
    db.commit()
    transition(db, c, states.COMPLETED)
    top.status = "running"
    with pytest.raises(InvalidTransition):
        transition(db, top, states.COMPLETED)


def test_every_transition_writes_audit(db, repo):
    from app.models import AuditEvent
    t = create_tasks(db, title="x", repo_ids=[repo.id])[0]
    transition(db, t, states.PLANNING, "go")
    ev = db.query(AuditEvent).filter_by(task_id=t.id, type="status_changed").one()
    assert ev.payload == {"from": "queued", "to": "planning"}
