import { useEffect, useMemo, useRef, useState } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { KeyRound, ListFilter, Radio, X } from "lucide-react";
import { ReasonText, ResultBadge } from "./badges";
import { GlowCard, Skeleton } from "./fx";
import { Tooltip } from "./ui/tooltip";
import { eventKey, isHoldReason, isSnapshotSwap } from "./live/streamChange";
import { fmtClock } from "../lib/format";
import { cn } from "../lib/utils";
import { useTripwire } from "../hooks/useTripwire";
import { AgentStateAtT, TimeScrubber, TimeTravelChip } from "./timetravel/TimeScrubber";
import { agentStateAt, eventTimes, eventsUpTo } from "./timetravel/timeTravel";
import { TaintChip, taintOf } from "./incidents/Timeline";

const COLS = "grid grid-cols-[104px_148px_112px_minmax(0,1fr)_136px_108px] items-center gap-3";
const ROW_H = 40;
/** Rows kept in presenter mode (P): the newest calls (in Act 3, deploy-bot's attack and support-bot's held
 * send, with their "from ticket:4821" chips; the two ticket reads stay visible in the ticket exhibit). */
const SLIM_ROWS = 6;

/**
 * Virtualized live event table: last 200 tool events of live agents (newest first), filterable by agent.
 * D9 time travel: a scrubber over these same real events; while scrubbed the table shows only rows at or
 * before T plus each agent's state at T (client-side tallies). Live resumes the stream view.
 * `slim` (presenter mode): the newest SLIM_ROWS rows only, always live, no filter chips or scrubber.
 */
export function EventTable({ slim = false }: { slim?: boolean } = {}) {
  const { state } = useTripwire();
  const all = state.events;
  const [agent, setAgent] = useState<string | null>(null);
  const [at, setAt] = useState<number | null>(null); // null = live
  const times = useMemo(() => eventTimes(all), [all]);
  // A reset/snapshot that empties the table (or leaves < 2 events) returns to live.
  const t = slim || times.length < 2 ? null : at;
  const scoped = useMemo(() => eventsUpTo(all, t), [all, t]);

  // Per-agent counts of the rows on screen (display only: a filter, never a metric).
  const agents = useMemo(() => {
    const m = new Map<string, number>();
    for (const e of scoped) m.set(e.agent_id, (m.get(e.agent_id) ?? 0) + 1);
    return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  }, [scoped]);
  const active = !slim && agent && agents.some(([a]) => a === agent) ? agent : null;
  const rows = useMemo(() => {
    const r = active ? scoped.filter((e) => e.agent_id === active) : scoped;
    return slim ? r.slice(0, SLIM_ROWS) : r;
  }, [scoped, active, slim]);
  const atT = useMemo(
    () => (t === null ? [] : agentStateAt(scoped).filter((a) => !active || a.agent_id === active)),
    [scoped, t, active],
  );

  const parentRef = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => ROW_H,
    overscan: 12,
    getItemKey: (i) => eventKey(rows[i]),
  });

  // The head row flashes once when a tool_event arrives live (never for snapshot history).
  const [flash, setFlash] = useState<number | null>(null);
  const prev = useRef({ head: all[0], swap: { alerts: state.alerts, queryTimings: state.queryTimings } });
  useEffect(() => {
    const p = prev.current;
    const swap = { alerts: state.alerts, queryTimings: state.queryTimings };
    prev.current = { head: all[0], swap };
    if (all[0] && all[0] !== p.head && !isSnapshotSwap(p.swap, swap)) setFlash(eventKey(all[0]));
  }, [all, state.alerts, state.queryTimings]);

  const toggle = (a: string) => setAgent((cur) => (cur === a ? null : a));
  const loading = state.connection === "connecting" && all.length === 0;

  return (
    <GlowCard
      className="h-full"
      reveal={11}
      eyebrow={
        <>
          <Radio /> Stream · tool_event
        </>
      }
      title={slim ? "Latest tool calls" : "Live tool calls"}
      actions={
        <span className="flex flex-wrap items-center justify-end gap-2">
          {t !== null && <TimeTravelChip t={t} />}
          <span className="font-mono text-[12px] text-dim">
            {rows.length} shown{active || t !== null || slim ? ` of ${all.length}` : ""} · live agents only
          </span>
        </span>
      }
      bodyClassName="flex min-h-0 flex-col gap-3 pt-1"
    >
      {/* agent filter chips */}
      {!slim && agents.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by agent">
          <ListFilter className="mr-0.5 size-4 text-dim" strokeWidth={1.75} aria-hidden />
          <button
            type="button"
            onClick={() => setAgent(null)}
            aria-pressed={!active}
            className={cn(
              "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-[13px] font-medium transition-[background-color,border-color,color] duration-200",
              !active ? "tint-brand" : "border-line bg-panel text-muted hover:border-line-strong hover:text-fg",
            )}
          >
            All <span className="font-mono text-[12px] opacity-75">{scoped.length}</span>
          </button>
          {agents.map(([a, n]) => {
            const on = active === a;
            return (
              <button
                key={a}
                type="button"
                onClick={() => toggle(a)}
                aria-pressed={on}
                className={cn(
                  "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-full border px-3 font-mono text-[12px] transition-[background-color,border-color,color] duration-200",
                  on ? "tint-brand" : "border-line bg-panel text-muted hover:border-line-strong hover:text-fg",
                )}
              >
                {a}
                <span className="opacity-70">{n}</span>
                {on && <X className="size-3" strokeWidth={2} aria-hidden />}
              </button>
            );
          })}
        </div>
      )}

      {!slim && <TimeScrubber times={times} t={t} onChange={setAt} />}
      {t !== null && <AgentStateAtT agents={atT} t={t} />}

      <div className="overflow-x-auto rounded-xl border border-line">
        <div className="min-w-[760px]">
          <div className={`${COLS} border-b border-line bg-panel-2 px-4 py-2.5 text-[12px] font-medium text-dim`}>
            <span>Time</span>
            <span>Agent</span>
            <span>Action</span>
            <span>Target</span>
            <span>Result</span>
            <span>Reason</span>
          </div>
          <div ref={parentRef} className={cn(slim ? "h-[240px]" : "h-[400px]", "overflow-y-auto")}>
            {loading ? (
              <div role="status" aria-label="Loading events" className="flex flex-col">
                {Array.from({ length: slim ? SLIM_ROWS : 8 }, (_, i) => (
                  <div key={i} className={`${COLS} h-10 border-b border-line/70 px-4`}>
                    <Skeleton className="h-3 w-16" />
                    <Skeleton className="h-3 w-20" />
                    <Skeleton className="h-3 w-16" />
                    <Skeleton className="h-3 w-40" />
                    <Skeleton className="h-5 w-20 rounded-full" />
                    <Skeleton className="h-3 w-12" />
                  </div>
                ))}
              </div>
            ) : rows.length === 0 ? (
              <div className="grid h-full place-items-center p-6 text-center">
                <div className="flex flex-col items-center gap-2 text-dim">
                  <Radio className="size-5" strokeWidth={1.75} />
                  <span className="text-[14px]">
                    {t !== null ? "No retained tool calls at or before this time." : "No tool calls yet."}
                  </span>
                </div>
              </div>
            ) : (
              <div style={{ height: v.getTotalSize(), position: "relative" }}>
                {v.getVirtualItems().map((item) => {
                  const e = rows[item.index];
                  const denied = e.result === "denied";
                  const held = denied && isHoldReason(e.reason);
                  const taint = taintOf(e);
                  const isFlash = t === null && flash === item.key && e === all[0];
                  return (
                    <div
                      key={item.key}
                      className={cn(
                        COLS,
                        "group/row absolute left-0 top-0 w-full border-b border-line/70 px-4 text-[13px] transition-colors duration-150",
                        denied
                          ? held
                            ? "bg-held-soft/45 shadow-[inset_2px_0_0_var(--color-held)] hover:bg-held-soft/80"
                            : "bg-bad-soft/45 shadow-[inset_2px_0_0_var(--color-bad)] hover:bg-bad-soft/80"
                          : cn("hover:bg-panel-3/70", item.index % 2 === 1 && "bg-[rgb(21_22_26/0.014)]"),
                      )}
                      style={{ height: item.size, transform: `translateY(${item.start}px)` }}
                    >
                      {isFlash && (
                        <span
                          key={flash}
                          aria-hidden
                          className={cn(
                            "pointer-events-none absolute inset-0 animate-[fade-out_1.4s_ease-out_both]",
                            denied ? (held ? "bg-held-soft" : "bg-bad-soft") : "bg-ok-soft",
                          )}
                        />
                      )}
                      <span className="relative font-mono text-[12px] text-dim tabular-nums">{fmtClock(e.ts_ms)}</span>
                      <span className="relative min-w-0">
                        {slim ? (
                          <span className="block truncate font-medium text-fg">{e.agent_id}</span>
                        ) : (
                          <Tooltip content={active === e.agent_id ? "Show all agents" : `Show only ${e.agent_id}`} delay={400}>
                            <button
                              type="button"
                              onClick={() => toggle(e.agent_id)}
                              className="-mx-1.5 max-w-full cursor-pointer truncate rounded-md px-1.5 py-0.5 text-left font-medium text-fg transition-colors duration-150 hover:bg-panel hover:text-brand group-hover/row:bg-panel/60"
                            >
                              {e.agent_id}
                            </button>
                          </Tooltip>
                        )}
                      </span>
                      <span className="relative truncate font-mono text-[12.5px] text-fg">{e.action}</span>
                      <span className="relative flex min-w-0 items-center gap-1.5 font-mono text-[12.5px] text-muted" title={e.target}>
                        {e.honeytoken_hit ? (
                          <Tooltip content="Honeytoken: a decoy secret was touched">
                            <KeyRound className="size-3.5 shrink-0 text-held" aria-label="honeytoken" strokeWidth={1.75} />
                          </Tooltip>
                        ) : null}
                        <span className="truncate">{e.target}</span>
                        {taint && <TaintChip source={taint} className="max-w-[min(200px,60%)] shrink-0" />}
                      </span>
                      <span className="relative">
                        <ResultBadge result={e.result} reason={e.reason} />
                      </span>
                      <span className="relative truncate">
                        <ReasonText reason={e.reason} />
                      </span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    </GlowCard>
  );
}
