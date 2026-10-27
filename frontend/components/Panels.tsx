"use client";
import { AgentDef, Child, Session, Validation } from "@/lib/api";
import { StatusBadge } from "./ui";

export function DagView({ steps }: { steps: Child[] }) {
  const name = (id: number) => steps.find((s) => s.id === id)?.title ?? `#${id}`;
  if (!steps.length) return <p className="muted">Not planned yet.</p>;
  return (
    <div className="dag">
      {steps.map((s) => (
        <div key={s.id} className="step">
          <span className="muted small" style={{ width: 22 }}>{s.position + 1}</span>
          <div style={{ flex: 1 }}>
            <b>{s.title}</b> <span className="badge">{s.role}</span>
            <div className="small muted">{s.depends_on.length ? `after: ${s.depends_on.map(name).join(", ")}` : "no prerequisites"}{s.retry_count ? ` · ${s.retry_count} retries` : ""}</div>
            {s.error && <div className="small" style={{ color: "var(--bad)" }}>{s.error}</div>}
          </div>
          <StatusBadge status={s.status} />
        </div>
      ))}
    </div>
  );
}

export function ValidationPanel({ results }: { results: Validation[] }) {
  if (!results.length) return <p className="muted">No validation has run yet.</p>;
  const attempts = Array.from(new Set(results.map((r) => r.attempt)));
  return (
    <>
      {attempts.map((a) => (
        <div key={a} className="card">
          <h3>Attempt {a}</h3>
          {results.filter((r) => r.attempt === a).map((r) => (
            <details key={r.id} open={!r.passed}>
              <summary>
                <StatusBadge status={r.passed ? "passed" : "failed"} /> <b>{r.kind}</b> <code>{r.command}</code>
                <span className="muted small"> · exit {r.exit_code ?? "–"} · {r.duration_s}s{r.required ? "" : " · optional"}</span>
              </summary>
              <pre>{r.output_tail || "(no output)"}</pre>
            </details>
          ))}
        </div>
      ))}
    </>
  );
}

export function AgentSummary({ sessions, agents }: { sessions: Session[]; agents: AgentDef[] }) {
  return (
    <>
      <table>
        <thead><tr><th>#</th><th>Agent</th><th>Attempt</th><th>Status</th><th>Files changed</th><th>Tools</th><th>Summary</th></tr></thead>
        <tbody>
          {sessions.map((s) => (
            <tr key={s.id}>
              <td>{s.id}</td>
              <td>{s.agent_role}{agents.find((a) => a.role === s.agent_role)?.can_write ? "" : " (read-only)"}</td>
              <td>{s.attempt}</td><td><StatusBadge status={s.status} /></td>
              <td>{s.diff_summary.changed?.length ?? 0}</td><td>{s.tool_calls.length}</td>
              <td className="small">
                <details><summary>{s.summary.slice(0, 90) || "(no summary)"}</summary>
                  <pre>{s.summary}{"\n"}{s.tool_calls.map((t) => `${t.name} ${t.input}`).join("\n")}</pre></details>
              </td>
            </tr>
          ))}
          {!sessions.length && <tr><td colSpan={7} className="muted">No sessions yet.</td></tr>}
        </tbody>
      </table>
      <h3 style={{ marginTop: 16 }}>Agent roles</h3>
      <div className="grid2">
        {agents.map((a) => (
          <div key={a.role} className="card" style={{ marginBottom: 0 }}>
            <b>{a.role}</b> <span className="badge">{a.can_write ? "can write" : "read-only"}</span>
            <div className="small muted">{a.description}</div>
            <ul className="small">{a.constraints.map((c) => <li key={c}>{c}</li>)}</ul>
          </div>
        ))}
      </div>
    </>
  );
}
