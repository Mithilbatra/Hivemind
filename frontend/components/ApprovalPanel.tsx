"use client";
import { useState } from "react";
import { api, TaskDetail, Validation } from "@/lib/api";
import { RiskBadge } from "./ui";

export default function ApprovalPanel({ task, validation, filesChanged, onDone }: { task: TaskDetail; validation: Validation[]; filesChanged: number; onDone: () => void }) {
  const [reason, setReason] = useState("");
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const preRun = task.status === "blocked";
  const conflicts = task.worktree?.conflict_with ?? [];
  const last = Math.max(0, ...validation.map((v) => v.attempt));
  const lastRes = validation.filter((v) => v.attempt === last);
  const canForce = (task.status === "failed" && !!task.worktree?.active) || task.status === "waiting_for_review";

  async function act(action: string) {
    setBusy(true); setErr("");
    try { await api(`/tasks/${task.id}/approval`, { method: "POST", json: { action, reason, confirm } }); setReason(""); setConfirm(false); onDone(); }
    catch (e) { setErr((e as Error).message); } finally { setBusy(false); }
  }

  return (
    <div className="card" style={{ borderColor: "var(--warn)" }}>
      <h2>{preRun ? "Approval needed before this task runs" : task.status === "failed" ? "Validation failed — closed" : "Review before commit"}</h2>
      <div className="row" style={{ marginBottom: 8 }}>
        <RiskBadge score={task.risk_score} />
        {!preRun && <span className="badge">{filesChanged} files changed</span>}
        {!preRun && lastRes.length > 0 && <span className={`badge ${lastRes.every((r) => r.passed || !r.required) ? "ok" : "bad"}`}>validation {lastRes.every((r) => r.passed || !r.required) ? "passed" : "failed"}</span>}
      </div>
      <ul className="small">{task.risk_reasons.map((r) => <li key={r}>{r}</li>)}</ul>
      {conflicts.length > 0 && (
        <div className="banner warn">
          Merge conflict risk: overlapping edits with {conflicts.map((c) => `task #${c.task_id} (${c.files.join(", ")})`).join("; ")}
        </div>
      )}
      <div className="field">
        <label>Reason / feedback (required for request changes and force commit)</label>
        <textarea rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      {err && <div className="banner bad">{err}</div>}
      <div className="row">
        {task.status !== "failed" && <button className="primary" disabled={busy} onClick={() => act("approve")}>{preRun ? "Approve & run" : "Approve & commit"}</button>}
        {!preRun && task.status === "waiting_for_review" && <button disabled={busy || !reason.trim()} onClick={() => act("request_changes")}>Request changes</button>}
        {task.status !== "failed" && <button className="danger" disabled={busy} onClick={() => act("reject")}>Reject</button>}
        {!preRun && canForce && (
          <>
            <label style={{ margin: 0 }}><input type="checkbox" style={{ width: "auto", marginRight: 4 }} checked={confirm} onChange={(e) => setConfirm(e.target.checked)} />I understand this bypasses checks</label>
            <button className="danger" disabled={busy || !confirm || !reason.trim()} onClick={() => act("force_commit")}>Force commit</button>
          </>
        )}
      </div>
    </div>
  );
}
