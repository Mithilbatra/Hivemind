"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import HiveMap from "@/components/HiveMap";
import TaskForm from "@/components/TaskForm";
import { StatusBadge } from "@/components/ui";
import { Hub, HubAgent, HubEvent, HubSession, HubTask, Project, Repo, api } from "@/lib/api";
import { ago, usePoll } from "@/lib/hooks";

const COLUMNS: { title: string; statuses: string[]; cap?: number }[] = [
  { title: "Queued", statuses: ["queued", "planning", "assigned"] },
  { title: "Agents working", statuses: ["running", "validation"] },
  { title: "Needs you", statuses: ["blocked", "waiting_for_review"] },
  { title: "Done", statuses: ["completed"], cap: 8 },
  { title: "Stopped", statuses: ["failed", "cancelled"], cap: 8 },
];

const roleVar = (role: string) => ({ "--role": `var(--r-${role}, var(--accent))` }) as React.CSSProperties;

function useNow(ms = 1000) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => { const id = setInterval(() => setNow(Date.now()), ms); return () => clearInterval(id); }, [ms]);
  return now;
}

function elapsed(from: string, now: number) {
  const s = Math.max(0, Math.floor((now - new Date(from).getTime()) / 1000));
  return s < 60 ? `${s}s` : s < 3600 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
}

function Role({ role }: { role: string }) {
  return <span className="role" style={roleVar(role)}>{role}</span>;
}

function Job({ s, now }: { s: HubSession; now: number }) {
  return (
    <div className="job">
      <div className="small">
        <Link href={`/tasks/${s.top_task_id}`}><b>#{s.top_task_id} {s.top_title}</b></Link>
        {s.task_title && s.task_title !== s.top_title && <span className="muted"> → {s.task_title}</span>}
      </div>
      <div className="small muted">
        {s.kind && s.kind !== "task" ? `${s.kind} · ` : ""}{s.attempt > 1 ? `attempt ${s.attempt} · ` : ""}{s.repo} · {elapsed(s.started_at, now)}
      </div>
      <div className="doing" title={s.doing}>{s.doing || "starting…"}</div>
    </div>
  );
}

function AgentCard({ a, now }: { a: HubAgent; now: number }) {
  const busy = a.active.length > 0;
  const done = a.stats.succeeded ?? 0;
  const failed = (a.stats.failed ?? 0) + (a.stats.timeout ?? 0) + (a.stats.abandoned ?? 0);
  const last = a.recent[0];
  return (
    <div id={`agent-${a.role}`} className={`agent${busy ? " busy" : ""}`} style={roleVar(a.role)}>
      <h3>
        <span className={`dot${busy ? " on" : ""}`} aria-hidden />
        {a.role}
        <span className="badge" style={{ marginLeft: "auto", textTransform: "none" }}>
          {a.can_write ? (a.test_files_only ? "test files only" : "can edit") : "read-only"}
        </span>
      </h3>
      <div className="small muted" style={{ margin: "4px 0" }}>{a.description}</div>
      <div className="small"><b>{busy ? `Working on ${a.active.length} task${a.active.length > 1 ? "s" : ""}` : "Idle"}</b></div>
      {a.active.map((s) => <Job key={s.id} s={s} now={now} />)}
      {!busy && last && (
        <div className="small muted" style={{ marginTop: 6 }}>
          Last: <StatusBadge status={last.status} />{" "}
          <Link href={`/tasks/${last.top_task_id}`}>#{last.top_task_id} {last.task_title || last.top_title}</Link> · {ago(last.ended_at)}
        </div>
      )}
      <div className="small muted" style={{ marginTop: 8 }}>{done} sessions done · {failed} failed</div>
    </div>
  );
}

function TaskCard({ t }: { t: HubTask }) {
  const pct = t.progress.total ? Math.round((100 * t.progress.done) / t.progress.total) : 0;
  return (
    <Link href={`/tasks/${t.id}`} className="tcard">
      <div className="small"><b>{t.title}</b></div>
      <div className="small muted">#{t.id} · {t.repo}</div>
      {t.progress.total > 0
        ? <><div className="bar" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}><i style={{ width: `${pct}%` }} /></div>
          <div className="small muted">{t.progress.done}/{t.progress.total} steps</div></>
        : <div className="small muted">not planned yet</div>}
      <div className="row" style={{ gap: 6, marginTop: 4 }}>
        {t.agent && <><Role role={t.agent.role} /><span className="small muted">{t.agent.subtask}</span></>}
        {!t.agent && <StatusBadge status={t.status} />}
        {t.conflict && <span className="badge warn" title="Another task edits the same files">overlap</span>}
        {t.dead_letter && <span className="badge bad">dead-letter</span>}
      </div>
    </Link>
  );
}

function ProjectBoard({ p, repos, projects, onCreated }: {
  p: Hub["projects"][number]; repos: Repo[]; projects: Project[]; onCreated: () => void;
}) {
  const [adding, setAdding] = useState(false);
  const done = p.tasks.filter((t) => t.status === "completed").length;
  return (
    <section className="card">
      <div className="row">
        <h2 style={{ margin: 0 }}>{p.name}</h2>
        <span className="muted small">{done}/{p.tasks.length} tasks done</span>
        <span style={{ flex: 1 }} />
        <button onClick={() => setAdding(!adding)}>{adding ? "Close" : "+ Task"}</button>
      </div>
      {p.description && <p className="small" style={{ margin: "6px 0 10px", whiteSpace: "pre-wrap" }}><b>Goal:</b> {p.description}</p>}
      {adding && <TaskForm repos={repos} projects={projects} projectId={p.id} onCreated={() => { setAdding(false); onCreated(); }} />}
      {p.tasks.length === 0
        ? <p className="muted small" style={{ marginTop: 10 }}>No tasks yet. Add one and the hive picks it up.</p>
        : (
          <div className="board" style={{ marginTop: 10 }}>
            {COLUMNS.map((c) => {
              const ts = p.tasks.filter((t) => c.statuses.includes(t.status));
              const shown = c.cap ? ts.slice(0, c.cap) : ts;
              return (
                <div key={c.title} className="col">
                  <h4>{c.title} · {ts.length}</h4>
                  {shown.map((t) => <TaskCard key={t.id} t={t} />)}
                  {ts.length > shown.length && <Link className="small" href="/tasks">+{ts.length - shown.length} more</Link>}
                </div>
              );
            })}
          </div>
        )}
    </section>
  );
}

function Feed({ events }: { events: HubEvent[] }) {
  return (
    <ul className="feed">
      {events.map((e) => {
        const [kind, who] = e.actor.includes(":") ? e.actor.split(":", 2) : ["system", "system"];
        return (
          <li key={e.id}>
            <div className="row" style={{ gap: 6 }}>
              {kind === "agent" ? <Role role={who} /> : <span className="badge">{kind === "user" ? who : "system"}</span>}
              <b>{e.type.replace(/_/g, " ")}</b>
              <span className="muted small" style={{ marginLeft: "auto" }}>{ago(e.ts)}</span>
            </div>
            {e.message && <div className="small">{e.message}</div>}
            {e.top_task_id && <Link className="small" href={`/tasks/${e.top_task_id}`}>#{e.top_task_id} {e.task_title}</Link>}
          </li>
        );
      })}
      {!events.length && <li className="muted">Nothing has happened yet.</li>}
    </ul>
  );
}

function NewProject({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState("");
  const [goal, setGoal] = useState("");
  const [err, setErr] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try { await api("/projects", { method: "POST", json: { name, description: goal } }); onDone(); }
    catch (e) { setErr((e as Error).message); }
  }
  return (
    <form className="card" onSubmit={submit}>
      <h2>New project</h2>
      <p className="small muted" style={{ marginTop: 0 }}>A project is one big goal for the hive. Split it into tasks; every agent working on a task sees the goal and what the other agents are editing.</p>
      <div className="field"><label>Name</label><input required value={name} onChange={(e) => setName(e.target.value)} placeholder="Booking app" /></div>
      <div className="field"><label>Goal</label><textarea rows={3} value={goal} onChange={(e) => setGoal(e.target.value)} placeholder="A booking app with accounts, a calendar view and payments; all tests pass." /></div>
      {err && <div className="banner bad">{err}</div>}
      <button className="primary">Create project</button>
    </form>
  );
}

export default function Hive() {
  const now = useNow();
  const [creating, setCreating] = useState(false);
  const { data: hub, error, reload } = usePoll(() => api<Hub>("/hub"), 2000);
  const { data: repos, reload: reloadRepos } = usePoll(() => api<Repo[]>("/repos"), 0);
  const { data: projects, reload: reloadProjects } = usePoll(() => api<Project[]>("/projects"), 0);
  if (error && !hub) return <div className="banner bad">{error}</div>;
  if (!hub) return <p className="muted">Loading…</p>;

  const tasks = hub.projects.flatMap((p) => p.tasks);
  const inFlight = tasks.filter((t) => ["queued", "planning", "assigned", "running", "validation"].includes(t.status)).length;
  const needsYou = tasks.filter((t) => t.status === "blocked" || t.status === "waiting_for_review").length;
  const done = tasks.filter((t) => t.status === "completed").length;

  return (
    <>
      <section className="hero comb">
        <HiveMap agents={hub.agents} needsYou={needsYou} />
        <div>
          <h1>The Hive</h1>
          <p className="tag">Many agents, one mind. Live, refreshed every 2 s.</p>
          <div className="hstats">
            <div><b>{inFlight}</b><span className="small muted">tasks in flight</span></div>
            <div><b style={{ color: needsYou ? "var(--accent)" : undefined }}>{needsYou}</b><span className="small muted">waiting for you</span></div>
            <div><b>{done}</b><span className="small muted">tasks done</span></div>
            <div><b>{hub.workers?.workers.length ?? "–"}</b><span className="small muted">{hub.workers ? `workers · ${hub.workers.queued} queued` : "queue unavailable"}</span></div>
          </div>
          <button className="primary" onClick={() => setCreating(!creating)}>{creating ? "Close" : "+ New project"}</button>
        </div>
      </section>
      {creating && <NewProject onDone={() => { setCreating(false); reload(); reloadProjects(); }} />}
      <div className="hub">
        <div>
          <h2 className="section-title">Agents</h2>
          <div className="agents">{hub.agents.map((a) => <AgentCard key={a.role} a={a} now={now} />)}</div>
          <h2 className="section-title" id="boards">Projects</h2>
          {hub.projects.map((p) => (
            <ProjectBoard key={p.id} p={p} repos={repos ?? []} projects={projects ?? []} onCreated={() => { reload(); reloadRepos(); }} />
          ))}
        </div>
        <aside className="card">
          <h2>Hive activity</h2>
          <Feed events={hub.events} />
        </aside>
      </div>
    </>
  );
}
