from fastapi import APIRouter, Depends, HTTPException
from rq import Worker
from rq.registry import FailedJobRegistry
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from ..auth import create_user_session, current_user
from ..config import get_settings
from ..db import get_db
from ..models import Agent, AgentSession, Project, Repository, Task
from ..schemas import AgentOut, Login, ProjectIn, ProjectOut, RepoIn, RepoOut, SessionOut, TaskOut
from ..services import context, safety
from ..services import tasks as svc
from ..worker import queue

public = APIRouter()
router = APIRouter(dependencies=[Depends(current_user)])


@public.post("/auth/login")
def login(body: Login, db=Depends(get_db)):
    token = create_user_session(db, body.username, body.password)
    if token is None:
        raise HTTPException(401, "bad credentials")
    return {"token": token, "username": body.username}


@public.get("/health")
def health():
    return {"status": "ok"}


@public.get("/health/ready")
def ready(db=Depends(get_db)):
    try:
        db.execute(text("select 1"))
        queue.get_redis().ping()
    except Exception as e:
        raise HTTPException(503, f"not ready: {e}")
    return {"status": "ready"}


@public.get("/health/workers")
def workers():
    try:
        conn, q = queue.get_redis(), queue.get_queue()
        ws = Worker.all(connection=conn)
        return {"queued": q.count, "failed": FailedJobRegistry(queue=q).count,
                "workers": [{"name": w.name, "state": w.get_state(), "last_heartbeat": str(w.last_heartbeat)} for w in ws]}
    except Exception as e:
        raise HTTPException(503, f"redis unavailable: {e}")


@router.get("/repos", response_model=list[RepoOut])
def repos(db=Depends(get_db)):
    return db.query(Repository).order_by(Repository.id).all()


@router.post("/repos", response_model=RepoOut, status_code=201)
def add_repo(body: RepoIn, db=Depends(get_db)):
    project = db.get(Project, body.project_id) if body.project_id else db.query(Project).order_by(Project.id).first()
    if project is None:
        raise HTTPException(404, "project not found")
    try:
        return svc.register_repo(db, body.path, project, body.name, body.default_branch)
    except svc.BadRequest as e:
        raise HTTPException(400, str(e))


@router.post("/repos/{repo_id}/context")
def refresh_context(repo_id: int, db=Depends(get_db)):
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(404, "repo not found")
    row = context.get_or_create_context(db, repo, force=True)
    return {"head_sha": row.head_sha, "summary": row.summary, "rendered": context.render(row.summary)}


@router.get("/repos/{repo_id}/context")
def get_context(repo_id: int, db=Depends(get_db)):
    repo = db.get(Repository, repo_id)
    if repo is None:
        raise HTTPException(404, "repo not found")
    row = context.get_or_create_context(db, repo)
    return {"head_sha": row.head_sha, "summary": row.summary, "rendered": context.render(row.summary)}


def _check_commands(cmds):
    for c in cmds:
        try:
            safety.validate_command(c.command)
        except safety.UnsafeCommand as e:
            raise HTTPException(400, f"'{c.command}' rejected: {e}")


@router.get("/projects", response_model=list[ProjectOut])
def projects(db=Depends(get_db)):
    return db.query(Project).order_by(Project.id).all()


def _save_project(db, p: Project, body: ProjectIn) -> Project:
    _check_commands(body.validation_commands)
    p.name, p.description, p.allowed_roots, p.max_retries = body.name, body.description, body.allowed_roots, body.max_retries
    p.validation_commands = [c.model_dump(exclude_none=True) for c in body.validation_commands]
    db.add(p)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, f"a project named '{body.name}' already exists")
    return p


@router.post("/projects", response_model=ProjectOut, status_code=201)
def add_project(body: ProjectIn, db=Depends(get_db)):
    return _save_project(db, Project(), body)


@router.put("/projects/{project_id}", response_model=ProjectOut)
def update_project(project_id: int, body: ProjectIn, db=Depends(get_db)):
    p = db.get(Project, project_id)
    if p is None:
        raise HTTPException(404, "project not found")
    return _save_project(db, p, body)


@router.get("/agents", response_model=list[AgentOut])
def agents(db=Depends(get_db)):
    return db.query(Agent).order_by(Agent.id).all()


@router.get("/sessions", response_model=list[SessionOut])
def sessions(task_id: int | None = None, limit: int = 100, db=Depends(get_db)):
    q = db.query(AgentSession)
    if task_id:
        q = q.filter(AgentSession.top_task_id == task_id)
    return q.order_by(AgentSession.id.desc()).limit(limit).all()


@router.get("/dead-letter", response_model=list[TaskOut])
def dead_letter(db=Depends(get_db)):
    return db.query(Task).filter_by(dead_letter=True, parent_id=None).order_by(Task.id.desc()).all()


@router.get("/settings")
def runtime_settings():
    s = get_settings()
    return {"agent_mode": s.agent_mode, "agent_command": s.agent_command, "allowed_repo_roots": s.repo_roots and
            [str(r) for r in s.repo_roots], "session_timeout_s": s.session_timeout_s,
            "validation_timeout_s": s.validation_timeout_s, "default_max_retries": s.max_retries,
            "retry_backoff_s": s.retry_backoff_s, "worker_concurrency": s.worker_concurrency,
            "command_allowlist": sorted(safety.ALLOWED_EXECUTABLES)}
