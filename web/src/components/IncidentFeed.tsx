import { ArrowRight, Siren } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Badge } from "./ui/badge";
import { DecisionSourceBadge, VerdictBadge } from "./badges";
import { fmtClock } from "../lib/format";
import type { Incident } from "../lib/types";
import { useTripwire } from "../hooks/useTripwire";

export function sortedIncidents(incidents: Record<string, Incident>): Incident[] {
  return Object.values(incidents).sort((a, b) => b.opened_ms - a.opened_ms);
}

export function TagList({ tags }: { tags?: string[] }) {
  if (!tags?.length) return null;
  return (
    <div className="flex flex-wrap gap-1">
      {tags.map((t) => (
        <Badge key={t} variant="muted" className="font-mono text-[10px]">
          {t}
        </Badge>
      ))}
    </div>
  );
}

export function IncidentFeed({ onOpen }: { onOpen: (id: string) => void }) {
  const { state } = useTripwire();
  const incidents = sortedIncidents(state.incidents).slice(0, 20);
  const cleared = state.alerts.filter((a) => a.verdict === "benign").slice(0, 8);
  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader>
        <CardTitle>Incident feed</CardTitle>
        <span className="font-mono text-xs text-dim">{incidents.length} incidents</span>
      </CardHeader>
      <CardContent className="flex max-h-[420px] flex-col gap-2 overflow-y-auto">
        {incidents.length === 0 && cleared.length === 0 && (
          <div className="py-6 text-center text-sm text-dim">No incidents. Fleet is quiet.</div>
        )}
        {incidents.map((inc) => (
          <button
            key={inc.id}
            onClick={() => onOpen(inc.id)}
            className="group flex w-full cursor-pointer flex-col gap-1.5 rounded-lg border border-line bg-panel-2 p-3 text-left hover:border-muted"
          >
            <div className="flex items-center gap-2">
              <Siren className={`size-4 ${inc.closed_ms ? "text-dim" : "text-bad"}`} />
              <span className="font-mono text-sm font-bold">{inc.rule}</span>
              <span className="font-mono text-xs text-muted">{inc.agent_id}</span>
              <span className="ml-auto font-mono text-[11px] text-dim">{fmtClock(inc.opened_ms, false)}</span>
              {inc.closed_ms ? <Badge variant="muted">closed</Badge> : <Badge variant="bad">open</Badge>}
            </div>
            <div className="flex flex-wrap items-center gap-1.5">
              <VerdictBadge verdict={inc.verdict?.verdict} confidence={inc.verdict?.confidence} />
              <DecisionSourceBadge source={inc.verdict?.decision_source} />
              {inc.report_md ? <Badge variant="info">report ready</Badge> : null}
            </div>
            <TagList tags={inc.tags} />
          </button>
        ))}
        {cleared.length > 0 && (
          <div className="mt-1 flex flex-col gap-1.5">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">Flagged → benign</div>
            {cleared.map((a, i) => (
              <div
                key={a.id ?? `${a.agent_id}-${a.last_step_ts_ms}-${i}`}
                className="flex flex-wrap items-center gap-1.5 rounded-lg border border-line/70 px-3 py-2 text-xs"
              >
                <Badge variant="held">flagged</Badge>
                <ArrowRight className="size-3.5 text-dim" />
                <VerdictBadge verdict={a.verdict} confidence={a.confidence} />
                <DecisionSourceBadge source={a.decision_source} />
                <span className="font-mono text-muted">
                  {a.agent_id ?? "—"} · {a.rule}
                </span>
                <span className="w-full truncate text-dim" title={a.reason}>
                  {a.reason}
                </span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
