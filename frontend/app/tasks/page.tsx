"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import TaskForm from "@/components/TaskForm";
import { RiskBadge, StatusBadge } from "@/components/ui";
import { ACTIVE, api, Project, Repo, Task } from "@/lib/api";
import { ago, usePoll } from "@/lib/hooks";

const STATUSES = ["queued", "planning", "assigned", "running", "validation", "blocked", "waiting_for_review", "completed", "failed", "cancelled"];

export default function TaskList() {
  const router = useRouter();
  const [status, setStatus] = useState("");
  const [repo, setRepo] = useState("");
  const [q, setQ] = useState("");
  const [showForm, setShowForm] = useState(false);
  const params = new URLSearchParams();
  if (status) params.set("status", status);
  if (repo) params.set("repo_id", repo);
  if (q) params.set("q", q);
  const { data: tasks, error, reload } = usePoll(() => api<Task[]>(`/tasks?${params}`), 3000, [status, repo, q]);
  const { data: repos, reload: reloadRepos } = usePoll(() => api<Repo[]>("/repos"), 0);
  const { data: projects } = usePoll(() => api<Project[]>("/projects"), 0);
  const repoName = (id: number) => repos?.find((r) => r.id === id)?.name ?? `#${id}`;
  const needs = (tasks ?? []).filter((t) => t.status === "waiting_for_review" || t.status === "blocked").length;
  const active = (tasks ?? []).filter((t) => ACTIVE.includes(t.status)).length;

  return (
    <>
      <div className="row" style={{ marginBottom: 14 }}>
        <h1 style={{ margin: 0, fontSize: 20 }}>Tasks</h1>
        <span className="muted">{active} active · {needs} awaiting you</span>
        <span style={{ flex: 1 }} />
        <button className="primary" onClick={() => setShowForm(!showForm)}>{showForm ? "Close" : "+ New task"}</button>
      </div>
      {showForm && projects && <TaskForm repos={repos ?? []} projects={projects} onCreated={(t) => { setShowForm(false); reload(); reloadRepos(); if (t.length === 1) router.push(`/tasks/${t[0].id}`); }} />}
      <div className="card">
        <div className="row" style={{ marginBottom: 10 }}>
          <input style={{ maxWidth: 260 }} placeholder="Search title or description" value={q} onChange={(e) => setQ(e.target.value)} />
          <select style={{ width: "auto" }} value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>{STATUSES.map((s) => <option key={s} value={s}>{s.replace(/_/g, " ")}</option>)}
          </select>
          <select style={{ width: "auto" }} value={repo} onChange={(e) => setRepo(e.target.value)}>
            <option value="">All repositories</option>{(repos ?? []).map((r) => <option key={r.id} value={r.id}>{r.name}</option>)}
          </select>
        </div>
        {error && <div className="banner bad">{error}</div>}
        <table>
          <thead><tr><th>#</th><th>Title</th><th>Repo</th><th>Status</th><th>Risk</th><th>Priority</th><th>Updated</th></tr></thead>
          <tbody>
            {(tasks ?? []).map((t) => (
              <tr key={t.id} className="click" onClick={() => router.push(`/tasks/${t.id}`)}>
                <td>{t.id}</td>
                <td><b>{t.title}</b>{t.dead_letter && <span className="badge bad" style={{ marginLeft: 8 }}>dead-letter</span>}</td>
                <td>{repoName(t.repo_id)}</td><td><StatusBadge status={t.status} /></td>
                <td><RiskBadge score={t.risk_score} /></td><td>{t.priority}</td><td className="muted">{ago(t.updated_at)}</td>
              </tr>
            ))}
            {tasks && !tasks.length && <tr><td colSpan={7} className="muted">No tasks yet. Create one to get started.</td></tr>}
          </tbody>
        </table>
      </div>
    </>
  );
}
