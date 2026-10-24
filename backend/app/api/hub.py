"""Agent hub: one read-only snapshot of which agent works on what, per project, plus the live event feed."""
import json
from collections import defaultdict

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func

from ..auth import current_user
from ..db import get_db
from ..models import (
    Agent,
    AgentSession,
    AuditEvent,
    BranchWorktree,
    OutputLog,
    Project,
    Prompt,
    Repository,
    Task,
)
from . import admin

router = APIRouter(dependencies=[Depends(current_user)])

TASK_LIMIT = 300  # most recent top-level tasks shown on the boards
EVENT_LIMIT = 40


def describe(line: str) -> str:
    """Turn one raw agent output line (plain text or stream-json) into something a person can read."""
    try:
        ev = json.loads(line)
    except ValueError:
        return line.strip()[:200]
    if not isinstance(ev, dict):
        return line.strip()[:200]
    if ev.get("type") == "result":
        return str(ev.get("result") or "")[:200]
    for c in (ev.get("message") or {}).get("content") or []:
        if c.get("type") == "tool_use":
            return f"{c.get('name')} {json.dumps(c.get('input'))[:160]}"
        if c.get("type") == "text" and c.get("text", "").strip():
            return c["text"].strip()[:200]
    return ""  # tool results and system events say nothing useful on their own


def _doing_now(db, session_id: int) -> str:
    rows = (db.query(OutputLog.chunk).filter_by(session_id=session_id).order_by(OutputLog.seq.desc()).limit(20).all())
    return next((d for (chunk,) in rows if (d := describe(chunk))), "")


@router.get("/hub")
def hub(db=Depends(get_db)):
    running = db.query(AgentSession).filter_by(status="running").order_by(AgentSession.id).all()
    recent = (db.query(AgentSession).filter(AgentSession.status != "running")
              .order_by(AgentSession.id.desc()).limit(60).all())
    tops = db.query(Task).filter(Task.parent_id.is_(None)).order_by(Task.id.desc()).limit(TASK_LIMIT).all()
    events = db.query(AuditEvent).order_by(AuditEvent.id.desc()).limit(EVENT_LIMIT).all()

    task_ids = {s.task_id for s in running + recent} | {s.top_task_id for s in running + recent}
    task_ids |= {e.task_id for e in events if e.task_id}
    titles = {t.id: t for t in db.query(Task).filter(Task.id.in_(task_ids or [0]))}
    repos = dict(db.query(Repository.id, Repository.name).all())
    kinds = dict(db.query(Prompt.session_id, Prompt.kind).filter(Prompt.session_id.in_([s.id for s in running] or [0])))

    def card(s: AgentSession, live: bool) -> dict:
        sub, top = titles.get(s.task_id), titles.get(s.top_task_id)
        return {"id": s.id, "role": s.agent_role, "status": s.status, "attempt": s.attempt, "kind": kinds.get(s.id),
                "task_id": s.task_id, "task_title": sub.title if sub else "", "top_task_id": s.top_task_id,
                "top_title": top.title if top else "", "project_id": top.project_id if top else None,
                "repo": repos.get(s.repo_id), "branch": s.branch, "started_at": s.started_at, "ended_at": s.ended_at,
                "doing": _doing_now(db, s.id) if live else s.summary[:200]}

    active = [card(s, True) for s in running]
    stats = defaultdict(dict)
    for role, status, n in db.query(AgentSession.agent_role, AgentSession.status, func.count()).group_by(
            AgentSession.agent_role, AgentSession.status):
        stats[role][status] = n
    by_role_recent = defaultdict(list)
    for s in recent:
        if len(by_role_recent[s.agent_role]) < 3:
            by_role_recent[s.agent_role].append(card(s, False))
    agents = [{"role": a.role, "description": a.description, "can_write": a.can_write,
               "test_files_only": bool(a.writable_globs), "constraints": a.constraints,
               "active": [c for c in active if c["role"] == a.role], "recent": by_role_recent[a.role],
               "stats": stats[a.role]} for a in db.query(Agent).order_by(Agent.id)]

    progress = defaultdict(lambda: {"done": 0, "total": 0})
    for parent, status, n in (db.query(Task.parent_id, Task.status, func.count())
                              .filter(Task.parent_id.in_([t.id for t in tops] or [0])).group_by(Task.parent_id, Task.status)):
        progress[parent]["total"] += n
        progress[parent]["done"] += n if status == "completed" else 0
    wts = db.query(BranchWorktree).filter(BranchWorktree.active.is_(True)).all()
    conflicts = {a.task_id for a in wts for b in wts if a.task_id != b.task_id and a.repo_id == b.repo_id
                 and set(a.touched_files or []) & set(b.touched_files or [])}
    working = {c["top_task_id"]: c for c in active}
    boards = defaultdict(list)
    for t in tops:
        w = working.get(t.id)
        boards[t.project_id].append({
            "id": t.id, "title": t.title, "status": t.status, "repo": repos.get(t.repo_id), "priority": t.priority,
            "risk_score": t.risk_score, "dead_letter": t.dead_letter, "updated_at": t.updated_at,
            "progress": progress[t.id], "conflict": t.id in conflicts,
            "agent": {"role": w["role"], "subtask": w["task_title"]} if w else None})
    projects = [{"id": p.id, "name": p.name, "description": p.description, "tasks": boards[p.id]}
                for p in db.query(Project).order_by(Project.id)]

    feed = []
    for e in events:
        t = titles.get(e.task_id)
        top_id = (t.parent_id or t.id) if t else None
        feed.append({"id": e.id, "ts": e.ts, "type": e.type, "actor": e.actor, "message": e.message,
                     "task_id": e.task_id, "top_task_id": top_id, "task_title": t.title if t else ""})
    try:
        workers = admin.workers()
    except HTTPException:
        workers = None
    return {"agents": agents, "active": active, "projects": projects, "events": feed, "workers": workers}
