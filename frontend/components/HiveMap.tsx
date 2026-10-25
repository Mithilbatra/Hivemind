import { HubAgent } from "@/lib/api";

// Seven-cell honeycomb: the shared mind in the middle, the five agent roles and you around it.
const R = 50; // centre-to-centre spacing unit; cells are drawn slightly smaller to leave gaps
const W = Math.sqrt(3) * R;
const SLOTS: Record<string, [number, number]> = {
  planner: [-W / 2, -1.5 * R], researcher: [W / 2, -1.5 * R], writer: [W, 0],
  tester: [W / 2, 1.5 * R], reviewer: [-W / 2, 1.5 * R], you: [-W, 0],
};

function hex(cx: number, cy: number, r = 46) {
  return Array.from({ length: 6 }, (_, i) => {
    const a = (Math.PI / 180) * (60 * i - 90);
    return `${(cx + r * Math.cos(a)).toFixed(1)},${(cy + r * Math.sin(a)).toFixed(1)}`;
  }).join(" ");
}

// `ink` is the text colour on a lit cell; honey cells use --accent, which keeps contrast on the pale fill.
function Cell({ x, y, color, ink = color, active, title, label, status, href, big = false }: {
  x: number; y: number; color: string; ink?: string; active: boolean; title: string; label: string; status: string;
  href?: string; big?: boolean;
}) {
  const body = (
    <g>
      <title>{title}</title>
      <polygon points={hex(x, y)} className={`cell${active ? " live" : ""}`} fill={active ? color : "var(--card)"}
        fillOpacity={active ? 0.18 : 1} stroke={active ? color : "var(--line)"} strokeWidth={active ? 2.5 : 1.5} strokeLinejoin="round" />
      <text x={x} y={y - (big ? 2 : 3)} textAnchor="middle" fontSize={big ? 26 : 13} fontWeight="700" fill="var(--fg)" style={{ textTransform: "capitalize" }}>{label}</text>
      <text x={x} y={y + (big ? 17 : 14)} textAnchor="middle" fontSize="11" fill={active ? ink : "var(--muted)"} fontWeight={active ? 600 : 400}>{status}</text>
    </g>
  );
  return href ? <a href={href}>{body}</a> : body;
}

export default function HiveMap({ agents, needsYou }: { agents: HubAgent[]; needsYou: number }) {
  const working = agents.reduce((n, a) => n + a.active.length, 0);
  const pad = 6;
  const span = W + 46 * Math.cos(Math.PI / 6) + pad; // outer cells sit at ±W; a pointy-top hex is r·cos30° wide each side
  const vspan = 1.5 * R + 46 + pad;
  return (
    <svg className="hivemap" viewBox={`${-span} ${-vspan} ${2 * span} ${2 * vspan}`} role="group"
      aria-label={`The hive: ${working} agent session${working === 1 ? "" : "s"} running, ${needsYou} task${needsYou === 1 ? "" : "s"} waiting for you`}>
      <Cell x={0} y={0} color="var(--honey)" ink="var(--accent)" active={working > 0} title="The shared mind: every agent session running right now"
        label={String(working)} status="working" big />
      {agents.filter((a) => SLOTS[a.role]).map((a) => {
        const [x, y] = SLOTS[a.role];
        const n = a.active.length;
        return (
          <Cell key={a.role} x={x} y={y} color={`var(--r-${a.role})`} active={n > 0} href={`#agent-${a.role}`}
            title={`${a.role}: ${n ? `working on ${n} task${n > 1 ? "s" : ""}` : "idle"}`}
            label={a.role} status={n ? `${n} working` : "idle"} />
        );
      })}
      <Cell x={SLOTS.you[0]} y={SLOTS.you[1]} color="var(--honey)" ink="var(--accent)" active={needsYou > 0} href="#boards"
        title={needsYou ? `${needsYou} task${needsYou > 1 ? "s" : ""} waiting for your approval` : "Nothing waiting for you"}
        label="You" status={needsYou ? `${needsYou} waiting` : "all clear"} />
    </svg>
  );
}
