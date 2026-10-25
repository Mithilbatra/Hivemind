export type Task = {
  id: number; group_id: string | null; parent_id: number | null; repo_id: number; project_id: number;
  title: string; description: string; kind: string; role: string | null; position: number;
  branch_target: string | null; issue_ref: string | null; priority: string; risk_level: string;
  risk_score: number; risk_reasons: string[]; acceptance_criteria: string; status: string;
  retry_count: number; max_retries: number; cancel_requested: boolean; dead_letter: boolean;
  summary: string; error: string | null; created_by: string; created_at: string; updated_at: string;
  started_at: string | null; finished_at: string | null;
};
export type Child = Task & { depends_on: number[] };
export type Session = {
  id: number; task_id: number; agent_role: string; attempt: number; status: string; started_at: string;
  ended_at: string | null; exit_code: number | null; summary: string; tool_calls: { name: string; input: string }[];
  diff_summary: { changed?: string[]; additions?: number; deletions?: number; violation?: string | null };
};
export type Approval = {
  id: number; kind: string; status: string; decided_by: string | null; reason: string; created_at: string;
};
export type Worktree = {
  branch: string; base_branch: string; base_sha: string; touched_files: string[];
  conflict_with: { task_id: number; files: string[] }[]; active: boolean; commit_sha: string | null;
};
export type TaskDetail = Task & {
  children: Child[]; sessions: Session[]; approvals: Approval[]; worktree: Worktree | null;
  repo_name: string; repo_path: string;
};
export type Validation = {
  id: number; attempt: number; kind: string; command: string; required: boolean; passed: boolean;
  exit_code: number | null; duration_s: number; output_tail: string;
};
export type Diff = {
  patch: string; files: { path: string; additions: number; deletions: number }[]; additions: number;
  deletions: number; truncated: boolean;
} | null;
export type AuditEvent = { id: number; task_id: number | null; actor: string; type: string; message: string; ts: string; payload: Record<string, unknown> };
export type Repo = { id: number; project_id: number; name: string; path: string; default_branch: string };
export type ValidationCommand = { kind: string; command: string; required: boolean };
export type Project = { id: number; name: string; description: string; allowed_roots: string[]; validation_commands: ValidationCommand[]; max_retries: number };

export type HubSession = {
  id: number; role: string; status: string; attempt: number; kind: string | null; task_id: number; task_title: string;
  top_task_id: number; top_title: string; project_id: number | null; repo: string | null; branch: string;
  started_at: string; ended_at: string | null; doing: string;
};
export type HubAgent = {
  role: string; description: string; can_write: boolean; test_files_only: boolean; constraints: string[];
  active: HubSession[]; recent: HubSession[]; stats: Record<string, number>;
};
export type HubTask = {
  id: number; title: string; status: string; repo: string | null; priority: string; risk_score: number;
  dead_letter: boolean; updated_at: string; progress: { done: number; total: number }; conflict: boolean;
  agent: { role: string; subtask: string } | null;
};
export type HubEvent = {
  id: number; ts: string; type: string; actor: string; message: string; task_id: number | null;
  top_task_id: number | null; task_title: string;
};
export type Hub = {
  agents: HubAgent[]; active: HubSession[]; events: HubEvent[];
  projects: { id: number; name: string; description: string; tasks: HubTask[] }[];
  workers: { queued: number; failed: number; workers: { name: string; state: string }[] } | null;
};
export type AgentDef = { role: string; description: string; constraints: string[]; can_write: boolean; writable_globs: string[]; tool_policy: { allowed?: string[]; disallowed?: string[] } };
export type LogLine = { session_id: number; seq: number; stream: string; text: string };
export type Logs = { sessions: { id: number; role: string; status: string; task_id: number }[]; next_offset: number; lines: LogLine[] };

const TOKEN = "hivemind_token";
export const token = () => (typeof window === "undefined" ? "" : localStorage.getItem(TOKEN) || "");
export const setToken = (t: string | null) => (t ? localStorage.setItem(TOKEN, t) : localStorage.removeItem(TOKEN));

export async function api<T>(path: string, init: RequestInit & { json?: unknown } = {}): Promise<T> {
  const headers: Record<string, string> = { Authorization: `Bearer ${token()}` };
  if (init.json !== undefined) headers["Content-Type"] = "application/json";
  const res = await fetch(`/api${path}`, {
    ...init, headers, body: init.json !== undefined ? JSON.stringify(init.json) : init.body,
  });
  if (res.status === 401 && !path.startsWith("/auth")) {
    setToken(null);
    if (typeof window !== "undefined") window.location.href = "/login";
  }
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error(typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? res.statusText));
  }
  return res.json();
}

export const ACTIVE = ["queued", "planning", "assigned", "running", "validation"];
