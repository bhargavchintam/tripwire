import { ArrowRight, Brain, Hand, Siren } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Badge } from "./ui/badge";
import { DecisionSourceBadge, VerdictBadge } from "./badges";
import { fmtClock } from "../lib/format";
import type { Alert, Incident, QuorumVote } from "../lib/types";
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

const VERDICT_TONE: Record<string, string> = {
  malicious: "border-bad/50 text-bad",
  benign: "border-ok/50 text-ok",
  uncertain: "border-held/50 text-held",
};

/** One chip per model id with that model's verdict (from SSE quorum if present, else the incident verdict). */
export function QuorumChips({ inc, votes }: { inc: Incident; votes?: QuorumVote[] }) {
  const ids = inc.verdict?.model_ids ?? [];
  if (ids.length === 0 && !votes?.length) return null;
  const byId = new Map((votes ?? []).map((v) => [v.model_id, v]));
  const list: QuorumVote[] = ids.length
    ? ids.map((m) => byId.get(m) ?? { model_id: m, verdict: inc.verdict?.verdict, confidence: undefined })
    : (votes ?? []);
  return (
    <div className="flex flex-wrap gap-1">
      {list.map((v) => {
        const tone = VERDICT_TONE[String(v.verdict)] ?? "border-line text-muted";
        return (
          <span
            key={v.model_id}
            title={`model ${v.model_id} voted ${v.verdict ?? "—"}`}
            className={`inline-flex items-center gap-1 rounded-full border bg-black/20 px-2 py-0.5 font-mono text-[10px] font-semibold ${tone}`}
          >
            <Brain className="size-3 text-model" />
            <span className="text-fg/85">{v.model_id}</span>
            <span>· {v.verdict ?? "—"}</span>
            {typeof v.confidence === "number" ? <span>{(v.confidence * 100).toFixed(0)}%</span> : null}
          </span>
        );
      })}
    </div>
  );
}

/** Non-blocking alerts: flagged → benign (green), held for review (amber), denied by policy (red). */
function AlertEntry({ a }: { a: Alert }) {
  const benign = a.verdict === "benign";
  const uncertain = a.verdict === "uncertain";
  const cls = benign
    ? "border-ok/70 bg-ok/[0.06] shadow-[0_0_0_1px_rgb(34_197_94_/_0.25)]"
    : uncertain
      ? "border-held/60 bg-held/[0.05]"
      : "border-bad/40 bg-bad/[0.04]";
  return (
    <div className={`flex flex-wrap items-center gap-1.5 rounded-lg border-2 px-3 py-2 text-xs ${cls}`}>
      {benign ? (
        <>
          <Badge variant="held">flagged</Badge>
          <ArrowRight className="size-3.5 text-ok" />
        </>
      ) : uncertain ? (
        <Badge variant="held">
          <Hand /> held for review
        </Badge>
      ) : (
        <Badge variant="bad">denied · non-blocking</Badge>
      )}
      <VerdictBadge verdict={a.verdict} confidence={a.confidence} />
      <DecisionSourceBadge source={a.decision_source} />
      <span className="font-mono text-muted">
        {a.agent_id ?? "—"} · {a.rule}
      </span>
      {a.model_ids?.length ? (
        <span className="font-mono text-[10px] text-dim">{a.model_ids.join(" + ")}</span>
      ) : null}
      <span className="w-full truncate text-dim" title={a.reason}>
        {a.reason}
      </span>
    </div>
  );
}

export function IncidentFeed({ onOpen }: { onOpen: (id: string) => void }) {
  const { state } = useTripwire();
  const incidents = sortedIncidents(state.incidents).slice(0, 20);
  const alerts = state.alerts.slice(0, 10);
  return (
    <Card className="flex min-h-0 flex-col">
      <CardHeader>
        <CardTitle>Incident feed</CardTitle>
        <span className="font-mono text-xs text-dim">{incidents.length} incidents</span>
      </CardHeader>
      <CardContent className="flex max-h-[420px] flex-col gap-2 overflow-y-auto">
        {incidents.length === 0 && alerts.length === 0 && (
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
              {state.approvedIds.includes(inc.id) ? <Badge variant="ok">cure approved</Badge> : null}
            </div>
            <QuorumChips inc={inc} votes={state.quorums[inc.id]} />
            <TagList tags={inc.tags} />
          </button>
        ))}
        {alerts.length > 0 && (
          <div className="mt-1 flex flex-col gap-1.5">
            <div className="text-[11px] font-semibold uppercase tracking-wider text-muted">
              Non-blocking verdicts · flagged → benign
            </div>
            {alerts.map((a, i) => (
              <AlertEntry key={a.id ?? `${a.agent_id}-${a.last_step_ts_ms}-${i}`} a={a} />
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}
