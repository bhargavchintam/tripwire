import { useRef } from "react";
import { useVirtualizer } from "@tanstack/react-virtual";
import { KeyRound } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { ReasonText, ResultBadge } from "./badges";
import { fmtClock } from "../lib/format";
import { useTripwire } from "../hooks/useTripwire";

const COLS = "grid grid-cols-[96px_110px_112px_minmax(0,1fr)_128px_96px] items-center gap-2";

/** Virtualized live event table: last 200 tool events of live agents (newest first). */
export function EventTable() {
  const { state } = useTripwire();
  const rows = state.events;
  const parentRef = useRef<HTMLDivElement>(null);
  const v = useVirtualizer({
    count: rows.length,
    getScrollElement: () => parentRef.current,
    estimateSize: () => 34,
    overscan: 12,
  });

  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader>
        <CardTitle>Live tool calls</CardTitle>
        <span className="font-mono text-xs text-dim">{rows.length} shown · live agents only</span>
      </CardHeader>
      <CardContent className="px-0 pb-0">
        <div className={`${COLS} border-b border-line px-4 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted`}>
          <span>Time</span>
          <span>Agent</span>
          <span>Action</span>
          <span>Target</span>
          <span>Result</span>
          <span>Reason</span>
        </div>
        <div ref={parentRef} className="h-[340px] overflow-y-auto">
          {rows.length === 0 ? (
            <div className="p-6 text-center text-sm text-dim">No tool calls yet.</div>
          ) : (
            <div style={{ height: v.getTotalSize(), position: "relative" }}>
              {v.getVirtualItems().map((item) => {
                const e = rows[item.index];
                const denied = e.result === "denied";
                return (
                  <div
                    key={item.key}
                    className={`${COLS} absolute left-0 top-0 w-full border-b border-line/50 px-4 text-sm ${
                      denied ? "bg-bad/[0.07]" : ""
                    }`}
                    style={{ height: item.size, transform: `translateY(${item.start}px)` }}
                  >
                    <span className="font-mono text-xs text-muted tabular-nums">{fmtClock(e.ts_ms)}</span>
                    <span className="truncate font-mono text-xs font-semibold">{e.agent_id}</span>
                    <span className="truncate font-mono text-xs">{e.action}</span>
                    <span className="flex min-w-0 items-center gap-1 font-mono text-xs text-muted" title={e.target}>
                      {e.honeytoken_hit ? <KeyRound className="size-3.5 shrink-0 text-held" aria-label="honeytoken" /> : null}
                      <span className="truncate">{e.target}</span>
                    </span>
                    <span>
                      <ResultBadge result={e.result} reason={e.reason} />
                    </span>
                    <span className="truncate">
                      <ReasonText reason={e.reason} />
                    </span>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      </CardContent>
    </Card>
  );
}
