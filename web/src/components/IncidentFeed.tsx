import type { KeyboardEvent } from "react";
import { ArrowRight, ArrowUpRight, Brain, Hand, Siren } from "lucide-react";
import { Badge } from "./ui/badge";
import { Tooltip } from "./ui/tooltip";
import { DecisionSourceBadge, VerdictBadge } from "./badges";
import { Chip, CopyId, Eyebrow, GlowCard, Skeleton, type ChipTone } from "./fx";
import { fmtClock } from "../lib/format";
import { cn } from "../lib/utils";
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
        <span
          key={t}
          className="inline-flex h-5 items-center rounded-md border border-line bg-panel-2 px-1.5 font-mono text-[11px] text-muted"
        >
          {t}
        </span>
      ))}
    </div>
  );
}

const VERDICT_TONE: Record<string, ChipTone> = {
  malicious: "bad",
  benign: "ok",
  uncertain: "held",
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
      {list.map((v) => (
        <Chip
          key={v.model_id}
          mono
          tone={VERDICT_TONE[String(v.verdict)] ?? "neutral"}
          title={`model ${v.model_id} voted ${v.verdict ?? "—"}`}
          className="h-auto min-h-6 max-w-full whitespace-normal py-1 text-[11px]"
        >
          <Brain className="text-model" />
          <span className="text-fg/85">{v.model_id}</span>
          <span>· {v.verdict ?? "—"}</span>
          {typeof v.confidence === "number" ? <span>{(v.confidence * 100).toFixed(0)}%</span> : null}
        </Chip>
      ))}
    </div>
  );
}

/** Non-blocking alerts: flagged → benign (green), held for review (amber), denied by policy (red). */
function AlertEntry({ a }: { a: Alert }) {
  const benign = a.verdict === "benign";
  const uncertain = a.verdict === "uncertain";
  const edge = benign ? "var(--color-ok)" : uncertain ? "var(--color-held)" : "var(--color-bad)";
  return (
    <div
      className="flex flex-col gap-2 rounded-xl border border-line bg-panel px-3.5 py-3 text-[13px] shadow-sm"
      style={{ boxShadow: `inset 2px 0 0 ${edge}, var(--shadow-sm)` }}
    >
      <div className="flex flex-wrap items-center gap-1.5">
        {benign ? (
          <>
            <Badge variant="held">flagged</Badge>
            <ArrowRight className="size-3.5 text-ok" strokeWidth={1.75} />
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
      </div>
      <div className="flex min-w-0 flex-wrap items-baseline gap-x-2 gap-y-0.5 font-mono text-[12px]">
        <span className="font-medium text-fg">{a.agent_id ?? "—"}</span>
        <span className="text-muted">{a.rule}</span>
        {a.model_ids?.length ? <span className="text-[11px] text-dim">{a.model_ids.join(" + ")}</span> : null}
      </div>
      <p className="line-clamp-2 text-[12px] leading-snug text-muted" title={a.reason}>
        {a.reason}
      </p>
    </div>
  );
}

function IncidentRow({
  inc,
  approved,
  votes,
  onOpen,
}: {
  inc: Incident;
  approved: boolean;
  votes?: QuorumVote[];
  onOpen: (id: string) => void;
}) {
  const open = !inc.closed_ms;
  const onKey = (e: KeyboardEvent<HTMLDivElement>) => {
    if (e.target !== e.currentTarget) return;
    if (e.key === "Enter" || e.key === " ") {
      e.preventDefault();
      onOpen(inc.id);
    }
  };
  return (
    <div
      role="button"
      tabIndex={0}
      aria-label={`Open incident ${inc.id}: ${inc.rule} on ${inc.agent_id}`}
      onClick={() => onOpen(inc.id)}
      onKeyDown={onKey}
      className={cn(
        "group/inc relative flex w-full cursor-pointer gap-3 px-4 py-3.5 text-left transition-colors duration-150",
        "hover:bg-panel-3/60 focus-visible:bg-panel-3/60 focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-brand",
      )}
    >
      <span
        aria-hidden
        className={cn(
          "mt-0.5 grid size-8 shrink-0 place-items-center rounded-lg border [&_svg]:size-4",
          open ? "tint-bad" : "tint-neutral",
        )}
      >
        <Siren strokeWidth={1.75} />
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="truncate text-[14px] font-semibold tracking-[-0.01em] text-fg">{inc.rule}</span>
          <span className="truncate font-mono text-[12px] text-muted">{inc.agent_id}</span>
          <span className="ml-auto shrink-0 font-mono text-[12px] text-dim">{fmtClock(inc.opened_ms, false)}</span>
          <ArrowUpRight
            aria-hidden
            className="size-4 shrink-0 text-dim transition-[transform,color] duration-200 group-hover/inc:-translate-y-0.5 group-hover/inc:translate-x-0.5 group-hover/inc:text-brand"
            strokeWidth={1.75}
          />
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          {open ? <Badge variant="bad">open</Badge> : <Badge variant="muted">closed</Badge>}
          <VerdictBadge verdict={inc.verdict?.verdict} confidence={inc.verdict?.confidence} />
          <DecisionSourceBadge source={inc.verdict?.decision_source} />
          {inc.report_md ? <Badge variant="info">report ready</Badge> : null}
          {approved ? <Badge variant="ok">cure approved</Badge> : null}
        </div>
        <QuorumChips inc={inc} votes={votes} />
        <div className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-1">
          <CopyId value={inc.id} className="text-[12px] text-muted" />
          <TagList tags={inc.tags} />
        </div>
      </div>
    </div>
  );
}

export function IncidentFeed({ onOpen }: { onOpen: (id: string) => void }) {
  const { state } = useTripwire();
  const incidents = sortedIncidents(state.incidents).slice(0, 20);
  const alerts = state.alerts.slice(0, 10);
  const open = incidents.filter((i) => !i.closed_ms).length;
  const loading = state.connection === "connecting" && incidents.length === 0 && alerts.length === 0;
  return (
    <GlowCard
      className="h-full"
      tone={open ? "bad" : "neutral"}
      glow="soft"
      reveal={12}
      eyebrow={
        <>
          <Siren /> Incidents · live
        </>
      }
      title="Incident feed"
      actions={
        open ? (
          <Tooltip content={`${open} open of the ${incidents.length} most recent incidents`}>
            <span>
              <Badge variant="bad" className="font-mono">
                {open} open
              </Badge>
            </span>
          </Tooltip>
        ) : (
          <span className="font-mono text-[12px] text-dim">{incidents.length} incidents</span>
        )
      }
      bodyClassName="flex min-h-0 flex-col pt-1"
    >
      <div className="-mx-1 flex max-h-[468px] min-h-0 flex-col gap-3 overflow-y-auto px-1 pb-1">
        {loading && (
          <div role="status" aria-label="Loading incidents" className="flex flex-col gap-2">
            {Array.from({ length: 3 }, (_, i) => (
              <Skeleton key={i} className="h-24 rounded-xl" />
            ))}
          </div>
        )}
        {!loading && incidents.length === 0 && alerts.length === 0 && (
          <div className="grid min-h-48 place-items-center rounded-xl border border-dashed border-line-strong bg-panel-2 py-6 text-center">
            <div className="flex flex-col items-center gap-2 text-dim">
              <Siren className="size-5" strokeWidth={1.75} />
              <span className="text-[14px]">No incidents. Fleet is quiet.</span>
            </div>
          </div>
        )}
        {incidents.length > 0 && (
          <div className="divide-y divide-line overflow-hidden rounded-xl border border-line bg-panel">
            {incidents.map((inc) => (
              <IncidentRow
                key={inc.id}
                inc={inc}
                approved={state.approvedIds.includes(inc.id)}
                votes={state.quorums[inc.id]}
                onOpen={onOpen}
              />
            ))}
          </div>
        )}
        {alerts.length > 0 && (
          <div className="flex flex-col gap-2">
            <Eyebrow className="mt-1">Non-blocking verdicts · flagged → benign</Eyebrow>
            {alerts.map((a, i) => (
              <AlertEntry key={a.id ?? `${a.agent_id}-${a.last_step_ts_ms}-${i}`} a={a} />
            ))}
          </div>
        )}
      </div>
    </GlowCard>
  );
}
