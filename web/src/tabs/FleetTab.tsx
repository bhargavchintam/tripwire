import { useMemo, useRef, useState, type MouseEvent } from "react";
import { AnimatePresence, motion, useReducedMotion } from "motion/react";
import { Database, Flame, Grid3x3, Receipt, Rows3, Timer } from "lucide-react";
import { Badge } from "../components/ui/badge";
import { Tooltip } from "../components/ui/tooltip";
import { Chip, CopyId, GlowCard, SectionHeader, Skeleton, toneVar, type Tone } from "../components/fx";
import { SqlReceipt } from "../components/analytics/SqlReceipt";
import { DASH, fmtInt, fmtMs, isNum } from "../lib/format";
import { cn } from "../lib/utils";
import type { AgentMode, FleetHeatmap } from "../lib/types";
import { useFleetTop, useHeatmap, useTripwire } from "../hooks/useTripwire";

const HOURS = 72;

/** Fields GET /fleet/heatmap also returns (checkpoint/app.py) that lib/types doesn't declare. Read-only. */
type HeatmapWire = FleetHeatmap & { cached_age_ms?: number | null; sql?: string };

// Sequential single-hue ramp for magnitude on white: porcelain-indigo -> deep signal indigo.
// Lightness falls monotonically, so order reads in greyscale too; near-zero recedes into the paper,
// the hottest hour is the deepest ink. Status colours (red / amber) stay reserved for state.
const STOPS = ["#ECEBF8", "#D9D8F4", "#C0BEEE", "#A3A0E6", "#8480DA", "#625DCB", "#413CB5"];
const ZERO = "#E6E3DC"; // activity in the hour, zero risk (warm grey: no hue = no risk)
const EMPTY = "#F3F2EE"; // no events in the hour

function hexRgb(h: string): [number, number, number] {
  const n = parseInt(h.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}
const RGB = STOPS.map(hexRgb);
/** HEAT[s] = colour for score s (1..100). Index 0 unused. */
const HEAT = Array.from({ length: 101 }, (_, s) => {
  const t = (Math.max(1, s) - 1) / 99;
  const pos = t * (RGB.length - 1);
  const i = Math.min(Math.floor(pos), RGB.length - 2);
  const f = pos - i;
  const [r, g, b] = RGB[i].map((c, k) => Math.round(c + (RGB[i + 1][k] - c) * f));
  return `rgb(${r} ${g} ${b})`;
});

function scoreColor(score: number): string {
  if (!(score > 0)) return ZERO;
  return HEAT[Math.round(Math.min(100, score))];
}

/** Same split as the checkpoint's heatmap ordering (LIVE_AGENTS + guild:*). */
const isLive = (a: string) => a === "deploy-bot" || a === "support-bot" || a.startsWith("guild:");

const MODE_TONE: Record<AgentMode, Tone> = { normal: "ok", heightened: "held", quarantined: "bad" };
const MODE_LABEL: Record<AgentMode, string> = { normal: "active", heightened: "heightened", quarantined: "quarantined" };

function hourLabel(ms: number, withDay = false): string {
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, "0");
  return withDay ? `${d.toLocaleDateString("en-US", { weekday: "short" })} ${hh}:00` : `${hh}:00`;
}

function ageLabel(ms: number): string {
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(ms < 10_000 ? 1 : 0)} s`;
}

/** Live-agent state dot (mode from the live stream). Synthetic agents get a hollow ring: no live state. */
function StateDot({ agent, mode }: { agent: string; mode: AgentMode | undefined }) {
  if (!isLive(agent)) {
    return <span aria-hidden className="size-1.5 shrink-0 rounded-full border border-line-strong" />;
  }
  const tone: Tone = mode ? MODE_TONE[mode] : "neutral";
  return (
    <Tooltip content={mode ? `${agent} · ${MODE_LABEL[mode]} now` : `${agent} · no live state yet`} side="left">
      <span
        tabIndex={0}
        aria-label={`${agent} ${mode ? MODE_LABEL[mode] : "state unknown"}`}
        className="relative inline-flex size-3 shrink-0 items-center justify-center rounded-full outline-none focus-visible:ring-2 focus-visible:ring-brand"
      >
        <span className="size-2 rounded-full" style={{ background: toneVar[tone] }} />
        {mode === "quarantined" && (
          <span
            aria-hidden
            className="absolute inset-0 rounded-full motion-safe:animate-pulse-ring"
            style={{ background: toneVar.bad }}
          />
        )}
      </span>
    </Tooltip>
  );
}

interface Hover {
  agent: string;
  hi: number;
  score: number;
  events: number;
  x: number;
  y: number;
  w: number;
  h: number;
  boxW: number;
}

const LABEL_W = 200;
const CARD_W = 248;

function Heatmap({ data }: { data: FleetHeatmap }) {
  const { state } = useTripwire();
  const modes = state.modes;
  const reduce = useReducedMotion() ?? false;
  const wrap = useRef<HTMLDivElement>(null);
  const [hover, setHover] = useState<Hover | null>(null);
  const { rows, lookup, liveCount, otherLabel } = useMemo(() => {
    const lookup = new Map<string, [number, number]>();
    for (const [a, h, score, events] of data.cells) lookup.set(`${a}:${h}`, [score, events]);
    // Server already orders live first; keep that, but pin live agents defensively.
    const idx = data.agents.map((a, i) => ({ a, i }));
    const live = idx.filter((r) => isLive(r.a));
    const others = idx.filter((r) => !isLive(r.a));
    const rows = [...live, ...others];
    // agent-NN rows are the synthetic background load; say so only when that's all the rest is.
    const otherLabel = others.every((r) => /^agent-\d+$/.test(r.a)) ? "Synthetic background fleet" : "Other agents";
    return { rows, lookup, liveCount: live.length, otherLabel };
  }, [data]);

  // The grid only re-renders when the data (or a live agent's mode) changes; hover is drawn as
  // overlays on top of it, so the column fade-in plays once per mount.
  const grid = useMemo(() => {
    const cols = { gridTemplateColumns: `${LABEL_W}px repeat(${data.hours.length}, minmax(0, 1fr))` };
    const last = data.hours.length - 1;
    const group = (label: string, n: number, first: boolean) => (
      <div className={cn("flex items-center gap-2 pb-1.5", !first && "mt-3 border-t border-line pt-3")}>
        <span className="eyebrow">{label}</span>
        <span className="font-mono text-[12px] text-dim tabular-nums">{n}</span>
      </div>
    );
    return (
      <>
        {/* hour axis: a day name at midnight, 12:00 at noon, "now" on the current hour */}
        <div className="relative grid gap-[2px] pb-2" style={cols}>
          <span />
          {data.hours.map((h, i) => {
            const hr = new Date(h).getHours();
            const label = hr === 0 ? hourLabel(h, true).split(" ")[0] : hr === 12 ? "12:00" : "";
            return (
              <span key={h} className="relative h-4">
                {label && i < last - 3 && (
                  <span
                    className={cn(
                      "absolute bottom-0 left-0 whitespace-nowrap border-l pl-1 font-mono text-[12px] leading-none",
                      hr === 0 ? "border-line-strong font-medium text-fg" : "border-line text-dim",
                    )}
                  >
                    {label}
                  </span>
                )}
                {i === last && (
                  <span className="absolute right-0 bottom-0 whitespace-nowrap font-mono text-[12px] leading-none font-medium text-brand">
                    now
                  </span>
                )}
              </span>
            );
          })}
        </div>
        <div className="flex flex-col gap-[2px]">
          {rows.map(({ a, i }, r) => {
            const live = isLive(a);
            return (
              <div key={a}>
                {r === 0 && liveCount > 0 && group("Live agents", liveCount, true)}
                {r === liveCount && rows.length > liveCount && group(otherLabel, rows.length - liveCount, liveCount === 0)}
                <div className="grid items-center gap-[2px]" style={cols}>
                  <span className="flex min-w-0 items-center gap-2 pr-3">
                    <StateDot agent={a} mode={modes[a]} />
                    <span
                      className={cn("truncate font-mono text-[12px]", live ? "font-semibold text-fg" : "text-muted")}
                      title={a}
                    >
                      {a}
                    </span>
                  </span>
                  {data.hours.map((h, hi) => {
                    const [score, events] = lookup.get(`${i}:${hi}`) ?? [0, 0];
                    return (
                      <span
                        key={h}
                        data-cell=""
                        data-a={a}
                        data-hi={hi}
                        data-s={score}
                        data-e={events}
                        className="h-3.5 rounded-[3px] motion-safe:animate-[fade-in_0.42s_ease-out_both]"
                        // Column-by-column entrance: plays once when a cell first mounts.
                        style={{
                          backgroundColor: events > 0 ? scoreColor(score) : EMPTY,
                          animationDelay: `${hi * 8}ms`,
                        }}
                        aria-label={`${a} ${hourLabel(h, true)} score ${score} events ${events}`}
                      />
                    );
                  })}
                </div>
              </div>
            );
          })}
        </div>
      </>
    );
  }, [data.hours, rows, lookup, liveCount, otherLabel, modes]);

  function onMove(e: MouseEvent<HTMLDivElement>) {
    const el = (e.target as HTMLElement).closest<HTMLElement>("[data-cell]");
    const box = wrap.current?.getBoundingClientRect();
    if (!el || !box) {
      setHover(null);
      return;
    }
    const agent = el.dataset.a ?? "";
    const hi = Number(el.dataset.hi);
    setHover((prev) => {
      if (prev && prev.agent === agent && prev.hi === hi) return prev;
      const c = el.getBoundingClientRect();
      return {
        agent,
        hi,
        score: Number(el.dataset.s),
        events: Number(el.dataset.e),
        x: c.left - box.left,
        y: c.top - box.top,
        w: c.width,
        h: c.height,
        boxW: box.width,
      };
    });
  }

  const hourMs = hover ? data.hours[hover.hi] : undefined;
  const anchorX = hover ? Math.min(Math.max(hover.x + hover.w / 2, CARD_W / 2 + 4), hover.boxW - CARD_W / 2 - 4) : 0;
  const below = hover ? hover.y < 190 : false;
  const anchorY = hover ? (below ? hover.y + hover.h + 10 : hover.y - 10) : 0;
  const follow = reduce ? { duration: 0 } : { type: "spring" as const, stiffness: 520, damping: 40, mass: 0.6 };

  return (
    <div ref={wrap} className="relative" onMouseMove={onMove} onMouseLeave={() => setHover(null)}>
      {grid}
      {hover && (
        <>
          {/* crosshair: row band + column band + ring on the cell */}
          <div
            aria-hidden
            className="pointer-events-none absolute inset-x-0 z-10 rounded-[4px] bg-[rgb(21_22_26/0.045)]"
            style={{ top: hover.y - 2, height: hover.h + 4 }}
          />
          <div
            aria-hidden
            className="pointer-events-none absolute top-0 bottom-0 z-10 rounded-[4px] bg-[rgb(21_22_26/0.035)]"
            style={{ left: hover.x - 2, width: hover.w + 4 }}
          />
          <div
            aria-hidden
            className="pointer-events-none absolute z-20 rounded-[4px] shadow-[0_0_0_2px_#fff,0_0_0_3.5px_var(--color-fg)]"
            style={{ left: hover.x, top: hover.y, width: hover.w, height: hover.h }}
          />
        </>
      )}
      {/* inspector card: springs from cell to cell, fades in/out */}
      <AnimatePresence>
        {hover && (
          <motion.div
            key="inspector"
            aria-hidden
            className="pointer-events-none absolute top-0 left-0 z-30"
            initial={{ opacity: 0, x: anchorX, y: anchorY }}
            animate={{ opacity: 1, x: anchorX, y: anchorY }}
            exit={{ opacity: 0, transition: { duration: 0.12 } }}
            transition={{ x: follow, y: follow, opacity: { duration: 0.16 } }}
          >
            <div
              className={cn(
                "-translate-x-1/2 rounded-xl border border-line bg-panel p-4 shadow-lg",
                !below && "-translate-y-full",
              )}
              style={{ width: CARD_W }}
            >
              <div className="flex items-center justify-between gap-2">
                <span className="eyebrow">{isNum(hourMs) ? hourLabel(hourMs, true) : DASH}</span>
                {isLive(hover.agent) && (
                  <span className="font-mono text-[12px] font-medium text-ok">live</span>
                )}
              </div>
              <div className="mt-1.5 truncate font-mono text-[13px] font-semibold text-fg">{hover.agent}</div>
              <div className="mt-3 grid grid-cols-2 gap-3 border-t border-line pt-3">
                <div>
                  <div className="text-[12px] text-dim">Risk score</div>
                  <div className="mt-1 flex items-center gap-2 text-[28px] leading-none font-semibold tracking-[-0.03em] tabular-nums text-fg">
                    {hover.events > 0 ? (
                      <>
                        <span
                          aria-hidden
                          className="size-3 rounded-[3px] ring-1 ring-[rgb(21_22_26/0.08)]"
                          style={{ backgroundColor: scoreColor(hover.score) }}
                        />
                        {hover.score}
                      </>
                    ) : (
                      <span className="text-dim">{DASH}</span>
                    )}
                  </div>
                </div>
                <div>
                  <div className="text-[12px] text-dim">Events</div>
                  <div className="mt-1 text-[28px] leading-none font-semibold tracking-[-0.03em] tabular-nums text-fg">
                    {fmtInt(hover.events)}
                  </div>
                </div>
              </div>
              {hover.events > 0 ? (
                <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-panel-3">
                  <div
                    className="h-full rounded-full transition-[width] duration-200 ease-out"
                    style={{
                      width: `${Math.max(2, Math.min(100, hover.score))}%`,
                      backgroundColor: scoreColor(hover.score),
                    }}
                  />
                </div>
              ) : (
                <div className="mt-3 text-[12px] text-dim">No events in this hour</div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}

function Legend() {
  const ticks = [1, 25, 50, 75, 100];
  return (
    <div className="flex flex-wrap items-center gap-x-6 gap-y-3 border-t border-line pt-4 text-[12px] text-muted">
      <span className="eyebrow">Risk score</span>
      <span className="flex min-w-[240px] flex-1 flex-col gap-1.5 sm:max-w-[380px]">
        <span
          className="block h-2.5 rounded-full ring-1 ring-[rgb(21_22_26/0.06)] ring-inset"
          style={{ background: `linear-gradient(90deg, ${STOPS.join(", ")})` }}
        />
        <span className="relative block h-3.5 font-mono">
          {ticks.map((t) => (
            <span
              key={t}
              className="absolute -translate-x-1/2 tabular-nums text-dim first:translate-x-0 last:-translate-x-full"
              style={{ left: `${((t - 1) / 99) * 100}%` }}
            >
              {t}
            </span>
          ))}
        </span>
      </span>
      <span className="flex items-center gap-2">
        <span className="inline-block size-3.5 rounded-[3px]" style={{ backgroundColor: ZERO }} /> 0 · activity, no risk
      </span>
      <span className="flex items-center gap-2">
        <span
          className="inline-block size-3.5 rounded-[3px] ring-1 ring-line ring-inset"
          style={{ backgroundColor: EMPTY }}
        />{" "}
        no events
      </span>
    </div>
  );
}

const TOP_COLS = "grid-cols-[32px_minmax(0,1.5fr)_minmax(120px,1fr)_64px_64px_80px]";

function TopRisky({ className }: { className?: string }) {
  const { data, isError, error, dataUpdatedAt } = useFleetTop(60, 10);
  const { state } = useTripwire();
  // MotionConfig only drops transforms under reduced motion; the bar width tween is ours to stop.
  const reduce = useReducedMotion() ?? false;
  return (
    <GlowCard
      reveal={2}
      className={className}
      eyebrow={
        <>
          <Flame /> Last 60 min · GET /fleet/top
        </>
      }
      title="Top risky agents"
      actions={
        <Chip tone="neutral" mono>
          every 5 s{dataUpdatedAt ? ` · ${new Date(dataUpdatedAt).toLocaleTimeString("en-US", { hour12: false })}` : ""}
        </Chip>
      }
      bodyClassName="px-0 pb-2"
    >
      {isError ? (
        <div className="mx-5 rounded-xl border border-held-line bg-held-soft px-4 py-3 text-[13px] text-held">
          GET /fleet/top failed: {(error as Error).message}
        </div>
      ) : !data ? (
        <div className="flex flex-col gap-3 px-5 py-2" role="status" aria-label="Loading top risky agents">
          {Array.from({ length: 5 }, (_, i) => (
            <Skeleton key={i} className="h-5" />
          ))}
        </div>
      ) : data.length === 0 ? (
        <div className="px-5 py-4 text-[13px] text-dim">No activity in the window.</div>
      ) : (
        <div className="flex flex-col">
          <div
            className={cn(
              "grid h-10 items-center gap-3 border-y border-line bg-panel-2 px-5 text-[12px] font-medium text-dim",
              TOP_COLS,
            )}
          >
            <span>#</span>
            <span>Agent</span>
            <span>Risk score</span>
            <span className="text-right">Events</span>
            <span className="text-right">Denied</span>
            <span className="text-right">Ext. posts</span>
          </div>
          {data.map((r, i) => (
            <motion.div
              key={r.agent_id}
              layout="position"
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{
                y: { type: "spring", stiffness: 300, damping: 30, delay: Math.min(i * 0.04, 0.36) },
                opacity: { duration: 0.24, delay: Math.min(i * 0.04, 0.36) },
                layout: { type: "spring", stiffness: 300, damping: 32 },
              }}
              className={cn(
                "grid min-h-12 items-center gap-3 border-b border-line px-5 py-2 text-sm transition-colors duration-150 last:border-b-0 hover:bg-panel-3/70",
                TOP_COLS,
              )}
            >
              <span className="font-mono text-[12px] tabular-nums text-dim">{String(i + 1).padStart(2, "0")}</span>
              <span className="flex min-w-0 items-center gap-2">
                <StateDot agent={r.agent_id} mode={state.modes[r.agent_id]} />
                <CopyId
                  value={r.agent_id}
                  className={cn("min-w-0 text-[13px]", isLive(r.agent_id) ? "font-semibold text-fg" : "text-muted")}
                />
              </span>
              <span className="flex items-center gap-3">
                <span className="h-1.5 flex-1 overflow-hidden rounded-full bg-panel-3">
                  <motion.span
                    className="block h-full rounded-full"
                    style={{ backgroundColor: scoreColor(r.score) }}
                    initial={{ width: 0 }}
                    animate={{ width: `${Math.max(0, Math.min(100, r.score))}%` }}
                    transition={
                      reduce
                        ? { duration: 0 }
                        : { duration: 0.7, ease: [0.22, 1, 0.36, 1], delay: Math.min(0.12 + i * 0.04, 0.5) }
                    }
                  />
                </span>
                <span className="w-8 text-right text-sm font-semibold tabular-nums text-fg">{r.score}</span>
              </span>
              <span className="text-right text-[13px] tabular-nums text-fg">{fmtInt(r.events)}</span>
              <span className={cn("text-right text-[13px] tabular-nums", r.denied ? "font-semibold text-bad" : "text-dim")}>
                {fmtInt(r.denied)}
              </span>
              <span className="text-right text-[13px] tabular-nums text-fg">{fmtInt(r.external_posts)}</span>
            </motion.div>
          ))}
        </div>
      )}
    </GlowCard>
  );
}

/** Formula as returned by the checkpoint, only re-flowed: one statement per line, one term per line. */
function FormulaText({ formula }: { formula: string }) {
  const lines = formula
    .split(/;\s+/)
    .map((stmt) => stmt.replace(/\s\+\s/g, " +\n      "))
    .join(";\n");
  return (
    <pre className="surface-raised overflow-x-auto px-3.5 py-3 font-mono text-[12px] leading-5 whitespace-pre-wrap break-words text-fg/85">
      {lines}
    </pre>
  );
}

function FormulaCard({ data, className }: { data: HeatmapWire; className?: string }) {
  return (
    <GlowCard
      tone="info"
      glow="soft"
      reveal={3}
      className={className}
      eyebrow={
        <>
          <Receipt /> Receipt · ClickHouse
        </>
      }
      title="How risk is scored"
      description={
        <>
          {data.agents.length} agents × {HOURS} h from {fmtInt(data.rows_read)} rows in {fmtMs(data.query_ms)}
        </>
      }
      bodyClassName="flex flex-col gap-3"
    >
      {data.formula ? <FormulaText formula={data.formula} /> : <span className="text-dim">{DASH}</span>}
      <SqlReceipt sql={data.sql} />
    </GlowCard>
  );
}

function ReceiptChips({ data }: { data: HeatmapWire }) {
  const age = data.cached_age_ms;
  return (
    <div className="flex flex-wrap items-center gap-1.5">
      <Tooltip content="ClickHouse query time for this heatmap">
        <Chip tone="info" mono icon={<Timer />}>
          {fmtMs(data.query_ms)}
        </Chip>
      </Tooltip>
      <Tooltip content="Rows ClickHouse read to build it">
        <Chip tone="glass" mono icon={<Rows3 />}>
          {fmtInt(data.rows_read)} rows
        </Chip>
      </Tooltip>
      {isNum(age) && (
        <Tooltip
          content={
            age > 0
              ? "Served from the checkpoint's 30 s heatmap cache; query time and rows are from the original query"
              : "Fresh query (not cached)"
          }
        >
          <Chip tone="glass" mono icon={<Database />}>
            {age > 0 ? `cached ${ageLabel(age)}` : "fresh"}
          </Chip>
        </Tooltip>
      )}
    </div>
  );
}

function HeatmapSkeleton() {
  return (
    <div role="status" aria-label="Querying ClickHouse" className="flex flex-col gap-[6px] py-1">
      <span className="eyebrow mb-2">Querying ClickHouse…</span>
      {Array.from({ length: 10 }, (_, i) => (
        <div key={i} className="flex items-center gap-3">
          <Skeleton className="h-3.5 w-[180px] shrink-0" />
          <Skeleton className="h-3.5 flex-1 rounded-[3px]" />
        </div>
      ))}
    </div>
  );
}

export function FleetTab() {
  const { data: raw, isError, error, isLoading } = useHeatmap(HOURS);
  const data = raw as HeatmapWire | undefined;
  const ok = !!data && !isError;
  return (
    <div className="flex flex-col gap-6">
      <SectionHeader
        eyebrow={`Fleet · risk over ${HOURS} h`}
        eyebrowTone="info"
        pre="Fleet"
        em="at a glance"
        description="Hourly risk score for every agent, computed in ClickHouse over the whole fleet. Live agents are pinned on top."
      />
      <div className="grid grid-cols-12 gap-4">
        <GlowCard
          reveal={1}
          className="col-span-12"
          eyebrow={
            <>
              <Grid3x3 /> Fleet risk heatmap · GET /fleet/heatmap
            </>
          }
          title={ok ? `${data.agents.length} agents × ${HOURS} hours` : `Fleet risk · ${HOURS} hours`}
          actions={
            <>
              {data?.mock ? <Badge variant="held">mock</Badge> : null}
              {ok ? <ReceiptChips data={data} /> : null}
            </>
          }
          footer={ok && data.agents.length > 0 ? <Legend /> : undefined}
          bodyClassName="overflow-x-auto pt-2"
        >
          {isError ? (
            <div className="rounded-xl border border-held-line bg-held-soft px-4 py-3 text-[13px] text-held">
              GET /fleet/heatmap failed: {(error as Error).message}
            </div>
          ) : isLoading || !data ? (
            <HeatmapSkeleton />
          ) : data.agents.length === 0 ? (
            <div className="py-6 text-[13px] text-dim">No events in the window.</div>
          ) : (
            <div className="min-w-[880px]">
              <Heatmap data={data} />
            </div>
          )}
        </GlowCard>
        <TopRisky className={cn("col-span-12", ok && "xl:col-span-8")} />
        {ok && <FormulaCard data={data} className="col-span-12 xl:col-span-4" />}
      </div>
    </div>
  );
}

export default FleetTab;
