import { Database } from "lucide-react";
import { Badge } from "./ui/badge";
import { DASH, fmtInt, fmtMs, isNum } from "../lib/format";
import type { BacktestResult } from "../lib/types";

/** "Backtested over N events in X ms — would block K". Every number straight from BacktestResult; null → "—". */
export function BacktestCard({ bt, title = "Backtest" }: { bt: BacktestResult | null | undefined; title?: string }) {
  if (!bt) return <div className="rounded-lg border border-dashed border-line p-3 text-sm text-dim">{title}: {DASH}</div>;
  return (
    <div className="rounded-lg border border-info/40 bg-info/[0.05] p-3">
      <div className="mb-1 flex flex-wrap items-center gap-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
        <Database className="size-3.5 text-info" /> {title}
        {bt.mock ? <Badge variant="held">mock</Badge> : null}
      </div>
      <div className="text-base">
        Backtested over <span className="font-mono font-bold text-fg">{fmtInt(bt.events_scanned)}</span> events in{" "}
        <span className="font-mono font-bold text-fg">{fmtMs(bt.query_ms)}</span> — would block{" "}
        <span className={`font-mono font-bold ${isNum(bt.would_block) && bt.would_block > 0 ? "text-bad" : "text-fg"}`}>
          {fmtInt(bt.would_block)}
        </span>
      </div>
      <div className="mt-1 flex flex-wrap gap-3 font-mono text-xs text-muted">
        <span>
          attack cases blocked: <span className="text-fg">{fmtInt(bt.would_block_attack_cases)}</span>
        </span>
        <span>
          normal cases blocked:{" "}
          <span className={isNum(bt.would_block_normal_cases) && bt.would_block_normal_cases > 0 ? "text-bad" : "text-ok"}>
            {fmtInt(bt.would_block_normal_cases)}
          </span>
        </span>
      </div>
      {bt.sql ? (
        <details className="mt-2">
          <summary className="cursor-pointer text-xs text-muted hover:text-fg">SQL receipt</summary>
          <pre className="mt-1 overflow-x-auto whitespace-pre-wrap break-words font-mono text-[11px] leading-5 text-info">
            {bt.sql}
          </pre>
        </details>
      ) : null}
    </div>
  );
}
