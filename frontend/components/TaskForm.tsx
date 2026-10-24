"use client";
import { useState } from "react";
import { api, Project, Repo, Task } from "@/lib/api";

type Props = { repos: Repo[]; projects: Project[]; projectId?: number; onCreated: (t: Task[]) => void };

export default function TaskForm({ repos, projects, projectId, onCreated }: Props) {
  const [f, setF] = useState({ title: "", description: "", acceptance_criteria: "", branch_target: "", issue_ref: "", priority: "normal", risk_level: "low" });
  const [project, setProject] = useState<number | "">(projectId ?? projects[0]?.id ?? "");
  const [selected, setSelected] = useState<number[]>([]);
  const [newPath, setNewPath] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const set = (k: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>) => setF({ ...f, [k]: e.target.value });

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const body = { ...f, project_id: project || null, branch_target: f.branch_target || null, issue_ref: f.issue_ref || null, repo_ids: selected, repo_paths: newPath ? [newPath] : [] };
      onCreated(await api<Task[]>("/tasks", { method: "POST", json: body }));
      setF({ ...f, title: "", description: "", acceptance_criteria: "" });
      setNewPath("");
    } catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <form className="card" onSubmit={submit}>
      <h2>New task</h2>
      {projects.length > 1 && (
        <div className="field"><label>Project (agents on the same project see each other&apos;s work)</label>
          <select value={project} onChange={(e) => setProject(Number(e.target.value))}>
            {projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
          </select></div>
      )}
      <div className="field"><label>Title</label><input required value={f.title} onChange={set("title")} placeholder="Add OAuth login and make sure tests pass" /></div>
      <div className="field"><label>Description</label><textarea rows={3} value={f.description} onChange={set("description")} /></div>
      <div className="field"><label>Acceptance criteria (optional)</label><textarea rows={2} value={f.acceptance_criteria} onChange={set("acceptance_criteria")} /></div>
      <div className="field">
        <label>Repositories (select one or more)</label>
        <div className="row">
          {repos.map((r) => (
            <label key={r.id} style={{ fontWeight: 400, color: "inherit" }}>
              <input type="checkbox" style={{ width: "auto", marginRight: 6 }} checked={selected.includes(r.id)}
                onChange={(e) => setSelected(e.target.checked ? [...selected, r.id] : selected.filter((i) => i !== r.id))} />
              {r.name}
            </label>
          ))}
          {!repos.length && <span className="muted small">No repositories registered yet; add a path below.</span>}
        </div>
        <input style={{ marginTop: 6 }} value={newPath} onChange={(e) => setNewPath(e.target.value)} placeholder="…or a repo path under the allowed roots, e.g. /repos/my-app" />
      </div>
      <div className="grid2">
        <div className="field"><label>Branch target</label><input value={f.branch_target} onChange={set("branch_target")} placeholder="default branch" /></div>
        <div className="field"><label>Issue / PR</label><input value={f.issue_ref} onChange={set("issue_ref")} placeholder="#123" /></div>
        <div className="field"><label>Priority</label>
          <select value={f.priority} onChange={set("priority")}>{["low", "normal", "high", "urgent"].map((p) => <option key={p}>{p}</option>)}</select></div>
        <div className="field"><label>Risk level (high/critical need approval before running)</label>
          <select value={f.risk_level} onChange={set("risk_level")}>{["low", "medium", "high", "critical"].map((p) => <option key={p}>{p}</option>)}</select></div>
      </div>
      {err && <div className="banner bad">{err}</div>}
      <button className="primary" disabled={busy || (!selected.length && !newPath)}>{busy ? "Creating…" : "Create task"}</button>
    </form>
  );
}
