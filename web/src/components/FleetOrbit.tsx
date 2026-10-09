import { useEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Hand, MousePointerClick, ShieldCheck } from "lucide-react";
import { Count, GlowCard, toneVar, type Tone } from "./fx";
import { Tooltip } from "./ui/tooltip";
import { isHoldReason, isSnapshotSwap } from "./live/streamChange";
import { modeLabel } from "../lib/format";
import { cn } from "../lib/utils";
import type { AgentMode, ToolEvent } from "../lib/types";
import { useTripwire, type TripwireState } from "../hooks/useTripwire";

/*
 * Fleet orbit: the ONE night-sky card on the light page. The checkpoint sits at the centre; every
 * live agent (the same list + modes as the agent cards) sits on its own orbit, at a fixed position.
 * One particle per REAL tool_event that arrives on /stream while this view is open:
 *   allowed  -> a soft white mote travels the spoke and is absorbed by the core
 *   held     -> an amber mote stops at the shield arc (the arc lights amber)
 *   denied   -> a red mote hits the shield and bursts, with a tiny label of the real action
 * No event -> no particle. Planets do not drift; stars twinkle (decoration, scoped to this card).
 */

// Geometry (SVG user units). The SVG scales to the card with preserveAspectRatio="meet".
const W = 760;
const H = 340;
const CX = W / 2;
const CY = 168;
const RY_MIN = 80;
const RY_MAX = 120;
const ASPECT = 2.12; // ellipse rx / ry: a tilted orbital plane
const R_CORE = 28;
const R_HOLD = 39;
const R_SHIELD = 52;

const FLIGHT_S = 0.9;
const LIFE_MS = 2800;
const MAX_PARTICLES = 28;
const MAX_SPAWN_PER_BATCH = 10;

const MODE_TONE: Record<AgentMode, Tone> = { normal: "ok", heightened: "held", quarantined: "bad" };

type Kind = "ok" | "held" | "bad" | "err";
const KIND_COLOR: Record<Kind, string> = {
  ok: "var(--color-fg)", // soft white inside the night scope
  held: toneVar.held,
  bad: toneVar.bad,
  err: "var(--color-muted)",
};

interface Slot {
  ring: number;
  rx: number;
  ry: number;
  x: number;
  y: number;
  ang: number; // direction centre -> planet
}

const DEG = Math.PI / 180;
/** Hand-placed angles for small fleets (left-up, right-down, bottom-right): labels never collide. */
const ANGLES: Record<number, number[]> = {
  1: [200],
  2: [200, 18],
  3: [200, 336, 74],
};

/** Up to 3 orbits; each agent keeps a fixed, deliberate position (the layout never encodes data). */
function layout(n: number): Slot[] {
  const rings = Math.min(Math.max(n, 1), 3);
  return Array.from({ length: n }, (_, i) => {
    const ring = i % rings;
    const ry = rings === 1 ? (RY_MIN + RY_MAX) / 2 : RY_MIN + ((RY_MAX - RY_MIN) * ring) / (rings - 1);
    const rx = ry * ASPECT;
    const deg = ANGLES[n]?.[i] ?? 200 + (360 / n) * i;
    const a = deg * DEG;
    const x = CX + rx * Math.cos(a);
    const y = CY + ry * Math.sin(a);
    return { ring, rx, ry, x, y, ang: Math.atan2(y - CY, x - CX) };
  });
}

function kindOf(e: ToolEvent): Kind {
  if (e.result === "denied") return isHoldReason(e.reason) ? "held" : "bad";
  return e.result === "ok" ? "ok" : "err";
}

const worst = (modes: AgentMode[]): AgentMode =>
  modes.includes("quarantined") ? "quarantined" : modes.includes("heightened") ? "heightened" : "normal";

function arcPath(r: number, from: number, to: number, sweep: 0 | 1 = 1) {
  const x1 = CX + r * Math.cos(from);
  const y1 = CY + r * Math.sin(from);
  const x2 = CX + r * Math.cos(to);
  const y2 = CY + r * Math.sin(to);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${r} ${r} 0 0 ${sweep} ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

interface Particle {
  id: number;
  kind: Kind;
  action: string;
  fx: number;
  fy: number;
  ang: number;
  delay: number;
}

/** One real tool call travelling the spoke to the checkpoint. */
function ParticleView({ p, reduced }: { p: Particle; reduced: boolean }) {
  const color = KIND_COLOR[p.kind];
  const stopped = p.kind === "held" || p.kind === "bad";
  const cos = Math.cos(p.ang);
  const sin = Math.sin(p.ang);
  // Stopped calls end on the shield; allowed calls sink into the core.
  const rEnd = stopped ? R_SHIELD + 3 : R_CORE - 6;
  const tx = CX + rEnd * cos;
  const ty = CY + rEnd * sin;
  const tail = 26;
  const gid = `tw-mote-${p.id}`;
  const d = p.delay;
  const hit = d + (reduced ? 0 : FLIGHT_S);
  const labelR = R_SHIELD + 16;
  const anchor = cos > 0.3 ? "start" : cos < -0.3 ? "end" : "middle";
  const label = p.kind === "held" ? `held · ${p.action}` : `denied · ${p.action}`;

  return (
    <g aria-hidden>
      <defs>
        <linearGradient id={gid} gradientUnits="userSpaceOnUse" x1={0} y1={0} x2={cos * tail} y2={sin * tail}>
          <stop offset="0" style={{ stopColor: color, stopOpacity: 0.85 }} />
          <stop offset="1" style={{ stopColor: color, stopOpacity: 0 }} />
        </linearGradient>
      </defs>

      {/* launch ripple at the agent */}
      {!reduced && (
        <motion.circle
          cx={p.fx}
          cy={p.fy}
          fill="none"
          strokeWidth={1}
          style={{ stroke: color }}
          initial={{ r: 9, opacity: 0 }}
          animate={{ r: [9, 22], opacity: [0.6, 0] }}
          transition={{ delay: d, duration: 0.7, ease: "easeOut" }}
        />
      )}

      {/* the mote */}
      <motion.g
        initial={{ x: reduced ? tx : p.fx, y: reduced ? ty : p.fy, opacity: 0 }}
        animate={{ x: tx, y: ty, opacity: stopped ? [0, 1, 1, 0.9, 0] : [0, 1, 1, 0] }}
        transition={{
          delay: d,
          duration: reduced ? 0.9 : FLIGHT_S,
          ease: [0.4, 0, 0.85, 0.6],
          opacity: stopped
            ? { delay: d, duration: (reduced ? 0.9 : FLIGHT_S) + 0.7, times: [0, 0.1, 0.55, 0.75, 1] }
            : { delay: d, duration: (reduced ? 0.9 : FLIGHT_S) + 0.06, times: [0, 0.12, 0.88, 1] },
        }}
      >
        {!reduced && (
          <line x1={0} y1={0} x2={cos * tail} y2={sin * tail} stroke={`url(#${gid})`} strokeWidth={2} strokeLinecap="round" />
        )}
        <circle r={6} style={{ fill: color }} opacity={0.16} />
        <circle r={2.8} style={{ fill: color }} />
      </motion.g>

      {stopped ? (
        <>
          {/* the shield segment facing the agent lights up */}
          <motion.path
            d={arcPath(R_SHIELD, p.ang - 0.38, p.ang + 0.38)}
            fill="none"
            strokeWidth={2.5}
            strokeLinecap="round"
            style={{ stroke: color }}
            initial={{ opacity: 0 }}
            animate={{ opacity: [0, 1, 0] }}
            transition={{ delay: hit, duration: 1.3, times: [0, 0.12, 1] }}
          />
          {p.kind === "bad" && !reduced && (
            <>
              <motion.circle
                cx={tx}
                cy={ty}
                fill="none"
                strokeWidth={1.5}
                style={{ stroke: color }}
                initial={{ r: 3, opacity: 0 }}
                animate={{ r: [3, 22], opacity: [0.9, 0] }}
                transition={{ delay: hit, duration: 0.65, ease: "easeOut" }}
              />
              <g transform={`translate(${tx.toFixed(2)} ${ty.toFixed(2)})`}>
                {[-0.9, -0.45, 0, 0.45, 0.9].map((o) => {
                  const a = p.ang + o;
                  return (
                    <motion.line
                      key={o}
                      x1={0}
                      y1={0}
                      x2={Math.cos(a) * 4}
                      y2={Math.sin(a) * 4}
                      strokeWidth={1.5}
                      strokeLinecap="round"
                      style={{ stroke: color }}
                      initial={{ x: 0, y: 0, opacity: 0 }}
                      animate={{ x: Math.cos(a) * 16, y: Math.sin(a) * 16, opacity: [0, 1, 0] }}
                      transition={{ delay: hit, duration: 0.6, ease: "easeOut" }}
                    />
                  );
                })}
              </g>
            </>
          )}
          {/* tiny label of the real action that was stopped */}
          <g transform={`translate(${(CX + labelR * cos).toFixed(2)} ${(CY + labelR * sin).toFixed(2)})`}>
            <motion.text
              textAnchor={anchor}
              dominantBaseline="middle"
              className="font-mono"
              fontSize={10.5}
              fontWeight={600}
              strokeWidth={4}
              paintOrder="stroke"
              style={{ fill: color, stroke: "var(--color-night)" }}
              initial={{ opacity: 0 }}
              animate={{ opacity: [0, 1, 1, 0] }}
              transition={{ delay: hit, duration: 1.8, times: [0, 0.1, 0.72, 1] }}
            >
              {label}
            </motion.text>
          </g>
        </>
      ) : (
        !reduced && (
          /* absorbed: the core ripples once */
          <motion.circle
            cx={CX}
            cy={CY}
            fill="none"
            strokeWidth={1}
            style={{ stroke: color }}
            initial={{ r: R_CORE, opacity: 0 }}
            animate={{ r: [R_CORE, R_CORE + 9], opacity: [0.45, 0] }}
            transition={{ delay: hit, duration: 0.55, ease: "easeOut" }}
          />
        )
      )}
    </g>
  );
}

function LegendItem({ color, label, dashed = false, mote = false }: { color: string; label: string; dashed?: boolean; mote?: boolean }) {
  return (
    <li className="flex items-center gap-1.5">
      <svg aria-hidden viewBox="0 0 18 8" className="h-2 w-[18px] shrink-0 overflow-visible">
        {mote ? (
          <>
            <line x1={1} y1={4} x2={12} y2={4} strokeWidth={1.5} strokeLinecap="round" style={{ stroke: color }} opacity={0.4} />
            <circle cx={14} cy={4} r={2.6} style={{ fill: color }} />
          </>
        ) : (
          <>
            <line
              x1={0}
              y1={4}
              x2={18}
              y2={4}
              strokeWidth={1.25}
              strokeDasharray={dashed ? "2 3" : undefined}
              style={{ stroke: color }}
              opacity={0.75}
            />
            <circle cx={9} cy={4} r={3} style={{ fill: color }} />
          </>
        )}
      </svg>
      {label}
    </li>
  );
}

/**
 * The fleet around the checkpoint. `ids` = the same agent list the agent cards use.
 * `onSelect(agentId)` fires when a planet is clicked / activated (the Live tab rings that card);
 * `activeId` rings a planet (its card is hovered / focused).
 */
export function FleetOrbit({
  ids,
  onSelect,
  activeId,
  className,
}: {
  ids: string[];
  onSelect?: (agentId: string) => void;
  activeId?: string | null;
  className?: string;
}) {
  const { state } = useTripwire();
  const reduced = !!useReducedMotion();
  const slots = useMemo(() => layout(ids.length), [ids.length]);
  const live = useRef({ ids, slots });
  live.current = { ids, slots };

  // ---- Real tool_events -> particles -------------------------------------------------------------
  const [particles, setParticles] = useState<Particle[]>([]);
  const [tally, setTally] = useState({ total: 0, allowed: 0, stopped: 0 });
  const seen = useRef<{
    init: boolean;
    head: ToolEvent | undefined;
    swap: Pick<TripwireState, "alerts" | "queryTimings">;
  }>({
    init: false,
    head: undefined,
    swap: { alerts: state.alerts, queryTimings: state.queryTimings },
  });
  const nextId = useRef(0);
  const timers = useRef(new Set<ReturnType<typeof setTimeout>>());

  useEffect(() => {
    const s = seen.current;
    const prevHead = s.head;
    const prevSwap = s.swap;
    const nextSwap = { alerts: state.alerts, queryTimings: state.queryTimings };
    s.head = state.events[0];
    s.swap = nextSwap;
    if (!s.init) {
      s.init = true; // whatever is on screen at mount is history
      return;
    }
    const events = state.events;
    if (!events.length || events[0] === prevHead) return;
    let fresh: ToolEvent[];
    if (prevHead) {
      const k = events.indexOf(prevHead);
      if (k <= 0) return; // list replaced wholesale (snapshot / reset): history, not arrivals
      fresh = events.slice(0, k);
    } else {
      if (isSnapshotSwap(prevSwap, nextSwap)) return; // empty list filled by a snapshot
      fresh = events;
    }

    const allowed = fresh.filter((e) => e.result === "ok").length;
    const stopped = fresh.filter((e) => e.result === "denied").length;
    setTally((t) => ({ total: t.total + fresh.length, allowed: t.allowed + allowed, stopped: t.stopped + stopped }));

    const { ids: list, slots: sl } = live.current;
    const born: Particle[] = [];
    // Oldest first, newest last; under a burst only the newest few fly (the counter still counts all).
    fresh
      .slice(0, MAX_SPAWN_PER_BATCH)
      .reverse()
      .forEach((e, i) => {
        const slot = sl[list.indexOf(e.agent_id)];
        if (!slot) return; // no planet for this agent: counted, not drawn
        born.push({
          id: ++nextId.current,
          kind: kindOf(e),
          action: e.action || "call",
          fx: slot.x,
          fy: slot.y,
          ang: slot.ang,
          delay: i * 0.14,
        });
      });
    if (!born.length) return;
    setParticles((prev) => [...prev, ...born].slice(-MAX_PARTICLES));
    for (const p of born) {
      const t = setTimeout(() => {
        timers.current.delete(t);
        setParticles((prev) => prev.filter((q) => q.id !== p.id));
      }, LIFE_MS + p.delay * 1000);
      timers.current.add(t);
    }
  }, [state.events, state.alerts, state.queryTimings]);

  useEffect(() => {
    const set = timers.current;
    return () => {
      for (const t of set) clearTimeout(t);
      set.clear();
    };
  }, []);

  // ---- Render ----------------------------------------------------------------------------------
  const modes = ids.map((id) => state.modes[id] ?? "normal");
  const fleet = worst(modes);
  const hold = state.holdEnabled;
  const ringCount = Math.min(Math.max(ids.length, 1), 3);
  const rings = Array.from({ length: ringCount }, (_, r) => {
    const members = modes.filter((_, i) => slots[i]?.ring === r);
    const s = slots.find((x) => x.ring === r);
    const ry = s?.ry ?? (RY_MIN + RY_MAX) / 2;
    return { r, mode: worst(members), rx: s?.rx ?? ry * ASPECT, ry };
  });

  const activate = (id: string) => onSelect?.(id);
  const onKey = (id: string) => (e: KeyboardEvent<SVGGElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      activate(id);
    }
  };

  const holdLabel = hold === null ? "—" : hold ? "ON" : "OFF";

  return (
    <GlowCard
      night
      reveal={0}
      className={cn("h-full min-h-[340px]", className)}
      bodyClassName="relative flex min-h-0 flex-col p-0"
      aria-label="Fleet orbit"
    >
      {/* Sky: the SVG fills the card; overlays sit in the empty corners of the ellipse. */}
      <div className="relative min-h-[260px] flex-1">
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="xMidYMid meet"
          className="absolute inset-0 block size-full select-none"
          role="group"
          aria-label={`Fleet orbit: ${ids.length} agents around the checkpoint. Hold mode ${
            hold === null ? "unknown" : hold ? "on" : "off"
          }.`}
        >
          <defs>
            <radialGradient id="tw-orbit-core" cx="40%" cy="34%" r="80%">
              <stop offset="0" style={{ stopColor: "#232836" }} />
              <stop offset="1" style={{ stopColor: "#0f1218" }} />
            </radialGradient>
            <radialGradient id="tw-orbit-hold" cx="50%" cy="50%" r="50%">
              <stop offset="0.5" style={{ stopColor: toneVar.held, stopOpacity: 0.16 }} />
              <stop offset="1" style={{ stopColor: toneVar.held, stopOpacity: 0 }} />
            </radialGradient>
            {(["ok", "held", "bad"] as const).map((t) => (
              <radialGradient key={t} id={`tw-orbit-halo-${t}`} cx="50%" cy="50%" r="50%">
                <stop offset="0" style={{ stopColor: toneVar[t], stopOpacity: 0.32 }} />
                <stop offset="1" style={{ stopColor: toneVar[t], stopOpacity: 0 }} />
              </radialGradient>
            ))}
            <path id="tw-orbit-hold-arc" d={arcPath(R_HOLD + 6.5, Math.PI * 1.12, Math.PI * 1.88)} />
            <path id="tw-orbit-shield-arc" d={arcPath(R_SHIELD + 9, Math.PI * 0.82, Math.PI * 0.18, 0)} />
          </defs>

          {/* orbits: faint white hairlines that draw in once; quarantine breaks the ring (red dashes) */}
          {rings.map(({ r, mode, rx, ry }) => (
            <g key={r}>
              <motion.ellipse
                cx={CX}
                cy={CY}
                rx={rx}
                ry={ry}
                fill="none"
                strokeWidth={1}
                style={{ stroke: "rgb(255 255 255 / 0.13)" }}
                initial={{ pathLength: 0, opacity: 0 }}
                animate={{ pathLength: 1, opacity: mode === "quarantined" ? 0 : 1 }}
                transition={{ pathLength: { duration: 1.1, ease: [0.22, 1, 0.36, 1], delay: 0.15 + r * 0.12 }, opacity: { duration: 0.4 } }}
              />
              {mode !== "normal" && (
                <motion.ellipse
                  cx={CX}
                  cy={CY}
                  rx={rx}
                  ry={ry}
                  fill="none"
                  strokeWidth={mode === "quarantined" ? 1.5 : 1}
                  strokeDasharray={mode === "quarantined" ? "3 9" : undefined}
                  style={{ stroke: toneVar[MODE_TONE[mode]] }}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: mode === "quarantined" ? 0.85 : 0.45 }}
                  transition={{ duration: 0.45 }}
                />
              )}
            </g>
          ))}

          {/* spokes: the route every tool call takes to the checkpoint (structure, not data) */}
          {slots.map((s, i) => (
            <line
              key={`spoke-${ids[i]}`}
              x1={s.x}
              y1={s.y}
              x2={CX + (R_SHIELD + 2) * Math.cos(s.ang)}
              y2={CY + (R_SHIELD + 2) * Math.sin(s.ang)}
              strokeWidth={1}
              strokeDasharray="1 5"
              strokeLinecap="round"
              style={{ stroke: "rgb(255 255 255 / 0.16)" }}
            />
          ))}

          {/* checkpoint: shield boundary, hold-mode ring, core */}
          {hold && <circle cx={CX} cy={CY} r={R_SHIELD + 18} fill="url(#tw-orbit-hold)" className="animate-breathe" />}
          <circle
            cx={CX}
            cy={CY}
            r={R_SHIELD}
            fill="none"
            strokeWidth={1}
            strokeDasharray="1 4"
            strokeLinecap="round"
            style={{ stroke: "rgb(255 255 255 / 0.32)" }}
          />
          <text className="font-mono" fontSize={7.5} letterSpacing="0.22em" style={{ fill: "rgb(255 255 255 / 0.42)" }}>
            <textPath href="#tw-orbit-shield-arc" startOffset="50%" textAnchor="middle">
              SHIELD
            </textPath>
          </text>
          <circle
            cx={CX}
            cy={CY}
            r={R_HOLD}
            fill="none"
            strokeWidth={hold ? 1.75 : 1}
            strokeDasharray={hold ? undefined : "3 4"}
            style={{
              stroke: hold ? toneVar.held : "rgb(255 255 255 / 0.22)",
              transition: "stroke 0.4s ease, stroke-width 0.4s ease",
            }}
          />
          <text className="font-mono" fontSize={7.5} fontWeight={600} letterSpacing="0.2em">
            <textPath
              href="#tw-orbit-hold-arc"
              startOffset="50%"
              textAnchor="middle"
              style={{ fill: hold ? toneVar.held : "rgb(255 255 255 / 0.45)", transition: "fill 0.4s ease" }}
            >
              {`HOLD ${holdLabel}`}
            </textPath>
          </text>
          <circle cx={CX} cy={CY} r={R_CORE} fill="url(#tw-orbit-core)" strokeWidth={1} style={{ stroke: "rgb(255 255 255 / 0.16)" }} />
          <ShieldCheck
            x={CX - 11}
            y={CY - 13}
            width={22}
            height={22}
            strokeWidth={1.75}
            style={{ color: hold ? "var(--color-held)" : "var(--color-fg)", transition: "color 0.4s ease" }}
            aria-hidden
          />
          <text
            x={CX}
            y={CY + R_SHIELD + 22}
            textAnchor="middle"
            className="font-mono"
            fontSize={8.5}
            fontWeight={600}
            letterSpacing="0.16em"
            style={{ fill: "rgb(255 255 255 / 0.55)" }}
          >
            CHECKPOINT
          </text>

          {/* real tool calls in flight */}
          <g>
            {particles.map((p) => (
              <ParticleView key={p.id} p={p} reduced={reduced} />
            ))}
          </g>

          {/* planets: fixed positions; hue = the agent's real mode */}
          {ids.map((id, i) => {
            const mode = modes[i];
            const tone = MODE_TONE[mode];
            const color = toneVar[tone];
            const s = slots[i];
            if (!s) return null;
            const last = state.stats[id]?.last;
            const active = activeId === id;
            return (
              <g
                key={id}
                transform={`translate(${s.x.toFixed(2)} ${s.y.toFixed(2)})`}
                role="button"
                tabIndex={0}
                aria-label={`${id}, ${modeLabel(mode)}. Show its agent card.`}
                onClick={() => activate(id)}
                onKeyDown={onKey(id)}
                className="group/planet cursor-pointer outline-none"
              >
                <title>
                  {`${id} · ${modeLabel(mode)}${last ? ` · last: ${last.action} ${last.target} (${last.result})` : ""}`}
                </title>
                <circle r={30} fill="transparent" />
                <circle r={24} fill={`url(#tw-orbit-halo-${tone})`} />
                {mode === "quarantined" && (
                  <circle
                    r={11}
                    fill="none"
                    strokeWidth={1.5}
                    style={{ stroke: color }}
                    className="origin-center animate-pulse-ring [transform-box:fill-box]"
                  />
                )}
                {/* focus / linked-card ring (brand) */}
                <circle
                  r={17}
                  fill="none"
                  strokeWidth={1.5}
                  style={{ stroke: "var(--color-brand)" }}
                  className={cn(
                    "transition-opacity duration-200 group-focus-visible/planet:opacity-100",
                    active ? "opacity-100" : "opacity-0",
                  )}
                />
                <g
                  className={cn(
                    "origin-center transition-transform duration-200 ease-out [transform-box:fill-box] group-hover/planet:scale-[1.18]",
                    active && "scale-[1.18]",
                  )}
                >
                  <circle r={10} strokeWidth={2.5} style={{ fill: color, stroke: "var(--color-night)", transition: "fill 0.5s ease" }} />
                  <circle cx={-3} cy={-3} r={3} fill="white" opacity={0.35} />
                </g>
                <text
                  y={31}
                  textAnchor="middle"
                  fontSize={14.5}
                  fontWeight={600}
                  letterSpacing="-0.01em"
                  strokeWidth={4}
                  paintOrder="stroke"
                  style={{ fill: "var(--color-fg)", stroke: "var(--color-night)", fontFamily: "var(--font-sans)" }}
                >
                  {id}
                </text>
                <text
                  y={46}
                  textAnchor="middle"
                  className="font-mono"
                  fontSize={9.5}
                  fontWeight={600}
                  letterSpacing="0.14em"
                  strokeWidth={3}
                  paintOrder="stroke"
                  style={{ fill: color, stroke: "var(--color-night)", transition: "fill 0.5s ease" }}
                >
                  {modeLabel(mode)}
                </text>
              </g>
            );
          })}
        </svg>

        {/* top-left: what this is */}
        <div className="pointer-events-none absolute left-5 top-4 flex flex-col gap-1">
          <span className="eyebrow inline-flex items-center gap-2">
            <span
              aria-hidden
              className="size-1.5 rounded-full"
              style={{ background: fleet === "normal" ? toneVar.ok : toneVar[MODE_TONE[fleet]] }}
            />
            Fleet · checkpoint
          </span>
          <span className="display text-[24px] text-fg">
            Fleet in <em>orbit</em>
          </span>
        </div>

        {/* top-right: the real count of calls seen since this view opened */}
        <Tooltip
          side="bottom"
          align="end"
          content="Tool calls that arrived on the live /stream while this view was open (snapshot history is not counted)"
        >
          <div className="absolute right-5 top-4 flex cursor-default flex-col items-end gap-1 text-right">
            <span className="eyebrow">Calls since opened</span>
            <Count value={tally.total} className="num-display text-[28px] text-fg" />
            <span className="font-mono text-[12px] text-muted">
              <span className="text-fg/80">
                <Count value={tally.allowed} />
              </span>{" "}
              allowed ·{" "}
              <span className={tally.stopped ? "text-bad" : undefined}>
                <Count value={tally.stopped} />
              </span>{" "}
              stopped
            </span>
          </div>
        </Tooltip>
      </div>

      {/* bottom bar: legend + hold state + hint */}
      <div className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-white/[0.07] px-5 py-3">
        <ul className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 font-mono text-[11px] text-muted">
          <LegendItem mote color={KIND_COLOR.ok} label="allowed" />
          <LegendItem mote color={toneVar.held} label="held" />
          <LegendItem mote color={toneVar.bad} label="denied" />
          <li aria-hidden className="h-3 w-px bg-white/15" />
          <LegendItem color={toneVar.held} label="heightened" />
          <LegendItem dashed color={toneVar.bad} label="quarantined" />
        </ul>
        <div className="flex items-center gap-3 font-mono text-[11px] text-muted">
          <span
            className={cn(
              "inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5",
              hold ? "tint-held" : "border-white/10 bg-white/[0.04] text-muted",
            )}
          >
            <Hand className="size-3" strokeWidth={1.75} /> hold {holdLabel.toLowerCase()}
          </span>
          {onSelect && (
            <span className="hidden items-center gap-1.5 xl:inline-flex">
              <MousePointerClick className="size-3.5" strokeWidth={1.75} /> click an agent to find its card
            </span>
          )}
        </div>
      </div>
    </GlowCard>
  );
}
