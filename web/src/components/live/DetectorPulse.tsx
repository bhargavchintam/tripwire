import { useEffect, useState } from "react";
import { useTripwire } from "../../hooks/useTripwire";
import { fmtClock, fmtMs } from "../../lib/format";
import { cn } from "../../lib/utils";
import { Tooltip } from "../ui/tooltip";

/** A heartbeat younger than this counts as "the detector is running right now". */
const FRESH_MS = 3000;

function fmtAgo(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  if (s < 60) return `${s} s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min ago`;
  return `${Math.floor(m / 60)} h ago`;
}

/**
 * "Detector · last pass 9 ms · 1 s ago" from the latest real detector heartbeat (SSE metrics,
 * source 'detector'). ms = sum of that pass's query_timings_ms; "ago" from the SSE event ts_ms.
 * Pulsing dot only while the last heartbeat is under 3 s old. No heartbeat -> says so.
 */
export function DetectorPulse({ className }: { className?: string }) {
  const hb = useTripwire().state.detectorHeartbeat;
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!hb) return;
    setNow(Date.now());
    const t = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(t);
  }, [hb]);

  const base =
    "inline-flex h-7 shrink-0 items-center gap-2 rounded-full border px-3 font-mono text-[12px] leading-none tabular-nums shadow-sm";

  if (!hb) {
    return (
      <Tooltip content="No detector heartbeat received on the stream yet. Source: SSE metrics (source=detector).">
        <span tabIndex={0} className={cn(base, "tint-neutral focus-ring", className)}>
          <span aria-hidden className="size-1.5 rounded-full bg-line-strong" />
          Detector · no heartbeat yet
        </span>
      </Tooltip>
    );
  }

  const entries = Object.entries(hb.timings);
  const passMs = entries.length ? entries.reduce((s, [, v]) => s + v, 0) : null;
  const age = Math.max(0, now - hb.ts_ms);
  const fresh = age < FRESH_MS;
  const iteration = hb.metrics?.iteration;

  return (
    <Tooltip
      content={
        <>
          Latest detector heartbeat at {fmtClock(hb.ts_ms, false)}
          {typeof iteration === "number" ? ` (pass #${iteration})` : ""}.{" "}
          {entries.length
            ? `Query timings: ${entries.map(([k, v]) => `${k} ${fmtMs(v)}`).join(", ")}.`
            : "This heartbeat carried no query timings."}{" "}
          Source: SSE metrics (source=detector).
        </>
      }
    >
      <span
        tabIndex={0}
        role="status"
        aria-label={`Detector, last pass ${passMs === null ? "no timings" : fmtMs(passMs)}, ${fmtAgo(age)}`}
        className={cn(base, fresh ? "tint-info" : "tint-neutral", "focus-ring", className)}
      >
        <span className="relative flex size-2" aria-hidden>
          {fresh && <span className="absolute inline-flex size-full animate-pulse-ring rounded-full bg-info" />}
          <span className={cn("relative inline-flex size-2 rounded-full", fresh ? "bg-info" : "bg-line-strong")} />
        </span>
        <span>
          Detector · {passMs === null ? "heartbeat" : `last pass ${fmtMs(passMs)}`} ·{" "}
          <span className={fresh ? undefined : "text-dim"}>{fmtAgo(age)}</span>
        </span>
      </span>
    </Tooltip>
  );
}
