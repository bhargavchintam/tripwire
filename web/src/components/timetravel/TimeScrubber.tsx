// D9 time travel UI for the Live tool-calls table: a scrubber over this page's real events, a
// "time travel" chip, and the per-agent state at T. Client-side only; every value is a tally of
// real rows at or before T. Returning to Live resumes the stream view.
import { History, Radio } from "lucide-react";
import { ResultBadge } from "../badges";
import { Chip } from "../fx";
import { Tooltip } from "../ui/tooltip";
import { fmtClock } from "../../lib/format";
import { cn } from "../../lib/utils";
import { indexAt, type AgentAtT } from "./timeTravel";

/** "time travel · 12:03:04.123" (only while scrubbed). */
export function TimeTravelChip({ t }: { t: number }) {
  return (
    <Chip tone="info" mono icon={<History />} className="animate-fade-in">
      time travel · {fmtClock(t)}
    </Chip>
  );
}

/** Slider from the first to the last retained event; each step lands on a real event time. */
export function TimeScrubber({
  times,
  t,
  onChange,
}: {
  times: number[]; // ascending
  t: number | null; // null = live
  onChange: (t: number | null) => void;
}) {
  const n = times.length;
  if (n < 2) return null;
  const live = t === null;
  const idx = live ? n - 1 : indexAt(times, t);
  const first = times[0];
  const last = times[n - 1];
  return (
    <div
      className={cn(
        "flex flex-wrap items-center gap-x-3 gap-y-2 rounded-xl border px-3 py-2 transition-colors duration-200",
        live ? "border-line bg-panel-2" : "border-info-line bg-info-soft/40",
      )}
    >
      <Tooltip content="Time travel: scrub through the tool calls this page has received (client-side, nothing refetched or invented). Esc returns to Live.">
        <span className="inline-flex items-center gap-1.5 text-[12px] font-medium text-muted">
          <History className={cn("size-4", live ? "text-dim" : "text-info")} strokeWidth={1.75} aria-hidden />
          Time travel
        </span>
      </Tooltip>
      <span className="font-mono text-[11.5px] text-dim tabular-nums">{fmtClock(first)}</span>
      <input
        type="range"
        min={0}
        max={n - 1}
        step={1}
        value={idx}
        onChange={(e) => onChange(times[Number(e.currentTarget.value)] ?? null)}
        onKeyDown={(e) => {
          if (e.key === "Escape" && !live) {
            e.preventDefault();
            onChange(null);
          }
        }}
        aria-label="Time travel through this session's tool calls"
        aria-valuetext={live ? "Live" : fmtClock(t)}
        className="h-1.5 min-w-[140px] flex-1 cursor-pointer accent-[var(--color-brand)]"
      />
      <span className="font-mono text-[11.5px] text-dim tabular-nums">{fmtClock(last)}</span>
      <button
        type="button"
        onClick={() => onChange(null)}
        aria-pressed={live}
        className={cn(
          "inline-flex h-7 cursor-pointer items-center gap-1.5 rounded-full border px-3 text-[12.5px] font-medium transition-[background-color,border-color,color] duration-200",
          live ? "tint-ok" : "border-line bg-panel text-muted hover:border-line-strong hover:text-fg",
        )}
      >
        <Radio className="size-3.5" strokeWidth={1.75} aria-hidden />
        Live
      </button>
    </div>
  );
}

/** Compact per-agent state at T: allowed / denied tallies and the last action up to T. */
export function AgentStateAtT({ agents, t }: { agents: AgentAtT[]; t: number }) {
  if (agents.length === 0) return null;
  return (
    <div role="group" aria-label={`Agent state at ${fmtClock(t)}`} className="flex flex-wrap gap-2 animate-fade-in">
      {agents.map((a) => (
        <div
          key={a.agent_id}
          className="flex min-w-0 max-w-full items-center gap-2 rounded-lg border border-line bg-panel px-2.5 py-1.5 text-[12.5px] shadow-sm"
        >
          <span className="font-medium text-fg">{a.agent_id}</span>
          <span className="font-mono text-[12px] text-ok tabular-nums">{a.allowed} allowed</span>
          <span className="font-mono text-[12px] text-bad tabular-nums">{a.denied} denied</span>
          <span aria-hidden className="h-3.5 w-px bg-line-strong" />
          <span className="text-[12px] text-dim">last</span>
          <span
            className="min-w-0 max-w-[260px] truncate font-mono text-[12px] text-muted"
            title={`${a.last.action} ${a.last.target} · ${fmtClock(a.last.ts_ms)}`}
          >
            {a.last.action} <span className="text-dim">→</span> {a.last.target}
          </span>
          <ResultBadge result={a.last.result} reason={a.last.reason} />
        </div>
      ))}
    </div>
  );
}
