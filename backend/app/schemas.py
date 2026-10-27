from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class Login(BaseModel):
    username: str = Field(max_length=100)  # also a Redis key for the failed-login counter
    password: str = Field(max_length=1000)


class ValidationCommand(BaseModel):
    kind: str = "test"
    command: str
    required: bool = True
    timeout: int | None = None


class TaskCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str = ""
    repo_ids: list[int] = []
    repo_paths: list[str] = []
    project_id: int | None = None
    branch_target: str | None = None
    issue_ref: str | None = None
    priority: str = "normal"
    risk_level: str = "low"
    acceptance_criteria: str = ""
    validation_commands: list[ValidationCommand] | None = None


class StatusPatch(BaseModel):
    status: str
    reason: str = ""


class ApprovalIn(BaseModel):
    action: str
    reason: str = ""
    confirm: bool = False


class RepoIn(BaseModel):
    path: str
    name: str | None = None
    project_id: int | None = None
    default_branch: str | None = None


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = ""
    allowed_roots: list[str] = []
    validation_commands: list[ValidationCommand] = []
    max_retries: int = Field(2, ge=0, le=10)


class TaskOut(ORM):
    id: int
    group_id: str | None
    parent_id: int | None
    project_id: int
    repo_id: int
    title: str
    description: str
    kind: str
    role: str | None
    position: int
    branch_target: str | None
    issue_ref: str | None
    priority: str
    risk_level: str
    risk_score: float
    risk_reasons: list
    acceptance_criteria: str
    status: str
    retry_count: int
    max_retries: int
    cancel_requested: bool
    dead_letter: bool
    summary: str
    error: str | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class ChildOut(TaskOut):
    depends_on: list[int] = []


class SessionOut(ORM):
    id: int
    task_id: int
    top_task_id: int
    branch: str
    agent_role: str
    attempt: int
    status: str
    started_at: datetime
    ended_at: datetime | None
    exit_code: int | None
    summary: str
    diff_summary: dict
    tool_calls: list


class ApprovalOut(ORM):
    id: int
    task_id: int
    kind: str
    status: str
    decided_by: str | None
    reason: str
    created_at: datetime
    decided_at: datetime | None


class WorktreeOut(ORM):
    branch: str
    base_branch: str
    base_sha: str
    touched_files: list
    conflict_with: list
    active: bool
    commit_sha: str | None


class TaskDetail(TaskOut):
    children: list[ChildOut]  # sessions come from GET /sessions?task_id=, only when the Agents tab asks
    approvals: list[ApprovalOut]
    worktree: WorktreeOut | None
    repo_name: str
    repo_path: str


class ValidationOut(ORM):
    id: int
    attempt: int
    kind: str
    command: str
    required: bool
    passed: bool
    exit_code: int | None
    duration_s: float
    output_tail: str
    created_at: datetime


class DiffOut(ORM):
    id: int
    session_id: int | None
    patch: str
    files: list
    additions: int
    deletions: int
    truncated: bool
    created_at: datetime


class EventOut(ORM):
    id: int
    task_id: int | None
    actor: str
    type: str
    message: str
    payload: dict
    ts: datetime


class RepoOut(ORM):
    id: int
    project_id: int
    name: str
    path: str
    default_branch: str


class ProjectOut(ORM):
    id: int
    name: str
    description: str
    allowed_roots: list
    validation_commands: list
    max_retries: int


class AgentOut(ORM):
    role: str
    description: str
    constraints: list
    can_write: bool
    writable_globs: list
    tool_policy: dict
