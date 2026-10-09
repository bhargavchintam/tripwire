import { ChevronRight, Database } from "lucide-react";
import { Badge } from "./ui/badge";
import { Tooltip } from "./ui/tooltip";
import { Count, InkMarker } from "./fx";
import { CopyButton } from "./incidents/CopyButton";
import { SqlText } from "./incidents/Receipts";
import { DASH, isNum } from "../lib/format";
import { cn } from "../lib/utils";
import type { BacktestResult } from "../lib/types";

/** Same precision rule as fmtMs (< 10 ms keeps one decimal). */
const msDigits = (v: number | null | undefined) => (isNum(v) && v < 10 ? 1 : 0);

/** One labelled figure in the backtest footer (value straight from BacktestResult; null -> DASH). */
function Figure({
  label,
  value,
  tone,
  tip,
}: {
  label: string;
  value: number | null | undefined;
  tone: "ok" | "bad" | "neutral";
  tip: string;
}) {
  return (
    <Tooltip content={tip}>
      <div className="min-w-0 flex-1 px-4 py-3 first:pl-0">
        <div className="text-xs text-dim">{label}</div>
        <div
          className={cn(
            "mt-0.5 text-lg font-semibold tracking-[-0.02em] tabular-nums",
            tone === "ok" ? "text-ok" : tone === "bad" ? "text-bad" : "text-fg",
          )}
        >
          <Count value={value} />
        </div>
      </div>
    </Tooltip>
  );
}

/**
 * "Backtested over N events in X ms, would block K". Every number straight from BacktestResult;
 * null -> "—". The hero figure gets a subtle ink-marker sweep that replays only when the measured
 * values change (a new backtest), never on a timer.
 */
export function BacktestCard({
  bt,
  title = "Backtest",
  inkDelay = 0.25,
}: {
  bt: BacktestResult | null | undefined;
  title?: string;
  /** Seconds before the ink sweep (GuardrailPanel delays it until the card is revealed). */
  inkDelay?: number;
}) {
  if (!bt)
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-dashed border-line-strong px-4 py-3 text-sm text-dim">
        <Database className="size-4 shrink-0 text-info" strokeWidth={1.75} aria-hidden />
        <span className="font-medium text-muted">{title}</span>
        <span>{DASH}</span>
      </div>
    );
  const measured = isNum(bt.events_scanned) && isNum(bt.query_ms);
  const blocks = isNum(bt.would_block) && bt.would_block > 0;
  const attack = bt.would_block_attack_cases;
  const normal = bt.would_block_normal_cases;
  const normalHit = isNum(normal) && normal > 0;
  return (
    <div className="@container relative overflow-hidden rounded-xl border border-line bg-panel shadow-sm">
      <div className="px-4 pt-3.5">
        <div className="flex flex-wrap items-center gap-2">
          <span className="eyebrow inline-flex items-center gap-1.5">
            <Database className="size-3.5 text-info" strokeWidth={1.75} aria-hidden /> {title}
          </span>
          {bt.mock ? <Badge variant="held">mock</Badge> : null}
        </div>

        <div className="mt-3 text-[13px] text-muted">Backtested over</div>
        <div className="mt-1 flex flex-wrap items-baseline gap-x-2 gap-y-1">
          <span className="num-display whitespace-nowrap text-[28px] text-fg @md:text-[32px]">
            <InkMarker
              tone="info"
              strength={26}
              active={measured}
              trigger={`${bt.events_scanned}|${bt.query_ms}`}
              delay={inkDelay}
            >
              <Count value={bt.events_scanned} />
            </InkMarker>
          </span>
          <span className="text-sm text-muted">events in</span>
          <span className="whitespace-nowrap text-xl font-semibold tracking-[-0.02em] tabular-nums text-fg">
            <Count value={bt.query_ms} digits={msDigits(bt.query_ms)} />
            <span className="ml-1 text-sm font-medium text-muted">ms</span>
          </span>
        </div>
        <div className="mt-1.5 flex flex-wrap items-baseline gap-x-1.5 text-sm text-muted">
          <span>would block</span>
          <span className={cn("font-semibold tabular-nums", blocks ? "text-bad" : "text-fg")}>
            <Count value={bt.would_block} />
          </span>
        </div>
      </div>

      <div className="mt-3.5 flex divide-x divide-line border-t border-line px-4">
        <Figure
          label="Attack cases blocked"
          value={attack}
          tone="neutral"
          tip="would_block_attack_cases: attack cases the candidate blocks"
        />
        <Figure
          label="Normal cases blocked"
          value={normal}
          tone={!isNum(normal) ? "neutral" : normalHit ? "bad" : "ok"}
          tip="would_block_normal_cases: normal cases the candidate would block"
        />
      </div>

      {bt.sql ? (
        <details className="group/sql border-t border-line">
          <summary className="flex cursor-pointer list-none items-center gap-1.5 px-4 py-2.5 text-[13px] text-muted outline-none transition-colors hover:bg-panel-2 hover:text-fg focus-visible:bg-panel-2 focus-visible:text-fg [&::-webkit-details-marker]:hidden">
            <ChevronRight
              className="size-3.5 transition-transform duration-200 group-open/sql:rotate-90"
              strokeWidth={1.75}
              aria-hidden
            />
            SQL receipt
          </summary>
          <div className="relative border-t border-line bg-panel-2">
            <CopyButton value={bt.sql} label="Copy SQL" className="absolute right-2 top-2" />
            <pre className="max-h-64 overflow-auto whitespace-pre-wrap break-words px-4 py-3 pr-11 font-mono text-xs leading-5 text-muted">
              <SqlText sql={bt.sql} />
            </pre>
          </div>
        </details>
      ) : null}
    </div>
  );
}
