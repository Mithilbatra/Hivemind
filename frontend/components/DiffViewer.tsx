"use client";
import { useMemo, useState } from "react";
import { Diff } from "@/lib/api";

function split(patch: string): { path: string; lines: string[] }[] {
  return patch.split(/^diff --git /m).filter(Boolean).map((chunk) => {
    const lines = chunk.split("\n");
    const m = lines[0].match(/ b\/(.+)$/);
    return { path: m ? m[1] : lines[0], lines };
  });
}

export default function DiffViewer({ diff }: { diff: Diff }) {
  const files = useMemo(() => (diff ? split(diff.patch) : []), [diff]);
  const [sel, setSel] = useState(0);
  if (!diff || !diff.files.length) return <p className="muted">No changes captured yet.</p>;
  const cur = files[Math.min(sel, files.length - 1)];
  const stat = (p: string) => diff.files.find((f) => f.path === p);
  return (
    <>
      <p className="small muted">{diff.files.length} files · <span style={{ color: "var(--ok)" }}>+{diff.additions}</span> <span style={{ color: "var(--bad)" }}>−{diff.deletions}</span>{diff.truncated ? " · truncated" : ""}</p>
      <div className="row" style={{ alignItems: "flex-start", flexWrap: "nowrap" }}>
        <div className="diff-files">
          {files.map((f, i) => (
            <div key={f.path} className={i === sel ? "on" : ""} onClick={() => setSel(i)}>
              {f.path} <span className="muted">+{stat(f.path)?.additions ?? 0} −{stat(f.path)?.deletions ?? 0}</span>
            </div>
          ))}
        </div>
        <div className="diff">
          {cur.lines.map((l, i) => (
            <div key={i} className={l.startsWith("@@") ? "hunk" : l.startsWith("+") && !l.startsWith("+++") ? "add" : l.startsWith("-") && !l.startsWith("---") ? "del" : ""}>{l || " "}</div>
          ))}
        </div>
      </div>
    </>
  );
}
