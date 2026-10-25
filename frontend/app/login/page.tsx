"use client";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { LogoMark } from "@/components/Logo";
import { api, setToken } from "@/lib/api";

export default function Login() {
  const router = useRouter();
  const [u, setU] = useState("admin");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  async function submit(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await api<{ token: string }>("/auth/login", { method: "POST", json: { username: u, password: p } });
      setToken(r.token);
      router.replace("/");
    } catch (e) { setErr((e as Error).message); }
  }
  return (
    <form onSubmit={submit}>
      <div className="brandblock">
        <LogoMark size={72} />
        <h1>Hive<b>mind</b></h1>
        <div className="muted">Many agents, one mind.</div>
      </div>
      <div className="card">
      <div className="field"><label>Username</label><input value={u} onChange={(e) => setU(e.target.value)} autoFocus /></div>
      <div className="field"><label>Password</label><input type="password" value={p} onChange={(e) => setP(e.target.value)} /></div>
      {err && <div className="banner bad">{err}</div>}
      <button className="primary" style={{ width: "100%" }}>Sign in</button>
      </div>
    </form>
  );
}
