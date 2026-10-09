import NumberFlow from "@number-flow/react";
import { Database, DollarSign, Gauge, Hand, Lock, Timer } from "lucide-react";
import type { ReactNode } from "react";
import { Card } from "./ui/card";
import { Tooltip } from "./ui/tooltip";
import { DASH, isNum } from "../lib/format";
import { useEvidence } from "../hooks/useTripwire";

function Num({ v, digits = 0, suffix }: { v: number | null | undefined; digits?: number; suffix?: string }) {
  if (!isNum(v)) return <span className="text-dim">{DASH}</span>;
  return (
    <NumberFlow
      value={v}
      format={{ maximumFractionDigits: digits, minimumFractionDigits: 0 }}
      suffix={suffix}
      willChange
    />
  );
}

function Tile({
  icon,
  label,
  children,
  source,
}: {
  icon: ReactNode;
  label: string;
  children: ReactNode;
  source: string;
}) {
  return (
    <Tooltip content={<span>Source: {source}</span>}>
      <Card className="flex min-w-0 flex-col gap-1 px-4 py-3">
        <div className="flex items-center gap-1.5 text-[11px] font-semibold uppercase tracking-[0.12em] text-muted [&_svg]:size-3.5">
          {icon}
          {label}
        </div>
        <div className="font-mono text-2xl font-bold tabular-nums leading-tight text-fg">{children}</div>
      </Card>
    </Tooltip>
  );
}

/** Six numbers, all straight from GET /evidence. Null renders "—". */
export function KpiStrip() {
  const { data: ev, isError } = useEvidence();
  const e = isError ? undefined : ev;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
      <Tile icon={<Database />} label="Events stored" source="/evidence events_stored (ClickHouse count, includes synthetic background rows)">
        <Num v={e?.events_stored} />
      </Tile>
      <Tile icon={<Gauge />} label="Detection p50 / p95" source="/evidence query_p50_ms / query_p95_ms">
        <Num v={e?.query_p50_ms} digits={1} />
        <span className="text-dim"> / </span>
        <Num v={e?.query_p95_ms} digits={1} />
        <span className="ml-1 text-sm text-muted">ms</span>
      </Tile>
      <Tile icon={<Timer />} label="Time to detect" source="/evidence time_to_detect_ms">
        <Num v={e?.time_to_detect_ms} />
        {isNum(e?.time_to_detect_ms) && <span className="ml-1 text-sm text-muted">ms</span>}
      </Tile>
      <Tile icon={<Lock />} label="Time to contain" source="/evidence time_to_contain_ms">
        <Num v={e?.time_to_contain_ms} />
        {isNum(e?.time_to_contain_ms) && <span className="ml-1 text-sm text-muted">ms</span>}
      </Tile>
      <Tile icon={<Hand />} label="Hold decision" source="/evidence hold_decision_ms">
        <Num v={e?.hold_decision_ms} />
        {isNum(e?.hold_decision_ms) && <span className="ml-1 text-sm text-muted">ms</span>}
      </Tile>
      <Tile
        icon={<DollarSign />}
        label="$ / 1,000 events"
        source={`/evidence cost_akashml vs cost_openai${e?.priced_on ? `, priced on ${e.priced_on}` : ""}`}
      >
        <div className="flex items-baseline gap-2 text-xl">
          <span className="text-model">
            <Num v={e?.cost_akashml} digits={4} />
          </span>
          <span className="text-xs font-medium text-muted">Akash</span>
          <span className="text-dim">vs</span>
          <Num v={e?.cost_openai} digits={4} />
          <span className="text-xs font-medium text-muted">OpenAI</span>
        </div>
      </Tile>
    </div>
  );
}
