"use client";
import { useState } from "react";
import { AgentDef, Project, Repo, Task, api } from "@/lib/api";
import { StatusBadge } from "@/components/ui";
import { usePoll } from "@/lib/hooks";
import { checksFromText, checksToText } from "@/lib/parse";

function ProjectEditor({ p, onSaved }: { p: Project; onSaved: () => void }) {
  const [cmds, setCmds] = useState(checksToText(p.validation_commands));
  const [goal, setGoal] = useState(p.description);
  const [roots, setRoots] = useState(p.allowed_roots.join("\n"));
  const [retries, setRetries] = useState(p.max_retries);
  const [msg, setMsg] = useState("");
  async function save() {
    // Keep flags the text form can't show (required, timeout) for commands that were already there.
    const prev = new Map(p.validation_commands.map((c) => [c.command, c]));
    const validation_commands = checksFromText(cmds).map((c) => ({ ...c, ...prev.get(c.command), kind: c.kind }));
    try {
      await api(`/projects/${p.id}`, { method: "PUT", json: { name: p.name, description: goal, allowed_roots: roots.split("\n").map((s) => s.trim()).filter(Boolean), validation_commands, max_retries: retries } });
      setMsg("Saved"); onSaved();
    } catch (e) { setMsg((e as Error).message); }
  }
  return (
    <div className="card">
      <h3>Project: {p.name}</h3>
      <div className="field"><label>Goal (shown to every agent working on this project)</label>
        <textarea rows={2} value={goal} onChange={(e) => setGoal(e.target.value)} /></div>
      <div className="grid2">
        <div className="field"><label>Validation commands (one per line, “kind: command”). Empty = auto-detect from manifests.</label>
          <textarea rows={5} className="mono" value={cmds} onChange={(e) => setCmds(e.target.value)} placeholder={"test: python -m pytest -q\nlint: ruff check ."} /></div>
        <div>
          <div className="field"><label>Extra allowed repo roots (optional, must be inside the server’s allowed roots)</label>
            <textarea rows={2} className="mono" value={roots} onChange={(e) => setRoots(e.target.value)} /></div>
          <div className="field"><label>Max validation retries</label>
            <input type="number" min={0} max={10} value={retries} onChange={(e) => setRetries(Number(e.target.value))} /></div>
        </div>
      </div>
      <div className="row"><button className="primary" onClick={save}>Save</button><span className="muted small">{msg}</span></div>
    </div>
  );
}

export default function Settings() {
  const { data: projects, reload } = usePoll(() => api<Project[]>("/projects"), 0);
  const { data: repos, reload: reloadRepos } = usePoll(() => api<Repo[]>("/repos"), 0);
  const { data: agents } = usePoll(() => api<AgentDef[]>("/agents"), 0);
  const { data: dead, reload: reloadDead } = usePoll(() => api<Task[]>("/dead-letter"), 5000);
  const { data: rt } = usePoll(() => api<Record<string, unknown>>("/settings"), 0);
  const [path, setPath] = useState("");
  const [err, setErr] = useState("");
  const [deadErr, setDeadErr] = useState("");
  async function addRepo() {
    setErr("");
    try { await api("/repos", { method: "POST", json: { path } }); setPath(""); reloadRepos(); } catch (e) { setErr((e as Error).message); }
  }
  async function retry(id: number) {
    setDeadErr("");
    try { await api(`/tasks/${id}/retry`, { method: "POST" }); } catch (e) { setDeadErr(`#${id}: ${(e as Error).message}`); }
    reloadDead();
  }
  return (
    <>
      <h1 style={{ fontSize: 20 }}>Settings</h1>
      {(projects ?? []).map((p) => <ProjectEditor key={p.id} p={p} onSaved={reload} />)}
      <div className="card">
        <h3>Repositories</h3>
        <table><tbody>{(repos ?? []).map((r) => <tr key={r.id}><td><b>{r.name}</b></td><td className="mono">{r.path}</td><td className="muted">{r.default_branch}</td></tr>)}</tbody></table>
        <div className="row" style={{ marginTop: 10 }}>
          <input style={{ flex: 1 }} value={path} onChange={(e) => setPath(e.target.value)} placeholder="/repos/my-app (must be a git repo under the allowed roots)" />
          <button onClick={addRepo} disabled={!path}>Add repository</button>
        </div>
        {err && <div className="banner bad" style={{ marginTop: 8 }}>{err}</div>}
      </div>
      <div className="card">
        <h3>Dead-letter tasks</h3>
        {deadErr && <div className="banner bad">{deadErr}</div>}
        {dead && !dead.length && <p className="muted">None.</p>}
        <table><tbody>{(dead ?? []).map((t) => (
          <tr key={t.id}><td>#{t.id}</td><td>{t.title}</td><td><StatusBadge status={t.status} /></td><td className="small">{t.error}</td><td><button onClick={() => retry(t.id)}>Retry</button></td></tr>
        ))}</tbody></table>
      </div>
      <div className="card">
        <h3>Agents</h3>
        <table><thead><tr><th>Role</th><th>Access</th><th>Description</th></tr></thead>
          <tbody>{(agents ?? []).map((a) => <tr key={a.role}><td><b>{a.role}</b></td><td>{a.can_write ? (a.writable_globs.length ? "tests only" : "read/write") : "read-only"}</td><td>{a.description}</td></tr>)}</tbody></table>
      </div>
      <div className="card"><h3>Runtime (set via environment)</h3><pre>{rt ? JSON.stringify(rt, null, 2) : "…"}</pre></div>
    </>
  );
}
