"use client";
import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { Wordmark } from "@/components/Logo";
import { api, setToken, token } from "@/lib/api";
import { usePoll } from "@/lib/hooks";

type Workers = { queued: number; failed: number; workers: { name: string; state: string }[] };

export default function Shell({ children }: { children: React.ReactNode }) {
  const path = usePathname();
  const router = useRouter();
  const [ready, setReady] = useState(false);
  const login = path === "/login";
  useEffect(() => {
    if (!login && !token()) router.replace("/login");
    else setReady(true);
  }, [login, router, path]);
  // Both need a login: on the login page a 401 would redirect to the login page, again and again.
  const { data: w } = usePoll(() => (login ? Promise.resolve(null) : api<Workers>("/health/workers")), login ? 0 : 10000, [login]);
  const { data: rt } = usePoll(() => (login ? Promise.resolve(null) : api<{ agent_mode: string }>("/settings")), 0, [login]);
  if (login) return <main className="login comb">{children}</main>;
  if (!ready) return null;
  return (
    <>
      <header className="top">
        <Link href="/" className="brand" aria-label="Hivemind home"><Wordmark /></Link>
        {([["/", "Hive"], ["/tasks", "Tasks"], ["/settings", "Settings"]] as const).map(([href, label]) => {
          const on = href === "/" ? path === "/" : path.startsWith(href);
          return <Link key={href} href={href} className={on ? "on" : ""} aria-current={on ? "page" : undefined}>{label}</Link>;
        })}
        <span className="sp" />
        <span className="small muted qstat" title="queued jobs / failed jobs / workers online">
          {w ? `queue ${w.queued} · failed ${w.failed} · workers ${w.workers.length}` : "queue unavailable"}
        </span>
        <button onClick={() => { setToken(null); router.replace("/login"); }}>Sign out</button>
      </header>
      {rt?.agent_mode === "mock" && (
        <div className="demo" role="status">
          Demo agent (AGENT_MODE=mock): agents only write placeholder files. Set AGENT_MODE=cli and AGENT_COMMAND for real work.
        </div>
      )}
      <main>{children}</main>
    </>
  );
}
