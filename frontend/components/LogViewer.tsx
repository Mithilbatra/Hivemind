"use client";
import { useEffect, useRef, useState } from "react";
import { api, LogLine, Logs, request } from "@/lib/api";

export default function LogViewer({ taskId, live }: { taskId: number; live: boolean }) {
  const [session, setSession] = useState<number | "">("");
  const [lines, setLines] = useState<LogLine[]>([]);
  const [sessions, setSessions] = useState<Logs["sessions"]>([]);
  const offset = useRef(0);
  const box = useRef<HTMLDivElement>(null);
  const [err, setErr] = useState("");

  async function downloadFull(id: number) {  // the page shows the first part; the whole log is a file on the server
    setErr("");
    try {
      const a = document.createElement("a");
      a.href = URL.createObjectURL(await (await request(`/tasks/${taskId}/sessions/${id}/log`)).blob());
      a.download = `session-${id}.log`;
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10_000);  // revoking at once can cancel the download
    } catch (e) { setErr((e as Error).message); }
  }

  useEffect(() => { offset.current = 0; setLines([]); }, [session, taskId]);
  useEffect(() => {
    let stop = false;
    async function pull() {
      const q = new URLSearchParams({ offset: String(offset.current), limit: "1000" });
      if (session !== "") q.set("session_id", String(session));
      try {
        const r = await api<Logs>(`/tasks/${taskId}/logs?${q}`);
        if (stop) return;
        setSessions(r.sessions);
        if (r.lines.length) { offset.current = r.next_offset; setLines((l) => [...l, ...r.lines]); }
      } catch { /* shown as empty log */ }
    }
    pull();
    const id = live ? setInterval(pull, 2000) : undefined;
    return () => { stop = true; if (id) clearInterval(id); };
  }, [taskId, session, live]);
  useEffect(() => { box.current?.scrollTo(0, box.current.scrollHeight); }, [lines.length]);

  return (
    <>
      <div className="row" style={{ marginBottom: 8 }}>
        <select style={{ width: "auto" }} value={session} onChange={(e) => setSession(e.target.value ? Number(e.target.value) : "")}>
          <option value="">All sessions</option>
          {sessions.map((s) => <option key={s.id} value={s.id}>#{s.id} {s.role} ({s.status})</option>)}
        </select>
        <span className="muted small">{lines.length} lines{live ? " · live" : ""}</span>
        {session !== "" && <button onClick={() => downloadFull(session)}>Full log</button>}
      </div>
      {err && <div className="banner bad">{err}</div>}
      <div className="log mono" ref={box}>
        {lines.length ? lines.map((l, i) => (
          <div key={i} className={l.stream === "stderr" ? "err" : l.stream === "system" ? "sys" : ""}>
            {session === "" && <span style={{ opacity: .5 }}>[{l.session_id}] </span>}{l.text}
          </div>
        )) : <span className="muted">No output captured.</span>}
      </div>
    </>
  );
}
