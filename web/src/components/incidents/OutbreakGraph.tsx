import { useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Ban, FileWarning, Users } from "lucide-react";
import { Chip, CopyId, EASE_OUT, Eyebrow, SPRING_SOFT } from "../fx";
import { Tooltip } from "../ui/tooltip";
import { ModeBadge } from "../badges";
import { DASH } from "../../lib/format";
import type { AgentMode, Outbreak } from "../../lib/types";

/** Width of an element, kept current with a ResizeObserver (the graph is laid out in px). */
function useWidth<T extends HTMLElement>() {
  const ref = useRef<T>(null);
  const [w, setW] = useState(0);
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    setW(el.getBoundingClientRect().width);
    const ro = new ResizeObserver((entries) => setW(entries[0]?.contentRect.width ?? 0));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  return [ref, w] as const;
}

const SRC_X = 34; // centre of the patient-zero node
const SRC_R = 22;
const GAP = 52; // vertical spacing between exposed agents
const LABEL_W = 230; // room reserved right of the agent nodes for id + mode
const NODE_R = 9;

/**
 * Patient zero (left) -> one curved edge per exposed agent. Only real nodes are drawn: one node per
 * `exposed_agents` entry, no placeholders, no decorative links. Edges draw in on mount and again only
 * when the outbreak itself changes (the svg is keyed by the source + agent list).
 */
function OrbitGraph({ ob, modes }: { ob: Outbreak; modes?: Record<string, AgentMode> }) {
  const [ref, w] = useWidth<HTMLDivElement>();
  const reduce = useReducedMotion();
  const agents = ob.exposed_agents;
  const n = agents.length;
  const H = Math.max(120, (n - 1) * GAP + 88);
  const cy = H / 2;
  // Agents sit on a gentle arc to the right of the source; the arc is layout only, never drawn.
  const R = Math.max(120, Math.min(300, w - SRC_X - LABEL_W - 16));
  const nodes = agents.map((a, i) => {
    const y = cy + (i - (n - 1) / 2) * GAP;
    const dy = y - cy;
    const x = SRC_X + Math.sqrt(Math.max(R * R - dy * dy, (0.55 * R) ** 2));
    return { a, x, y };
  });
  const runKey = `${ob.source_id}|${agents.join(",")}`;
  const from = <T,>(v: T): T | false => (reduce ? false : v);
  const step = (i: number) => 0.15 + Math.min(i * 0.09, 0.6);

  return (
    <div ref={ref} className="relative w-full" style={{ height: H }}>
      {w > 0 && (
        <>
          <svg key={runKey} aria-hidden width={w} height={H} className="absolute inset-0 overflow-visible">
            {nodes.map((nd, i) => {
              const sx = SRC_X + SRC_R + 4;
              const ex = nd.x - NODE_R - 3;
              const dx = ex - sx;
              const d = `M ${sx} ${cy} C ${sx + dx * 0.5} ${cy}, ${sx + dx * 0.5} ${nd.y}, ${ex} ${nd.y}`;
              const delay = step(i);
              return (
                <g key={nd.a}>
                  {/* faint track, then the edge drawing over it */}
                  <path d={d} fill="none" stroke="var(--color-line)" strokeWidth={1.5} />
                  <motion.path
                    d={d}
                    fill="none"
                    stroke="var(--color-held)"
                    strokeWidth={1.5}
                    strokeLinecap="round"
                    initial={from({ pathLength: 0, opacity: 0 })}
                    animate={{ pathLength: 1, opacity: 1 }}
                    transition={{ duration: 0.7, ease: EASE_OUT, delay }}
                  />
                  <motion.g
                    initial={from({ opacity: 0, scale: 0.5 })}
                    animate={{ opacity: 1, scale: 1 }}
                    transition={{ ...SPRING_SOFT, delay: delay + 0.55 }}
                  >
                    <circle cx={nd.x} cy={nd.y} r={NODE_R + 4} fill="var(--color-held-soft)" />
                    <circle cx={nd.x} cy={nd.y} r={NODE_R} fill="var(--color-panel)" stroke="var(--color-held-line)" strokeWidth={1.5} />
                    <circle cx={nd.x} cy={nd.y} r={3.5} fill="var(--color-held)" />
                  </motion.g>
                </g>
              );
            })}
          </svg>

          {/* patient zero node */}
          <Tooltip content={<>Patient zero <span className="font-mono text-muted">· {ob.source_id}</span></>}>
            <div
              className="absolute flex size-11 items-center justify-center rounded-full border border-bad-line bg-panel text-bad shadow-sm ring-4 ring-bad-soft"
              style={{ left: SRC_X - SRC_R, top: cy - SRC_R }}
            >
              <FileWarning className="size-[18px]" strokeWidth={1.75} aria-hidden />
            </div>
          </Tooltip>

          {/* agent labels (HTML, so long ids truncate instead of overflowing) */}
          {nodes.map((nd, i) => (
            <motion.div
              key={`${runKey}-${nd.a}`}
              className="absolute flex -translate-y-1/2 items-center gap-2"
              style={{ left: nd.x + NODE_R + 10, top: nd.y, maxWidth: Math.max(80, w - nd.x - NODE_R - 12) }}
              initial={from({ opacity: 0, x: -6 })}
              animate={{ opacity: 1, x: 0 }}
              transition={{ duration: 0.32, ease: EASE_OUT, delay: step(i) + 0.6 }}
            >
              <CopyId
                value={nd.a}
                label={<span className="font-sans text-sm font-semibold tracking-[-0.01em]">{nd.a}</span>}
                className="min-w-0"
              />
              {modes && <ModeBadge mode={modes[nd.a] ?? "heightened"} className="shrink-0" />}
            </motion.div>
          ))}
          {n === 0 && (
            <div className="absolute text-sm text-dim" style={{ left: SRC_X + SRC_R + 24, top: cy - 10 }}>
              {DASH}
            </div>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Outbreak body shared by the Live panel and the incident views: patient zero, a small node graph to
 * the exposed agents (edges draw in), and the destinations blocked fleet-wide as denied-tint chips.
 * `modes` (live agent modes) adds a mode pill per agent; `sourceNote` is a muted line under patient zero.
 */
export function OutbreakView({
  ob,
  modes,
  sourceNote,
  extra,
}: {
  ob: Outbreak;
  modes?: Record<string, AgentMode>;
  sourceNote?: ReactNode;
  extra?: ReactNode;
}) {
  const reduce = useReducedMotion();
  const n = ob.exposed_agents.length;
  const blocked = ob.blocked_destinations;
  return (
    <div className="@container">
      <div className="grid gap-4 @2xl:grid-cols-[minmax(0,1fr)_minmax(240px,0.55fr)]">
        <div className="min-w-0">
          <div className="flex flex-wrap items-center gap-2">
            <Eyebrow>Patient zero</Eyebrow>
            <Chip tone="bad" mono size="md" icon={<FileWarning />} className="max-w-full" title={ob.source_id}>
              <span className="truncate">{ob.source_id}</span>
            </Chip>
          </div>
          {sourceNote ? <div className="mt-1.5 text-[13px] text-muted">{sourceNote}</div> : null}
          <Eyebrow icon={<Users />} className="mt-5">
            Exposed agents · <span className="tabular-nums">{n}</span>
          </Eyebrow>
          <div className="mt-2">
            <OrbitGraph ob={ob} modes={modes} />
          </div>
        </div>
        <div className="flex min-w-0 flex-col rounded-xl border border-line bg-panel-2 p-4">
          <Eyebrow icon={<Ban />} className="mb-3">
            Blocked fleet-wide · denylist
          </Eyebrow>
          <div className="flex flex-wrap gap-1.5">
            {blocked.length === 0 && <span className="text-sm text-dim">{DASH}</span>}
            {blocked.map((h, i) => (
              <motion.span
                key={h}
                className="inline-flex max-w-full"
                initial={reduce ? false : { opacity: 0, y: 4, scale: 0.97 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                transition={{ ...SPRING_SOFT, delay: 0.3 + Math.min((n + i) * 0.06, 0.8) }}
              >
                <Tooltip content={<>Denied for every agent <span className="font-mono text-muted">· {h}</span></>}>
                  <Chip tone="bad" mono size="md" icon={<Ban />} className="max-w-full">
                    <span className="truncate">{h}</span>
                  </Chip>
                </Tooltip>
              </motion.span>
            ))}
          </div>
          {extra}
        </div>
      </div>
    </div>
  );
}
