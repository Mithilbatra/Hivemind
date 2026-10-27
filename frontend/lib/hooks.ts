"use client";
import { useCallback, useEffect, useRef, useState } from "react";

/** Load on mount, then every `ms` (or never if ms is 0). Errors are kept, not thrown. */
export function usePoll<T>(fn: () => Promise<T>, ms: number, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fnRef = useRef(fn);
  fnRef.current = fn;
  const reload = useCallback(async () => {
    try { setData(await fnRef.current()); setError(null); } catch (e) { setError((e as Error).message); }
  }, []);
  useEffect(() => {
    reload();
    if (!ms) return;
    const id = setInterval(() => { if (!document.hidden) reload(); }, ms);  // a background tab costs nothing
    return () => clearInterval(id);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ms, reload, ...deps]);
  return { data, error, reload };
}

export function ago(iso: string | null): string {
  if (!iso) return "–";
  const s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
  if (s < 60) return `${Math.floor(s)}s ago`;
  if (s < 3600) return `${Math.floor(s / 60)}m ago`;
  if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
  return new Date(iso).toLocaleDateString();
}
