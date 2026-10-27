from datetime import UTC, datetime

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def now():
    return datetime.now(UTC)


def _ts():
    return mapped_column(DateTime(timezone=True), default=now)


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    role: Mapped[str] = mapped_column(String(20), default="admin")
    created_at: Mapped[datetime] = _ts()


class Project(Base):
    __tablename__ = "projects"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    description: Mapped[str] = mapped_column(Text, default="", server_default="")  # the big goal; shown to every agent
    # Extra restriction on top of ALLOWED_REPO_ROOTS; empty list = no extra restriction.
    allowed_roots: Mapped[list] = mapped_column(JSON, default=list)
    # [{"kind": "test", "command": "pytest -q", "required": true, "timeout": 600}]
    validation_commands: Mapped[list] = mapped_column(JSON, default=list)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    created_at: Mapped[datetime] = _ts()


class Repository(Base):
    __tablename__ = "repositories"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    name: Mapped[str] = mapped_column(String(200))
    path: Mapped[str] = mapped_column(String(1000), unique=True)
    default_branch: Mapped[str] = mapped_column(String(200), default="main")
    created_at: Mapped[datetime] = _ts()


class RepoContext(Base):
    __tablename__ = "repo_contexts"
    __table_args__ = (UniqueConstraint("repo_id", "head_sha"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    head_sha: Mapped[str] = mapped_column(String(64))
    summary: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = _ts()


class Agent(Base):
    __tablename__ = "agents"
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(50), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    system_prompt: Mapped[str] = mapped_column(Text, default="")
    constraints: Mapped[list] = mapped_column(JSON, default=list)
    can_write: Mapped[bool] = mapped_column(Boolean, default=False)
    writable_globs: Mapped[list] = mapped_column(JSON, default=list)  # empty = anywhere (if can_write)
    tool_policy: Mapped[dict] = mapped_column(JSON, default=dict)


class Task(Base):
    __tablename__ = "tasks"
    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[str | None] = mapped_column(String(40), index=True)  # tasks created together for several repos
    parent_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"))
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    title: Mapped[str] = mapped_column(String(300))
    description: Mapped[str] = mapped_column(Text, default="")
    kind: Mapped[str] = mapped_column(String(30), default="generic")  # feature|bugfix|refactor|generic|step kinds
    role: Mapped[str | None] = mapped_column(String(50))
    position: Mapped[int] = mapped_column(Integer, default=0)
    branch_target: Mapped[str | None] = mapped_column(String(200))
    issue_ref: Mapped[str | None] = mapped_column(String(300))
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    risk_level: Mapped[str] = mapped_column(String(20), default="low")
    risk_score: Mapped[float] = mapped_column(Float, default=0)
    risk_reasons: Mapped[list] = mapped_column(JSON, default=list)
    acceptance_criteria: Mapped[str] = mapped_column(Text, default="")
    validation_override: Mapped[list | None] = mapped_column(JSON)
    status: Mapped[str] = mapped_column(String(30), default="queued", index=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=2)
    pending_prompt: Mapped[str | None] = mapped_column(Text)  # debug/feedback prompt for the next fix session
    pending_kind: Mapped[str | None] = mapped_column(String(20))
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    dead_letter: Mapped[bool] = mapped_column(Boolean, default=False)
    summary: Mapped[str] = mapped_column(Text, default="")
    error: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(String(100), default="system")
    created_at: Mapped[datetime] = _ts()
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"))


class AgentSession(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    top_task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"))
    branch: Mapped[str] = mapped_column(String(300), default="")
    agent_role: Mapped[str] = mapped_column(String(50))
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    status: Mapped[str] = mapped_column(String(20), default="running")  # running|succeeded|failed|cancelled|timeout
    started_at: Mapped[datetime] = _ts()
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    exit_code: Mapped[int | None] = mapped_column(Integer)
    summary: Mapped[str] = mapped_column(Text, default="")
    log_path: Mapped[str] = mapped_column(String(1000), default="")
    diff_summary: Mapped[dict] = mapped_column(JSON, default=dict)
    tool_calls: Mapped[list] = mapped_column(JSON, default=list)


class Prompt(Base):
    __tablename__ = "prompts"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # plan|task|debug|feedback
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class OutputLog(Base):
    __tablename__ = "output_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[int] = mapped_column(ForeignKey("sessions.id"), index=True)
    seq: Mapped[int] = mapped_column(Integer)
    stream: Mapped[str] = mapped_column(String(10))
    chunk: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = _ts()


class ValidationResult(Base):
    __tablename__ = "validation_results"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    kind: Mapped[str] = mapped_column(String(30))
    command: Mapped[str] = mapped_column(Text)
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    passed: Mapped[bool] = mapped_column(Boolean)
    exit_code: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float] = mapped_column(Float, default=0)
    output_tail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()


class Diff(Base):
    __tablename__ = "diffs"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    session_id: Mapped[int | None] = mapped_column(ForeignKey("sessions.id"))
    patch: Mapped[str] = mapped_column(Text, default="")
    files: Mapped[list] = mapped_column(JSON, default=list)  # [{path, additions, deletions}]
    additions: Mapped[int] = mapped_column(Integer, default=0)
    deletions: Mapped[int] = mapped_column(Integer, default=0)
    truncated: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = _ts()


class AuditEvent(Base):
    __tablename__ = "audit_events"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"), index=True)
    actor: Mapped[str] = mapped_column(String(100), default="system")
    type: Mapped[str] = mapped_column(String(50), index=True)
    message: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    ts: Mapped[datetime] = _ts()


class Approval(Base):
    __tablename__ = "approvals"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))  # pre_run|pre_commit
    status: Mapped[str] = mapped_column(String(20), default="pending")  # pending|approved|rejected|changes_requested|forced
    decided_by: Mapped[str | None] = mapped_column(String(100))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = _ts()
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class BranchWorktree(Base):
    __tablename__ = "branch_worktrees"
    id: Mapped[int] = mapped_column(primary_key=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id"), unique=True)
    repo_id: Mapped[int] = mapped_column(ForeignKey("repositories.id"), index=True)
    branch: Mapped[str] = mapped_column(String(300))
    base_branch: Mapped[str] = mapped_column(String(200), default="")
    base_sha: Mapped[str] = mapped_column(String(64))
    path: Mapped[str] = mapped_column(String(1000))
    touched_files: Mapped[list] = mapped_column(JSON, default=list)
    conflict_with: Mapped[list] = mapped_column(JSON, default=list)  # [{task_id, files}]
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = _ts()
