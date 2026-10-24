const TONE: Record<string, string> = {
  completed: "ok", succeeded: "ok", approved: "ok", passed: "ok",
  failed: "bad", cancelled: "bad", rejected: "bad", timeout: "bad",
  blocked: "warn", waiting_for_review: "warn", pending: "warn", changes_requested: "warn", forced: "warn",
  running: "info", validation: "info", planning: "info", assigned: "info",
};

export function StatusBadge({ status }: { status: string }) {
  return <span className={`badge ${TONE[status] ?? ""}`}>{status.replace(/_/g, " ")}</span>;
}

export function RiskBadge({ score }: { score: number }) {
  const tone = score >= 60 ? "bad" : score >= 35 ? "warn" : "ok";
  return <span className={`badge ${tone}`} title="risk score 0-100">risk {Math.round(score)}</span>;
}

export function Tabs<T extends string>({ tabs, value, onChange }: { tabs: T[]; value: T; onChange: (t: T) => void }) {
  return (
    <div className="tabs">
      {tabs.map((t) => (
        <button key={t} className={t === value ? "on" : ""} onClick={() => onChange(t)}>{t}</button>
      ))}
    </div>
  );
}
