import type { ValidationCommand } from "./api";

/** Split a unified diff into one entry per file, keyed by the path after "b/". */
export function splitDiff(patch: string): { path: string; lines: string[] }[] {
  return patch.split(/^diff --git /m).filter(Boolean).map((chunk) => {
    const lines = chunk.split("\n");
    const m = lines[0].match(/ b\/(.+)$/);
    return { path: m ? m[1] : lines[0], lines };
  });
}

/** Validation commands as editable text: one "kind: command" per line. */
export const checksToText = (v: ValidationCommand[]) => v.map((c) => `${c.kind}: ${c.command}`).join("\n");

export const checksFromText = (s: string): ValidationCommand[] =>
  s.split("\n").map((l) => l.trim()).filter(Boolean).map((l) => {
    const i = l.indexOf(":");
    return i > 0 && !l.slice(0, i).includes(" ") ? { kind: l.slice(0, i), command: l.slice(i + 1).trim(), required: true } : { kind: "test", command: l, required: true };
  });
