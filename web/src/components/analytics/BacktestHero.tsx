import type { ReactNode } from "react";
import { Ban, Database, LoaderCircle, ScanSearch, ShieldAlert, ShieldCheck, Timer } from "lucide-react";
import { Badge } from "../ui/badge";
import { Chip, Count, GlowCard, InkMarker } from "../fx";
import { MsCount } from "./MsCount";
import { Metric } from "./Metric";
import { SqlReceipt } from "./SqlReceipt";
import { isNum } from "../../lib/format";
import { cn } from "../../lib/utils";
import type { BacktestResult } from "../../lib/types";

/**
 * Backtest result as a bento card: events scanned · query time · would block, in raised wells.
 * Same fields and null → "—" semantics as components/BacktestCard; the query time gets the ink
 * marker (re-sweeps only when a new backtest result arrives). The scan line only runs while a
 * backtest request is really in flight.
 */
export function BacktestHero({
  bt,
  running = false,
  title = "Backtest",
  description,
  emptyHint,
  actions,
  className,
}: {
  bt: BacktestResult | null | undefined;
  running?: boolean;
  title?: ReactNode;
  description?: ReactNode;
  /** Shown under the wells when no result exists yet (not running). */
  emptyHint?: ReactNode;
  /** Extra header actions (e.g. the Backtest button). */
  actions?: ReactNode;
  className?: string;
}) {
  const blocks = isNum(bt?.would_block) && bt.would_block > 0;
  const normalBlocked = bt?.would_block_normal_cases;
  return (
    <GlowCard
      tone={bt ? "info" : "neutral"}
      glow="soft"
      reveal={1}
      aria-busy={running}
      className={className}
      eyebrow={
        <>
          <Database /> Backtest · ClickHouse
        </>
      }
      title={title}
      description={description}
      actions={
        <>
          {bt?.mock ? <Badge variant="held">mock</Badge> : null}
          {running && (
            <Chip tone="info" icon={<LoaderCircle className="animate-spin" />}>
              Running
            </Chip>
          )}
          {actions}
        </>
      }
    >
      <div aria-hidden className={cn("relative mb-4 h-px overflow-hidden bg-line", !running && "opacity-0")}>
        {running && <span className="absolute inset-y-0 left-0 w-1/3 animate-scan bg-info" />}
      </div>
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
        <Well>
          <Metric icon={<ScanSearch />} label="Events scanned" caption="rows read by ClickHouse">
            <Count value={bt?.events_scanned} />
          </Metric>
        </Well>
        <Well>
          <Metric icon={<Timer />} label="Query time" caption="ClickHouse query time">
            <InkMarker tone="info" trigger={bt?.query_ms} active={isNum(bt?.query_ms)}>
              <MsCount value={bt?.query_ms} />
            </InkMarker>
          </Metric>
        </Well>
        <Well tone={blocks ? "bad" : undefined}>
          <Metric
            icon={<Ban />}
            label="Would block"
            caption="historical external posts it would deny"
            valueClassName={blocks ? "text-bad" : undefined}
          >
            <Count value={bt?.would_block} />
          </Metric>
        </Well>
      </div>
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Chip tone="neutral" size="md" icon={<ShieldAlert />}>
          Attack cases blocked
          <span className="font-semibold text-fg tabular-nums">
            <Count value={bt?.would_block_attack_cases} />
          </span>
        </Chip>
        <Chip
          tone={isNum(normalBlocked) ? (normalBlocked > 0 ? "bad" : "ok") : "neutral"}
          size="md"
          icon={isNum(normalBlocked) && normalBlocked > 0 ? <ShieldAlert /> : <ShieldCheck />}
        >
          Normal cases blocked
          <span className="font-semibold tabular-nums">
            <Count value={normalBlocked} />
          </span>
        </Chip>
        {!bt && !running && emptyHint ? <span className="text-[13px] text-dim">{emptyHint}</span> : null}
      </div>
      <SqlReceipt sql={bt?.sql} className="mt-4" />
    </GlowCard>
  );
}

function Well({ children, tone }: { children: ReactNode; tone?: "bad" }) {
  return (
    <div
      className={cn(
        "rounded-xl border px-4 py-4 transition-colors duration-300",
        tone === "bad" ? "border-bad-line bg-bad-soft/60" : "border-line bg-panel-2",
      )}
    >
      {children}
    </div>
  );
}
