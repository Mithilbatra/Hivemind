import { AuditEvent } from "@/lib/api";

const TONE = (e: AuditEvent) =>
  /fail|dead_letter|violation|blocked|cancel/.test(e.type) ? "bad"
  : /conflict|retry|approval_required|no_validation|fallback/.test(e.type) ? "warn"
  : /committed|approval_decision|plan_created/.test(e.type) ? "ok" : "";

export default function Timeline({ events }: { events: AuditEvent[] }) {
  if (!events.length) return <p className="muted">No events yet.</p>;
  return (
    <ul className="timeline">
      {events.map((e) => (
        <li key={e.id} className={TONE(e)}>
          <div><b>{e.type}</b> <span className="muted small">{new Date(e.ts).toLocaleTimeString()} · {e.actor}{e.task_id ? ` · #${e.task_id}` : ""}</span></div>
          {e.message && <div className="small">{e.message}</div>}
        </li>
      ))}
    </ul>
  );
}
