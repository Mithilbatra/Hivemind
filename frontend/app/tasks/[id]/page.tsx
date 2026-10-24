"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useState } from "react";
import ApprovalPanel from "@/components/ApprovalPanel";
import DiffViewer from "@/components/DiffViewer";
import LogViewer from "@/components/LogViewer";
import { AgentSummary, DagView, ValidationPanel } from "@/components/Panels";
import Timeline from "@/components/Timeline";
import { RiskBadge, StatusBadge, Tabs } from "@/components/ui";
import { ACTIVE, AgentDef, AuditEvent, Diff, TaskDetail, Validation, api } from "@/lib/api";
import { ago, usePoll } from "@/lib/hooks";

const TABS = ["overview", "timeline", "validation", "logs", "diff", "agents"] as const;

export default function TaskPage() {
  const id = Number(useParams().id);
  const [tab, setTab] = useState<(typeof TABS)[number]>("overview");
  const [actionErr, setActionErr] = useState("");
  const { data: t, error, reload } = usePoll(() => api<TaskDetail>(`/tasks/${id}`), 2000, [id]);
  const live = !!t && ACTIVE.includes(t.status);
  const { data: events } = usePoll(() => api<AuditEvent[]>(`/tasks/${id}/events`), tab === "timeline" || live ? 3000 : 0, [id, tab, t?.status]);
  const { data: validation } = usePoll(() => api<Validation[]>(`/tasks/${id}/validation`), 3000, [id, t?.status]);
  const { data: diff } = usePoll(() => api<Diff>(`/tasks/${id}/diff`), 3000, [id, t?.status]);
  const { data: agents } = usePoll(() => api<AgentDef[]>("/agents"), 0);

  async function act(path: string) {
    setActionErr("");
    try { await api(`/tasks/${id}/${path}`, { method: "POST" }); reload(); } catch (e) { setActionErr((e as Error).message); }
  }
  if (error && !t) return <div className="banner bad">{error}</div>;
  if (!t) return <p className="muted">Loading…</p>;
  const conflicts = t.worktree?.conflict_with ?? [];
  // A task put on hold by hand is also "blocked" but has no pending approval; it needs Run, not Approve.
  const preRunPending = t.approvals.some((a) => a.kind === "pre_run" && a.status === "pending");
  const needsDecision = t.status === "waiting_for_review" || (t.status === "blocked" && preRunPending) || (t.status === "failed" && !!t.worktree?.active);

  return (
    <>
      <p className="small"><Link href="/tasks">← Tasks</Link> · <Link href="/">Agent hub</Link></p>
      <div className="row" style={{ marginBottom: 6 }}>
        <h1 style={{ margin: 0, fontSize: 20 }}>#{t.id} {t.title}</h1>
        <StatusBadge status={t.status} /><RiskBadge score={t.risk_score} />
        {t.dead_letter && <span className="badge bad">dead-letter</span>}
        <span style={{ flex: 1 }} />
        {["queued", "blocked"].includes(t.status) && <button onClick={() => act("run")}>Run</button>}
        {["failed", "cancelled"].includes(t.status) && <button className="primary" onClick={() => act("retry")}>Retry</button>}
        {!["completed", "failed", "cancelled"].includes(t.status) && <button className="danger" onClick={() => act("cancel")}>Cancel</button>}
      </div>
      <p className="muted small">
        {t.repo_name} · {t.worktree ? `branch ${t.worktree.branch}${t.worktree.commit_sha ? ` @ ${t.worktree.commit_sha.slice(0, 8)}` : ""}` : "no branch yet"} · priority {t.priority} · created {ago(t.created_at)} by {t.created_by}
        {t.issue_ref ? ` · ${t.issue_ref}` : ""} · retries {t.retry_count}/{t.max_retries}
      </p>
      {actionErr && <div className="banner bad">{actionErr}</div>}
      {t.error && <div className="banner bad">{t.error}</div>}
      {conflicts.length > 0 && !needsDecision && (
        <div className="banner warn">Overlapping edits with {conflicts.map((c) => `task #${c.task_id} (${c.files.join(", ")})`).join("; ")}. Expect a merge conflict.</div>
      )}
      {needsDecision && <ApprovalPanel task={t} validation={validation ?? []} filesChanged={diff?.files.length ?? 0} onDone={reload} />}
      <Tabs tabs={[...TABS]} value={tab} onChange={setTab} />
      {tab === "overview" && (
        <>
          <div className="card"><h3>Description</h3><p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{t.description || <span className="muted">(none)</span>}</p>
            {t.acceptance_criteria && <><h3 style={{ marginTop: 12 }}>Acceptance criteria</h3><p style={{ whiteSpace: "pre-wrap", margin: 0 }}>{t.acceptance_criteria}</p></>}</div>
          <div className="card"><h3>Plan ({t.children.length} subtasks)</h3><DagView steps={t.children} /></div>
          {t.summary && <div className="card"><h3>Summary</h3><pre>{t.summary}</pre></div>}
          {t.approvals.length > 0 && (
            <div className="card"><h3>Approvals</h3>
              <table><tbody>{t.approvals.map((a) => (
                <tr key={a.id}><td>{a.kind}</td><td><StatusBadge status={a.status} /></td><td>{a.decided_by ?? "–"}</td><td className="muted">{a.reason}</td></tr>
              ))}</tbody></table></div>
          )}
        </>
      )}
      {tab === "timeline" && <div className="card"><Timeline events={events ?? []} /></div>}
      {tab === "validation" && <ValidationPanel results={validation ?? []} />}
      {tab === "logs" && <LogViewer taskId={id} live={live} />}
      {tab === "diff" && <DiffViewer diff={diff ?? null} />}
      {tab === "agents" && <AgentSummary sessions={t.sessions} agents={agents ?? []} />}
    </>
  );
}
