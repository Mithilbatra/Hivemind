from contextlib import contextmanager
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy import or_

from .. import states
from ..auth import current_user
from ..config import get_settings
from ..db import get_db
from ..models import (
    AgentSession,
    Approval,
    AuditEvent,
    BranchWorktree,
    Diff,
    OutputLog,
    Repository,
    Task,
    TaskDependency,
    ValidationResult,
)
from ..schemas import (
    ApprovalIn,
    ChildOut,
    DiffOut,
    EventOut,
    StatusPatch,
    TaskCreate,
    TaskDetail,
    TaskOut,
    ValidationOut,
    WorktreeOut,
)
from ..services import repo_state, review
from ..services import tasks as svc
from ..states import InvalidTransition, transition
from ..worker import queue

router = APIRouter(prefix="/tasks", dependencies=[Depends(current_user)])


def _task(db, task_id: int) -> Task:
    t = db.get(Task, task_id)
    if t is None:
        raise HTTPException(404, "task not found")
    return t


def _top(db, task_id: int) -> Task:
    t = _task(db, task_id)
    return db.get(Task, t.parent_id) if t.parent_id else t


def _enqueue(task_id: int):
    try:
        queue.enqueue_task(task_id)
    except Exception as e:  # Redis down: the task keeps its status and POST /run can enqueue it again
        raise HTTPException(503, f"could not enqueue: {e}")


@contextmanager
def _exclusive(db, t: Task):
    """Change a task only while no worker is mid-step on it; re-read it once we hold the lock."""
    try:
        with queue.task_lock(t.id):
            db.refresh(t)
            yield
    except queue.TaskBusy:
        raise HTTPException(409, "a worker is busy with this task; try again in a moment")


@router.post("", response_model=list[TaskOut], status_code=201)
def create(body: TaskCreate, db=Depends(get_db), user=Depends(current_user)):
    try:
        created = svc.create_tasks(db, **body.model_dump(exclude={"validation_commands"}),
                                   validation_commands=[c.model_dump() for c in body.validation_commands]
                                   if body.validation_commands is not None else None, user=user.username)
    except svc.BadRequest as e:
        raise HTTPException(400, str(e))
    for t in created:
        _enqueue(t.id)
    for t in created:
        db.refresh(t)
    return created


@router.get("", response_model=list[TaskOut])
def list_tasks(status: str | None = None, repo_id: int | None = None, q: str | None = None,
               limit: int = Query(100, le=500), db=Depends(get_db)):
    qs = db.query(Task).filter(Task.parent_id.is_(None))
    if status:
        qs = qs.filter(Task.status == status)
    if repo_id:
        qs = qs.filter(Task.repo_id == repo_id)
    if q:
        qs = qs.filter(or_(Task.title.ilike(f"%{q}%"), Task.description.ilike(f"%{q}%")))
    return qs.order_by(Task.id.desc()).limit(limit).all()


@router.get("/{task_id}", response_model=TaskDetail)
def detail(task_id: int, db=Depends(get_db)):
    t = _task(db, task_id)
    repo = db.get(Repository, t.repo_id)
    kids = db.query(Task).filter_by(parent_id=t.id).order_by(Task.position).all()
    deps = {}
    for d in db.query(TaskDependency).filter(TaskDependency.task_id.in_([k.id for k in kids] or [0])):
        deps.setdefault(d.task_id, []).append(d.depends_on_id)
    data = TaskOut.model_validate(t).model_dump()
    wt = db.query(BranchWorktree).filter_by(task_id=t.id).one_or_none()
    worktree = WorktreeOut.model_validate(wt) if wt else None
    if wt and wt.active:  # live, both directions: a task only stores the overlaps it found itself
        worktree.conflict_with = repo_state.find_conflicts(db, wt)
    return TaskDetail(
        **data, repo_name=repo.name, repo_path=repo.path,
        children=[ChildOut(**TaskOut.model_validate(k).model_dump(), depends_on=deps.get(k.id, [])) for k in kids],
        approvals=db.query(Approval).filter_by(task_id=t.id).order_by(Approval.id).all(),
        worktree=worktree)


@router.patch("/{task_id}/status", response_model=TaskOut)
def patch_status(task_id: int, body: StatusPatch, db=Depends(get_db), user=Depends(current_user)):
    t = _task(db, task_id)
    actor = f"user:{user.username}"
    if body.status not in (states.CANCELLED, states.QUEUED, states.BLOCKED):
        raise HTTPException(409, f"manual change to '{body.status}' is not allowed")
    try:
        if body.status == states.CANCELLED:
            svc.request_cancel(db, t, actor)
        else:
            with _exclusive(db, t):
                if body.status == states.QUEUED:
                    svc.reset_for_retry(db, t, actor)
                elif t.status in (states.QUEUED, states.ASSIGNED):
                    transition(db, t, states.BLOCKED, body.reason or "put on hold", actor)
                else:
                    raise HTTPException(409, f"cannot hold a task that is {t.status}")
            if body.status == states.QUEUED:
                _enqueue(t.id)
    except (svc.BadRequest, InvalidTransition) as e:
        raise HTTPException(409, str(e))
    db.refresh(t)
    return t


@router.post("/{task_id}/run", response_model=TaskOut)
def run(task_id: int, db=Depends(get_db), user=Depends(current_user)):
    t = _task(db, task_id)
    if t.parent_id is not None:
        raise HTTPException(409, "run the top-level task")
    if t.status == states.BLOCKED:
        with _exclusive(db, t):
            if db.query(Approval).filter_by(task_id=t.id, status="pending").count():
                raise HTTPException(409, "task is waiting for approval")
            transition(db, t, states.QUEUED, "released from hold", f"user:{user.username}")
    elif t.status not in (states.QUEUED, states.ASSIGNED, states.PLANNING, states.RUNNING, states.VALIDATION):
        raise HTTPException(409, f"cannot run a task that is {t.status}")
    _enqueue(t.id)  # a duplicate job for a task a worker already holds is skipped
    db.refresh(t)
    return t


@router.post("/{task_id}/retry", response_model=TaskOut)
def retry(task_id: int, db=Depends(get_db), user=Depends(current_user)):
    t = _task(db, task_id)
    try:
        with _exclusive(db, t):
            svc.reset_for_retry(db, t, f"user:{user.username}")
    except svc.BadRequest as e:
        raise HTTPException(409, str(e))
    _enqueue(t.id)
    db.refresh(t)
    return t


@router.post("/{task_id}/cancel", response_model=TaskOut)
def cancel(task_id: int, db=Depends(get_db), user=Depends(current_user)):
    t = _task(db, task_id)
    try:
        svc.request_cancel(db, t, f"user:{user.username}")
    except svc.BadRequest as e:
        raise HTTPException(409, str(e))
    db.refresh(t)
    return t


@router.post("/{task_id}/approval", response_model=TaskOut)
def approval(task_id: int, body: ApprovalIn, db=Depends(get_db), user=Depends(current_user)):
    t = _task(db, task_id)
    try:
        with _exclusive(db, t):
            requeue = review.decide(db, t, body.action, user.username, body.reason, body.confirm)
    except (svc.BadRequest, InvalidTransition, repo_state.GitError) as e:  # e.g. a locked index: say why, not "500"
        raise HTTPException(409, str(e))
    if requeue:
        _enqueue(t.id)
    db.refresh(t)
    return t


@router.get("/{task_id}/logs")
def logs(task_id: int, session_id: int | None = None, offset: int = 0, limit: int = Query(500, le=2000), db=Depends(get_db)):
    t = _top(db, task_id)
    sessions = db.query(AgentSession).filter_by(top_task_id=t.id).order_by(AgentSession.id).all()
    ids = [s.id for s in sessions if session_id in (None, s.id)]
    rows = (db.query(OutputLog).filter(OutputLog.session_id.in_(ids or [0])).order_by(OutputLog.session_id, OutputLog.seq)
            .offset(offset).limit(limit).all())
    return {"sessions": [{"id": s.id, "role": s.agent_role, "status": s.status, "task_id": s.task_id} for s in sessions],
            "offset": offset, "next_offset": offset + len(rows),
            "lines": [{"session_id": r.session_id, "seq": r.seq, "stream": r.stream, "text": r.chunk} for r in rows]}


@router.get("/{task_id}/sessions/{session_id}/log")
def full_log(task_id: int, session_id: int, db=Depends(get_db)):
    """The whole on-disk log; the database keeps only the first max_log_chars of each session."""
    s = db.get(AgentSession, session_id)
    if s is None or s.top_task_id != _top(db, task_id).id:
        raise HTTPException(404, "session not found")
    p = Path(s.log_path)
    if not p.resolve().is_relative_to(get_settings().data_dir.resolve()) or not p.is_file():
        raise HTTPException(404, "log file not found")
    return FileResponse(p, media_type="text/plain", filename=f"session-{s.id}.log")


@router.get("/{task_id}/diff", response_model=DiffOut | None)
def diff(task_id: int, db=Depends(get_db)):
    t = _top(db, task_id)
    return db.query(Diff).filter_by(task_id=t.id).order_by(Diff.id.desc()).first()


@router.get("/{task_id}/validation", response_model=list[ValidationOut])
def validation(task_id: int, db=Depends(get_db)):
    t = _top(db, task_id)
    return db.query(ValidationResult).filter_by(task_id=t.id).order_by(ValidationResult.id).all()


@router.get("/{task_id}/events", response_model=list[EventOut])
def events(task_id: int, db=Depends(get_db)):
    t = _top(db, task_id)
    kids = [k.id for k in db.query(Task.id).filter_by(parent_id=t.id)]
    return db.query(AuditEvent).filter(AuditEvent.task_id.in_([t.id, *kids])).order_by(AuditEvent.id).all()
