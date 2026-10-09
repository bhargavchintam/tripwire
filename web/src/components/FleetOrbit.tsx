import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { motion, useAnimationFrame, useReducedMotion } from "motion/react";
import { Hand, MousePointerClick, ShieldCheck } from "lucide-react";
import { Count, GlowCard, toneVar, type Tone } from "./fx";
import { Tooltip } from "./ui/tooltip";
import { isHoldReason, isSnapshotSwap } from "./live/streamChange";
import { modeLabel } from "../lib/format";
import { cn } from "../lib/utils";
import type { AgentMode, ToolEvent } from "../lib/types";
import { useHeatmap, useTripwire, type TripwireState } from "../hooks/useTripwire";

/*
 * Fleet orbit: a light "Paper & Signal" panel. The checkpoint sits at the centre; every live agent
 * (the same list + modes as the agent cards) orbits it on its own ring.
 *
 * Orbital motion is decoration: one animation-frame loop (motion's shared frameloop) moves the planets
 * and the outer band by writing SVG transforms through refs (no React render per frame). It pauses
 * while the tab is hidden and stops entirely under prefers-reduced-motion. Everything that MEANS
 * something is real:
 *   - planets = the live agents in /stream state; hue + label = the agent's real mode
 *   - a quarantined agent coasts to a stop on a red dashed ring; a heightened agent pulses amber
 *   - the faint outer band = agents GET /fleet/heatmap returned that are not live (agent-NN = the
 *     synthetic background fleet), with their real count; request failed -> no band, no dots
 *   - one particle per REAL tool_event that arrives on /stream while this view is open, launched from
 *     the planet's current position: allowed is absorbed, held stops amber at the shield, denied bursts
 * No event -> no particle. No timers fake activity.
 */

// Geometry (SVG user units). The SVG scales to its area with preserveAspectRatio="meet".
const W = 760;
const H = 280;
const CX = W / 2;
const CY = H / 2;
const TAU = Math.PI * 2;
const DEG = Math.PI / 180;

const R_CORE = 24;
const R_HOLD = 33;
const R_SHIELD = 44;

const ASPECT = 2.3; // ellipse rx / ry: a tilted orbital plane
const P_R = 9; // planet radius
const LABEL_GAP = 5; // clear space between a planet and its label
const DEPTH = 0.07; // planets read a touch larger at the front (bottom) of the plane

/** Ring radii (ry), periods and start angles by ring count. Inner rings turn faster (Kepler-ish). */
const RING_RY: Record<number, number[]> = { 1: [80], 2: [70, 92], 3: [66, 80, 94] };
const RING_PERIOD_S: Record<number, number[]> = { 1: [64], 2: [54, 78], 3: [48, 64, 84] };
/** Start angles: left-up, right-up, bottom-right (spread out so nothing overlaps at first paint). */
const START_DEG: Record<number, number[]> = { 1: [200], 2: [200, 18], 3: [200, 336, 74] };

// The outer band (real fleet list from GET /fleet/heatmap): a slow, flat ring of faint dots.
const BAND_RX = 352;
const BAND_RY = 118;
const BAND_OMEGA = TAU / 240; // one turn every 4 minutes
const HEAT_HOURS = 72; // same request (and react-query key) as the Fleet tab

const FLIGHT_S = 0.9;
const LIFE_MS = 2800;
const MAX_PARTICLES = 28;
const MAX_SPAWN_PER_BATCH = 10;

const MODE_TONE: Record<AgentMode, Tone> = { normal: "ok", heightened: "held", quarantined: "bad" };
/** Mode label text colour on white (ACTIVE in muted ink keeps >= 4.5:1; state words in their tone). */
const MODE_TEXT: Record<AgentMode, string> = {
  normal: "var(--color-muted)",
  heightened: toneVar.held,
  quarantined: toneVar.bad,
};

type Kind = "ok" | "held" | "bad" | "err";
const KIND_COLOR: Record<Kind, string> = {
  ok: "var(--color-muted)", // calm ink mote: allowed traffic is the quiet default
  held: toneVar.held,
  bad: toneVar.bad,
  err: "var(--color-dim)",
};

const SYNTHETIC = /^agent-\d+$/;

interface Orbit {
  ring: number;
  rx: number;
  ry: number;
  omega: number; // rad/s at full speed
  phase: number; // start angle
}

/** Per-agent kinematics (kept across renders and layout changes, keyed by agent id). */
interface Kin {
  a: number; // angle on its ellipse
  v: number; // speed factor 0..1 (eases to 0 when quarantined / hovered)
  rx: number; // current (eased) ellipse radii
  ry: number;
}

/** Everything the frame loop touches: mutated in place, never rendered from. */
interface Scene {
  ids: string[];
  orbits: Orbit[];
  modes: Record<string, AgentMode>;
  kin: Map<string, Kin>;
  planet: Map<string, SVGElement>;
  body: Map<string, SVGElement>;
  label: Map<string, SVGElement>;
  spoke: Map<string, SVGElement>;
  bandEl: Map<string, SVGElement>;
  bandIds: string[];
  bandRot: number;
  labelBox: Map<string, LabelBox>;
  tip: HTMLDivElement | null;
  tipW: number;
  tipH: number;
  hover: string | null;
  box: { w: number; h: number; top: number }; // orbit area size + its offset inside the card
  reduced: boolean;
  dirty: boolean;
}

/** Up to 3 rings; agent i rides ring i % rings, spread evenly when rings are shared (n > 3). */
function layout(n: number): Orbit[] {
  const rings = Math.min(Math.max(n, 1), 3);
  const ry = RING_RY[rings];
  const per = RING_PERIOD_S[rings];
  const start = START_DEG[rings];
  return Array.from({ length: n }, (_, i) => {
    const ring = i % rings;
    const members = Math.ceil((n - ring) / rings);
    const k = Math.floor(i / rings);
    return {
      ring,
      rx: ry[ring] * ASPECT,
      ry: ry[ring],
      omega: TAU / per[ring],
      phase: start[ring] * DEG + (k * TAU) / members,
    };
  });
}

function pos(rx: number, ry: number, a: number) {
  const x = CX + rx * Math.cos(a);
  const y = CY + ry * Math.sin(a);
  return { x, y, ang: Math.atan2(y - CY, x - CX) };
}

const f2 = (n: number) => n.toFixed(2);

/** A planet label's measured box (local coords): half sizes + the offset of its centre. */
interface LabelBox {
  hw: number;
  hh: number;
  ox: number;
  oy: number;
}
/** Fallback until the real text is measured (13px Geist 600 over a 9px mono line). */
const estBox = (id: string): LabelBox => ({ hw: Math.max(id.length * 7.4, 11 * 6.6) / 2 + 2, hh: 15, ox: 0, oy: 2 });

/**
 * Smallest t so that a (2hw x 2hh) box centred at t*u stays >= R away from the origin (u is a unit
 * vector, ax = |ux|, ay = |uy|). Exact for an axis-aligned box, and continuous in u, so a label glides
 * around its planet instead of jumping sides.
 */
function clearance(ax: number, ay: number, R: number, hw: number, hh: number): number {
  let t = Number.POSITIVE_INFINITY;
  if (ax > 1e-6) t = Math.min(t, (R + hw) / ax); // clear sideways
  if (ay > 1e-6) t = Math.min(t, (R + hh) / ay); // clear vertically
  const k = ax * hw + ay * hh; // clear past the corner
  const disc = k * k - (hw * hw + hh * hh - R * R);
  if (disc >= 0) {
    const tc = k + Math.sqrt(disc);
    if (ax * tc >= hw && ay * tc >= hh) t = Math.min(t, tc);
  }
  return t;
}

function kinOf(s: Scene, id: string, o: Orbit): Kin {
  let k = s.kin.get(id);
  if (!k) {
    k = { a: o.phase, v: s.modes[id] === "quarantined" ? 0 : 1, rx: o.rx, ry: o.ry };
    s.kin.set(id, k);
  }
  return k;
}

/** Advance the decoration by dt seconds (orbits ease their speed; quarantine coasts to a stop). */
function stepScene(s: Scene, dt: number) {
  const ease = 1 - Math.exp(-dt / 0.6);
  s.ids.forEach((id, i) => {
    const o = s.orbits[i];
    if (!o) return;
    const k = kinOf(s, id, o);
    const paused = s.hover === id; // stop under the pointer / focus so it can be read and clicked
    const stopped = s.modes[id] === "quarantined";
    const target = paused || stopped ? 0 : 1;
    const tau = paused ? 0.16 : stopped ? 1.4 : 0.9;
    k.v += (target - k.v) * (1 - Math.exp(-dt / tau));
    if (target === 0 && k.v < 1e-3) k.v = 0;
    k.a = (k.a + o.omega * k.v * dt) % TAU;
    k.rx += (o.rx - k.rx) * ease;
    k.ry += (o.ry - k.ry) * ease;
  });
  s.bandRot = (s.bandRot + BAND_OMEGA * dt) % TAU;
}

/** Map an SVG point to container pixels and park the hover card above (or below) the planet. */
function placeTip(s: Scene, x: number, y: number) {
  const tip = s.tip;
  const { w, h } = s.box;
  if (!tip || !w || !h) return;
  const sc = Math.min(w / W, h / H);
  const ox = (w - W * sc) / 2;
  const oy = (h - H * sc) / 2;
  const half = s.tipW / 2 + 8;
  const px = Math.min(Math.max(ox + x * sc, half), Math.max(half, w - half));
  const above = oy + (y - P_R - 10) * sc;
  // Above the planet when it fits inside the card (it may float over the header); else below.
  const below = above - s.tipH < 8 - s.box.top;
  const py = below ? oy + (y + P_R + 10) * sc : above;
  tip.style.transform = `translate(${f2(px)}px, ${f2(py)}px) translate(-50%, ${below ? "0%" : "-100%"})`;
}

/** Write every moving transform from the scene (no React). */
function drawScene(s: Scene) {
  s.ids.forEach((id, i) => {
    const o = s.orbits[i];
    if (!o) return;
    const k = kinOf(s, id, o);
    if (s.reduced) {
      k.rx = o.rx;
      k.ry = o.ry;
    }
    const p = pos(k.rx, k.ry, k.a);
    const ux = Math.cos(p.ang);
    const uy = Math.sin(p.ang);
    s.planet.get(id)?.setAttribute("transform", `translate(${f2(p.x)} ${f2(p.y)})`);
    s.body.get(id)?.setAttribute("transform", `scale(${(1 + DEPTH * Math.sin(k.a)).toFixed(3)})`);
    // Label sits on the outward side, just clear of the planet (support of the label box along u).
    const lb = s.labelBox.get(id) ?? estBox(id);
    const t = clearance(Math.abs(ux), Math.abs(uy), P_R * (1 + DEPTH) + LABEL_GAP, lb.hw, lb.hh);
    s.label
      .get(id)
      ?.setAttribute("transform", `translate(${f2(p.x + ux * t - lb.ox)} ${f2(p.y + uy * t - lb.oy)})`);
    const sp = s.spoke.get(id);
    if (sp) {
      sp.setAttribute("x1", f2(p.x - ux * (P_R + 5)));
      sp.setAttribute("y1", f2(p.y - uy * (P_R + 5)));
      sp.setAttribute("x2", f2(CX + (R_SHIELD + 2) * ux));
      sp.setAttribute("y2", f2(CY + (R_SHIELD + 2) * uy));
    }
    if (s.hover === id) placeTip(s, p.x, p.y);
  });
  const n = s.bandIds.length;
  s.bandIds.forEach((id, j) => {
    const el = s.bandEl.get(id);
    if (!el) return;
    const a = s.bandRot + (j * TAU) / n;
    el.setAttribute("transform", `translate(${f2(CX + BAND_RX * Math.cos(a))} ${f2(CY + BAND_RY * Math.sin(a))})`);
    // back of the plane (top) fainter, front (bottom) a touch stronger
    el.setAttribute("opacity", (0.5 + 0.4 * (0.5 + 0.5 * Math.sin(a))).toFixed(2));
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
  return `M ${f2(x1)} ${f2(y1)} A ${r} ${r} 0 0 ${sweep} ${f2(x2)} ${f2(y2)}`;
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

/** One real tool call travelling from the agent's position to the checkpoint. */
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
  const labelR = R_SHIELD + 15;
  const anchor = cos > 0.3 ? "start" : cos < -0.3 ? "end" : "middle";
  const label = p.kind === "held" ? `held · ${p.action}` : `denied · ${p.action}`;

  return (
    <g aria-hidden>
      <defs>
        <linearGradient id={gid} gradientUnits="userSpaceOnUse" x1={0} y1={0} x2={cos * tail} y2={sin * tail}>
          <stop offset="0" style={{ stopColor: color, stopOpacity: 0.8 }} />
          <stop offset="1" style={{ stopColor: color, stopOpacity: 0 }} />
        </linearGradient>
      </defs>

      {/* launch ripple where the agent is right now */}
      {!reduced && (
        <motion.circle
          cx={p.fx}
          cy={p.fy}
          fill="none"
          strokeWidth={1}
          style={{ stroke: color }}
          initial={{ r: 10, opacity: 0 }}
          animate={{ r: [10, 22], opacity: [0.55, 0] }}
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
        <circle r={6} style={{ fill: color }} opacity={0.14} />
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
              <g transform={`translate(${f2(tx)} ${f2(ty)})`}>
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
          <g transform={`translate(${f2(CX + labelR * cos)} ${f2(CY + labelR * sin)})`}>
            <motion.text
              textAnchor={anchor}
              dominantBaseline="middle"
              className="font-mono"
              fontSize={10.5}
              fontWeight={600}
              strokeWidth={4}
              strokeLinejoin="round"
              paintOrder="stroke"
              style={{ fill: color, stroke: "var(--color-panel)" }}
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
            style={{ stroke: "var(--color-brand)" }}
            initial={{ r: R_CORE, opacity: 0 }}
            animate={{ r: [R_CORE, R_CORE + 9], opacity: [0.4, 0] }}
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

type RefKind = "planet" | "body" | "label" | "spoke" | "bandEl";

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
  const orbits = useMemo(() => layout(ids.length), [ids.length]);
  const modes = ids.map((id) => state.modes[id] ?? "normal");

  // Real fleet list (GET /fleet/heatmap, shared cache with the Fleet tab; no polling from here).
  // Failed request -> no band at all; never fake dots.
  const heat = useHeatmap(HEAT_HOURS, false);
  const fleetList = heat.isError ? undefined : heat.data?.agents;
  // sorted so a refetch that reorders rows never makes the band jump
  const bandIds = useMemo(() => (fleetList ?? []).filter((a) => !ids.includes(a)).sort(), [fleetList, ids]);
  const synthCount = bandIds.filter((a) => SYNTHETIC.test(a)).length;
  const otherCount = bandIds.length - synthCount;

  // ---- Scene (mutable, read by the frame loop) -------------------------------------------------
  const sceneRef = useRef<Scene | null>(null);
  if (!sceneRef.current) {
    sceneRef.current = {
      ids,
      orbits,
      modes: {},
      kin: new Map(),
      planet: new Map(),
      body: new Map(),
      label: new Map(),
      spoke: new Map(),
      bandEl: new Map(),
      bandIds: [],
      bandRot: -Math.PI / 2,
      labelBox: new Map(),
      tip: null,
      tipW: 200,
      tipH: 90,
      hover: null,
      box: { w: 0, h: 0, top: 0 },
      reduced,
      dirty: true,
    };
  }
  const scene = sceneRef.current;
  scene.ids = ids;
  scene.orbits = orbits;
  scene.modes = Object.fromEntries(ids.map((id, i) => [id, modes[i]]));
  scene.bandIds = bandIds;
  scene.reduced = reduced;
  scene.dirty = true;

  // Stable ref callbacks per (kind, id): place the element the moment it mounts (no 0,0 flash).
  const refCache = useRef(new Map<string, (el: SVGElement | null) => void>());
  const bind = (kind: RefKind, id: string) => {
    const key = `${kind}\u0000${id}`;
    let cb = refCache.current.get(key);
    if (!cb) {
      cb = (el: SVGElement | null) => {
        const s = sceneRef.current;
        if (!s) return;
        if (el) {
          s[kind].set(id, el);
          drawScene(s);
        } else {
          s[kind].delete(id);
        }
      };
      refCache.current.set(key, cb);
    }
    return cb;
  };

  // One frame loop for the whole SVG (motion's shared frameloop; delta is clamped to 40 ms there).
  const tick = useCallback((_t: number, delta: number) => {
    const s = sceneRef.current;
    if (!s || (typeof document !== "undefined" && document.hidden)) return;
    if (!s.reduced) stepScene(s, Math.min(delta, 50) / 1000);
    else if (!s.dirty) return; // reduced motion: nothing moves; redraw only on layout / hover changes
    s.dirty = false;
    drawScene(s);
  }, []);
  useAnimationFrame(tick);

  // Container size -> SVG-to-pixel mapping for the hover card.
  const boxRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = boxRef.current;
    if (!el || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver((entries) => {
      const r = entries[0]?.contentRect;
      const s = sceneRef.current;
      if (!r || !s) return;
      s.box = { w: r.width, h: r.height, top: el.offsetTop };
      s.dirty = true;
    });
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  // Measure each planet label once its text is in the DOM (and again once web fonts settle).
  const labelKey = ids.map((id, i) => `${id}:${modes[i]}`).join("|");
  useLayoutEffect(() => {
    const measure = () => {
      const s = sceneRef.current;
      if (!s) return;
      for (const [id, el] of s.label) {
        try {
          const bb = (el as SVGGraphicsElement).getBBox();
          if (bb.width > 0)
            s.labelBox.set(id, { hw: bb.width / 2 + 2, hh: bb.height / 2 + 1, ox: bb.x + bb.width / 2, oy: bb.y + bb.height / 2 });
        } catch {
          /* not rendered yet: keep the estimate */
        }
      }
      s.dirty = true;
    };
    measure();
    let alive = true;
    document.fonts?.ready.then(() => alive && measure()).catch(() => {});
    return () => {
      alive = false;
    };
  }, [labelKey]);

  // ---- Hover / focus: the planet eases to a stop under the pointer and shows its real numbers ----
  const [hovered, setHovered] = useState<string | null>(null);
  const setHover = (id: string | null) => {
    scene.hover = id;
    scene.dirty = true;
    setHovered(id);
  };
  const clearHover = (id: string) => {
    if (scene.hover === id) setHover(null);
  };
  const tipRef = useCallback((el: HTMLDivElement | null) => {
    const s = sceneRef.current;
    if (!s) return;
    s.tip = el;
    if (el) {
      s.tipW = el.offsetWidth || s.tipW;
      s.tipH = el.offsetHeight || s.tipH;
      drawScene(s);
    }
  }, []);
  useLayoutEffect(() => {
    // tooltip content can change while open (new calls): keep its width for edge clamping
    const s = sceneRef.current;
    if (s?.tip) {
      s.tipW = s.tip.offsetWidth || s.tipW;
      s.tipH = s.tip.offsetHeight || s.tipH;
      s.dirty = true;
    }
  });

  // ---- Real tool_events -> particles -------------------------------------------------------------
  const [particles, setParticles] = useState<Particle[]>([]);
  const [tally, setTally] = useState<{ total: number; allowed: number; stopped: number; byAgent: Record<string, number> }>({
    total: 0,
    allowed: 0,
    stopped: 0,
    byAgent: {},
  });
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
    setTally((t) => {
      const byAgent = { ...t.byAgent };
      for (const e of fresh) byAgent[e.agent_id] = (byAgent[e.agent_id] ?? 0) + 1;
      return { total: t.total + fresh.length, allowed: t.allowed + allowed, stopped: t.stopped + stopped, byAgent };
    });

    const sc = sceneRef.current;
    if (!sc) return;
    const born: Particle[] = [];
    // Oldest first, newest last; under a burst only the newest few fly (the counter still counts all).
    fresh
      .slice(0, MAX_SPAWN_PER_BATCH)
      .reverse()
      .forEach((e, i) => {
        const idx = sc.ids.indexOf(e.agent_id);
        const o = sc.orbits[idx];
        if (!o) return; // no planet for this agent: counted, not drawn
        const delay = i * 0.14;
        const k = kinOf(sc, e.agent_id, o);
        // launch from where the planet WILL be when this mote leaves (it keeps orbiting meanwhile)
        const a = k.a + (sc.reduced ? 0 : o.omega * k.v * delay);
        const p = pos(k.rx, k.ry, a);
        born.push({ id: ++nextId.current, kind: kindOf(e), action: e.action || "call", fx: p.x, fy: p.y, ang: p.ang, delay });
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
  const fleet = worst(modes);
  const hold = state.holdEnabled;
  const ringCount = Math.min(Math.max(ids.length, 1), 3);
  const rings = Array.from({ length: ringCount }, (_, r) => {
    const members = modes.filter((_, i) => orbits[i]?.ring === r);
    const o = orbits.find((x) => x.ring === r);
    const ry = o?.ry ?? RING_RY[1][0];
    return { r, mode: worst(members), rx: o?.rx ?? ry * ASPECT, ry };
  });

  const activate = (id: string) => onSelect?.(id);
  const onKey = (id: string) => (e: KeyboardEvent<SVGGElement>) => {
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      activate(id);
    }
  };

  const holdLabel = hold === null ? "—" : hold ? "ON" : "OFF";
  const hoverIdx = hovered ? ids.indexOf(hovered) : -1;
  const hoverMode = hoverIdx >= 0 ? modes[hoverIdx] : undefined;
  const hoverStats = hovered ? state.stats[hovered] : undefined;

  return (
    <GlowCard
      glow="none"
      reveal={0}
      className={cn(
        "h-full min-h-[340px] bg-[linear-gradient(180deg,var(--color-panel)_0%,var(--color-panel-2)_100%)]",
        className,
      )}
      bodyClassName="relative flex min-h-0 flex-col p-0"
      aria-label="Fleet orbit"
    >
      {/* header: what this is | the real count of calls seen since this view opened */}
      <div className="relative z-10 flex items-start justify-between gap-4 px-5 pt-4">
        <div className="flex min-w-0 flex-col gap-1">
          <div className="flex items-center gap-3">
            <span className="eyebrow inline-flex items-center gap-2">
              <span
                aria-hidden
                className="size-1.5 rounded-full"
                style={{ background: fleet === "normal" ? toneVar.ok : toneVar[MODE_TONE[fleet]] }}
              />
              Fleet · checkpoint
            </span>
            {onSelect && (
              <span className="hidden items-center gap-1 text-[12px] text-muted xl:inline-flex">
                <MousePointerClick className="size-3.5" strokeWidth={1.75} /> click an agent to find its card
              </span>
            )}
          </div>
          <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
            <span className="display text-[24px] text-fg">
              Fleet in <em>orbit</em>
            </span>
            {/* the outer band, with its real count (only when GET /fleet/heatmap answered) */}
            {bandIds.length > 0 && (
              <Tooltip
                side="bottom"
                align="start"
                content={`Agents GET /fleet/heatmap (ClickHouse, last ${HEAT_HOURS} h) returned that are not live on /stream. agent-NN = the synthetic background load; drawn as the faint outer band for scale: no live state, no particles.`}
              >
                <span className="inline-flex cursor-default items-center gap-1.5 font-mono text-[11px] text-muted">
                  <svg aria-hidden viewBox="0 0 8 8" className="size-2">
                    <circle cx={4} cy={4} r={2.6} fill="none" strokeWidth={1.1} style={{ stroke: "var(--color-dim)" }} />
                  </svg>
                  {synthCount > 0 && `${synthCount} synthetic background agent${synthCount === 1 ? "" : "s"}`}
                  {synthCount > 0 && otherCount > 0 && " · "}
                  {otherCount > 0 && `${otherCount} other agent${otherCount === 1 ? "" : "s"}`}
                  {" · ClickHouse"}
                  {heat.data?.mock ? " · mock" : ""}
                </span>
              </Tooltip>
            )}
          </div>
        </div>
        <Tooltip
          side="bottom"
          align="end"
          content="Tool calls that arrived on the live /stream while this view was open (snapshot history is not counted)"
        >
          <div className="flex cursor-default flex-col items-end gap-1 text-right">
            <span className="eyebrow">Calls since opened</span>
            <div className="flex items-baseline gap-3">
              <span className="font-mono text-[12px] text-muted">
                <span className="text-fg">
                  <Count value={tally.allowed} />
                </span>{" "}
                allowed ·{" "}
                <span className={tally.stopped ? "text-bad" : "text-fg"}>
                  <Count value={tally.stopped} />
                </span>{" "}
                stopped
              </span>
              <Count value={tally.total} className="num-display text-[28px] text-fg" />
            </div>
          </div>
        </Tooltip>
      </div>

      {/* the orbit: light porcelain field, faint dotted grid, the SVG on top */}
      <div ref={boxRef} className="relative min-h-[220px] flex-1">
        <div
          aria-hidden
          className="pointer-events-none absolute inset-0"
          style={{
            backgroundImage: "radial-gradient(circle, var(--color-line-strong) 0.9px, transparent 1.3px)",
            backgroundSize: "18px 18px",
            backgroundPosition: "center",
            maskImage: "radial-gradient(ellipse 62% 72% at 50% 50%, #000 15%, transparent 78%)",
            WebkitMaskImage: "radial-gradient(ellipse 62% 72% at 50% 50%, #000 15%, transparent 78%)",
            opacity: 0.8,
          }}
        />
        <svg
          viewBox={`0 0 ${W} ${H}`}
          preserveAspectRatio="xMidYMid meet"
          className="absolute inset-0 block size-full select-none"
          role="group"
          aria-label={`Fleet orbit: ${ids.length} live agents around the checkpoint${
            bandIds.length ? `, ${bandIds.length} background agents on the outer band` : ""
          }. Hold mode ${hold === null ? "unknown" : hold ? "on" : "off"}.`}
        >
          <defs>
            <radialGradient id="tw-orbit-glow" cx="50%" cy="50%" r="50%">
              <stop offset="0" style={{ stopColor: "var(--color-brand)", stopOpacity: 0.13 }} />
              <stop offset="0.45" style={{ stopColor: "var(--color-brand)", stopOpacity: 0.05 }} />
              <stop offset="1" style={{ stopColor: "var(--color-brand)", stopOpacity: 0 }} />
            </radialGradient>
            <radialGradient id="tw-orbit-core" cx="40%" cy="34%" r="80%">
              <stop offset="0" style={{ stopColor: "var(--color-panel)" }} />
              <stop offset="1" style={{ stopColor: "var(--color-brand-soft)" }} />
            </radialGradient>
            <radialGradient id="tw-orbit-hold" cx="50%" cy="50%" r="50%">
              <stop offset="0.45" style={{ stopColor: toneVar.held, stopOpacity: 0.16 }} />
              <stop offset="1" style={{ stopColor: toneVar.held, stopOpacity: 0 }} />
            </radialGradient>
            {(["ok", "held", "bad"] as const).map((t) => (
              <radialGradient key={t} id={`tw-orbit-halo-${t}`} cx="50%" cy="50%" r="50%">
                <stop offset="0" style={{ stopColor: toneVar[t], stopOpacity: 0.24 }} />
                <stop offset="1" style={{ stopColor: toneVar[t], stopOpacity: 0 }} />
              </radialGradient>
            ))}
            <path id="tw-orbit-hold-arc" d={arcPath(R_HOLD + 5.5, Math.PI * 1.12, Math.PI * 1.88)} />
            <path id="tw-orbit-shield-arc" d={arcPath(R_SHIELD + 8.5, Math.PI * 0.96, Math.PI * 0.04, 0)} />
          </defs>

          {/* soft indigo glow at the checkpoint */}
          <ellipse cx={CX} cy={CY} rx={190} ry={120} fill="url(#tw-orbit-glow)" />

          {/* outer band: the real non-live fleet from GET /fleet/heatmap (hollow = no live state) */}
          {bandIds.length > 0 && (
            <g aria-hidden className="pointer-events-none">
              <ellipse
                cx={CX}
                cy={CY}
                rx={BAND_RX}
                ry={BAND_RY}
                fill="none"
                strokeWidth={1}
                strokeDasharray="1 6"
                strokeLinecap="round"
                style={{ stroke: "var(--color-line-strong)" }}
              />
              {bandIds.map((a) => (
                <g key={a} ref={bind("bandEl", a)}>
                  {SYNTHETIC.test(a) ? (
                    <circle r={2.4} fill="var(--color-panel)" strokeWidth={1.1} style={{ stroke: "var(--color-dim)" }} />
                  ) : (
                    <circle r={2.4} style={{ fill: "var(--color-dim)" }} />
                  )}
                </g>
              ))}
            </g>
          )}

          {/* orbits: warm-grey hairlines that draw in once; quarantine breaks the ring (red dashes) */}
          {rings.map(({ r, mode, rx, ry }) => (
            <g key={r}>
              <motion.ellipse
                cx={CX}
                cy={CY}
                fill="none"
                strokeWidth={1}
                style={{ stroke: "var(--color-line-strong)" }}
                initial={{ pathLength: 0, opacity: 0, rx, ry }}
                animate={{ pathLength: 1, opacity: mode === "quarantined" ? 0 : 1, rx, ry }}
                transition={{
                  pathLength: { duration: 1.1, ease: [0.22, 1, 0.36, 1], delay: 0.15 + r * 0.12 },
                  opacity: { duration: 0.4 },
                  rx: { duration: 0.6, ease: "easeOut" },
                  ry: { duration: 0.6, ease: "easeOut" },
                }}
              />
              {mode !== "normal" && (
                <motion.ellipse
                  cx={CX}
                  cy={CY}
                  rx={rx}
                  ry={ry}
                  fill="none"
                  strokeWidth={mode === "quarantined" ? 1.5 : 1.25}
                  strokeDasharray={mode === "quarantined" ? "3 8" : undefined}
                  strokeLinecap="round"
                  style={{ stroke: toneVar[MODE_TONE[mode]] }}
                  initial={{ opacity: 0 }}
                  animate={{ opacity: mode === "quarantined" ? 0.85 : 0.5 }}
                  transition={{ duration: 0.45 }}
                />
              )}
            </g>
          ))}

          {/* spokes: the route every tool call takes to the checkpoint (they follow the planets) */}
          {ids.map((id) => (
            <line
              key={`spoke-${id}`}
              ref={bind("spoke", id)}
              strokeWidth={1}
              strokeDasharray="1 5"
              strokeLinecap="round"
              style={{ stroke: "var(--color-line-strong)" }}
            />
          ))}

          {/* checkpoint: hold-mode ring (breathes while hold is ON), shield boundary, core */}
          {hold && <circle cx={CX} cy={CY} r={R_SHIELD + 16} fill="url(#tw-orbit-hold)" className="animate-breathe" />}
          <circle
            cx={CX}
            cy={CY}
            r={R_SHIELD}
            fill="none"
            strokeWidth={1.25}
            strokeDasharray="1 4"
            strokeLinecap="round"
            style={{ stroke: "var(--color-brand-line)" }}
          />
          <text className="font-mono" fontSize={7.5} fontWeight={600} letterSpacing="0.18em" style={{ fill: "var(--color-muted)" }}>
            <textPath href="#tw-orbit-shield-arc" startOffset="50%" textAnchor="middle">
              CHECKPOINT · SHIELD
            </textPath>
          </text>
          {hold && !reduced ? (
            <motion.circle
              cx={CX}
              cy={CY}
              fill="none"
              strokeWidth={1.75}
              style={{ stroke: toneVar.held }}
              initial={{ r: R_HOLD, opacity: 0.75 }}
              animate={{ r: [R_HOLD, R_HOLD + 2.5, R_HOLD], opacity: [0.75, 1, 0.75] }}
              transition={{ duration: 3.6, ease: "easeInOut", repeat: Infinity }}
            />
          ) : (
            <circle
              cx={CX}
              cy={CY}
              r={R_HOLD}
              fill="none"
              strokeWidth={hold ? 1.75 : 1}
              strokeDasharray={hold ? undefined : "3 4"}
              style={{
                stroke: hold ? toneVar.held : "var(--color-line-strong)",
                transition: "stroke 0.4s ease, stroke-width 0.4s ease",
              }}
            />
          )}
          <text className="font-mono" fontSize={7.5} fontWeight={600} letterSpacing="0.2em">
            <textPath
              href="#tw-orbit-hold-arc"
              startOffset="50%"
              textAnchor="middle"
              style={{ fill: hold ? toneVar.held : "var(--color-muted)", transition: "fill 0.4s ease" }}
            >
              {`HOLD ${holdLabel}`}
            </textPath>
          </text>
          <circle cx={CX} cy={CY} r={R_CORE} fill="url(#tw-orbit-core)" strokeWidth={1} style={{ stroke: "var(--color-brand-line)" }} />
          <ShieldCheck
            x={CX - 10}
            y={CY - 11}
            width={20}
            height={20}
            strokeWidth={1.75}
            style={{ color: hold ? "var(--color-held)" : "var(--color-brand)", transition: "color 0.4s ease" }}
            aria-hidden
          />

          {/* real tool calls in flight */}
          <g>
            {particles.map((p) => (
              <ParticleView key={p.id} p={p} reduced={reduced} />
            ))}
          </g>

          {/* planets: they orbit (decoration); hue = the agent's real mode */}
          {ids.map((id, i) => {
            const mode = modes[i];
            const tone = MODE_TONE[mode];
            const color = toneVar[tone];
            const active = activeId === id;
            return (
              <g
                key={id}
                ref={bind("planet", id)}
                role="button"
                tabIndex={0}
                aria-label={`${id}, ${modeLabel(mode)}. Show its agent card.`}
                onClick={() => activate(id)}
                onKeyDown={onKey(id)}
                onPointerEnter={() => setHover(id)}
                onPointerLeave={() => clearHover(id)}
                onFocus={() => setHover(id)}
                onBlur={() => clearHover(id)}
                className="group/planet cursor-pointer outline-none"
              >
                <circle r={22} fill="transparent" />
                <g ref={bind("body", id)}>
                  <circle
                    r={mode === "normal" ? 17 : 21}
                    fill={`url(#tw-orbit-halo-${tone})`}
                    className={mode === "normal" ? undefined : "animate-breathe"}
                  />
                  {mode !== "normal" && (
                    <circle
                      r={P_R + 1}
                      fill="none"
                      strokeWidth={1.5}
                      style={{
                        stroke: color,
                        animationDuration: mode === "quarantined" ? "1.8s" : "2.6s",
                      }}
                      className="origin-center animate-pulse-ring [transform-box:fill-box]"
                    />
                  )}
                  {/* focus / linked-card ring (brand) */}
                  <circle
                    r={16}
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
                      "origin-center transition-transform duration-200 ease-out [transform-box:fill-box] group-hover/planet:scale-[1.22]",
                      (active || hovered === id) && "scale-[1.22]",
                    )}
                  >
                    <circle r={P_R + 1.75} fill="none" strokeWidth={1} style={{ stroke: "rgb(21 22 26 / 0.1)" }} />
                    <circle r={P_R} strokeWidth={2.25} style={{ fill: color, stroke: "var(--color-panel)", transition: "fill 0.5s ease" }} />
                    <circle cx={-2.6} cy={-2.6} r={2.6} fill="white" opacity={0.4} />
                  </g>
                </g>
              </g>
            );
          })}

          {/* labels ride on the outward side of their planet, never over the checkpoint */}
          {ids.map((id, i) => {
            const mode = modes[i];
            return (
              <g key={`label-${id}`} ref={bind("label", id)} aria-hidden className="pointer-events-none">
                <text
                  y={-5}
                  textAnchor="middle"
                  dominantBaseline="central"
                  fontSize={13}
                  fontWeight={600}
                  letterSpacing="-0.01em"
                  strokeWidth={4}
                  strokeLinejoin="round"
                  paintOrder="stroke"
                  style={{ fill: "var(--color-fg)", stroke: "var(--color-panel)", fontFamily: "var(--font-sans)" }}
                >
                  {id}
                </text>
                <text
                  y={9}
                  textAnchor="middle"
                  dominantBaseline="central"
                  className="font-mono"
                  fontSize={9}
                  fontWeight={600}
                  letterSpacing="0.14em"
                  strokeWidth={3}
                  strokeLinejoin="round"
                  paintOrder="stroke"
                  style={{ fill: MODE_TEXT[mode], stroke: "var(--color-panel)", transition: "fill 0.5s ease" }}
                >
                  {modeLabel(mode)}
                </text>
              </g>
            );
          })}
        </svg>

        {/* hover / focus card: follows its planet (positioned by the frame loop) */}
        {hovered && hoverMode && (
          <div
            ref={tipRef}
            role="tooltip"
            className="theme-night pointer-events-none absolute left-0 top-0 z-20 w-max max-w-[260px] rounded-lg bg-[#15161a] px-3 py-2 text-[12px] leading-[1.45] text-fg shadow-lg"
          >
            <div className="flex items-center gap-2">
              <span className="font-semibold">{hovered}</span>
              <span
                className="font-mono text-[11px] font-semibold tracking-[0.08em]"
                style={{ color: toneVar[MODE_TONE[hoverMode]] }}
              >
                {modeLabel(hoverMode)}
              </span>
            </div>
            <div className="mt-1 grid grid-cols-[auto_auto] gap-x-4 font-mono text-[11px] text-muted">
              <span>calls since opened</span>
              <span className="text-right text-fg tabular-nums">{tally.byAgent[hovered] ?? 0}</span>
              <span>denied on stream</span>
              <span className={cn("text-right tabular-nums", hoverStats?.denied ? "text-bad" : "text-fg")}>
                {hoverStats?.denied ?? 0}
              </span>
            </div>
            {hoverStats?.last && (
              <div className="mt-1 max-w-[236px] truncate font-mono text-[11px] text-muted">
                last · {hoverStats.last.action} {hoverStats.last.target} ({hoverStats.last.result})
              </div>
            )}
          </div>
        )}

      </div>

      {/* bottom bar: legend + hold state */}
      <div className="relative flex flex-wrap items-center justify-between gap-x-4 gap-y-2 border-t border-line px-5 py-2.5">
        <ul className="flex flex-wrap items-center gap-x-3.5 gap-y-1.5 font-mono text-[11px] text-muted">
          <LegendItem mote color={KIND_COLOR.ok} label="allowed" />
          <LegendItem mote color={toneVar.held} label="held" />
          <LegendItem mote color={toneVar.bad} label="denied" />
          <li aria-hidden className="h-3 w-px bg-line-strong" />
          <LegendItem color={toneVar.held} label="heightened" />
          <LegendItem dashed color={toneVar.bad} label="quarantined" />
        </ul>
        <span
          className={cn(
            "inline-flex h-6 items-center gap-1.5 rounded-full border px-2.5 font-mono text-[11px]",
            hold ? "tint-held" : "border-line bg-panel-2 text-muted",
          )}
        >
          <Hand className="size-3" strokeWidth={1.75} /> hold {holdLabel.toLowerCase()}
        </span>
      </div>
    </GlowCard>
  );
}
