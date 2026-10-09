import { Brain, Bug, Database, Lightbulb, Workflow } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader } from "../components/ui/card";
import { DASH, fmtInt, fmtMs, fmtUsd, isNum } from "../lib/format";
import { sortedIncidents } from "../components/IncidentFeed";
import { useEvidence, useTripwire } from "../hooks/useTripwire";

function Sponsor({ icon, name, role, proof }: { icon: ReactNode; name: string; role: string; proof: ReactNode }) {
  return (
    <Card className="flex flex-col">
      <CardHeader className="justify-start gap-2 [&_svg]:size-5">
        {icon}
        <span className="text-lg font-bold">{name}</span>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <p className="text-sm text-fg/85">{role}</p>
        <div className="mt-auto rounded-lg border border-line bg-panel-2 p-3">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Live proof</div>
          <div className="font-mono text-sm">{proof}</div>
        </div>
      </CardContent>
    </Card>
  );
}

export function SponsorsTab() {
  const { state } = useTripwire();
  const { data: e } = useEvidence();
  const latest = sortedIncidents(state.incidents)[0];
  const lastCodeRef = state.events.find((x) => x.code_ref)?.code_ref;
  const guild = Object.keys(state.modes).find((a) => a.startsWith("guild:"));
  const modelVerdict = sortedIncidents(state.incidents).find((i) =>
    ["akashml", "quorum"].includes(String(i.verdict?.decision_source)),
  )?.verdict;
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Sponsor
        icon={<Database className="text-held" />}
        name="ClickHouse"
        role="Every tool call lands in tripwire.events. Funnel + baseline detection queries run every second; every number carries an SQL receipt (ms + rows read)."
        proof={
          <>
            {fmtInt(e?.events_stored)} events · p50 {fmtMs(e?.query_p50_ms)} · p95 {fmtMs(e?.query_p95_ms)}
          </>
        }
      />
      <Sponsor
        icon={<Brain className="text-model" />}
        name="Akash (AkashML)"
        role="Model verdicts for held actions and detections run on AkashML; cost per 1,000 events is compared with OpenAI."
        proof={
          modelVerdict ? (
            <>
              {modelVerdict.decision_source} · {modelVerdict.model_ids?.join(", ") || DASH} · {fmtUsd(e?.cost_akashml)}/1k
            </>
          ) : (
            DASH
          )
        }
      />
      <Sponsor
        icon={<Bug className="text-bad" />}
        name="Semgrep"
        role="A custom agent-security ruleset scans the agent tools; runtime events carry code_ref (file:line) so a denial links back to the vulnerable code."
        proof={lastCodeRef ?? DASH}
      />
      <Sponsor
        icon={<Workflow className="text-info" />}
        name="Guild"
        role="A Guild-hosted agent (guild:deploy-bot) is governed by the same checkpoint; a Guild Responder adds the human approval step."
        proof={guild ? `${guild} · ${state.modes[guild]}` : DASH}
      />
      <Sponsor
        icon={<Lightbulb className="text-ok" />}
        name="Pi · Most Innovative"
        role="Prevent → trip → trace → cure-with-proof: the risky send is held before it runs, patient zero is traced across the fleet, and the fix is proven by replay."
        proof={
          latest ? (
            <>
              {latest.rule} · hold {fmtMs(e?.hold_decision_ms)} · patient zero {latest.outbreak?.source_id ?? state.outbreak?.source_id ?? DASH}
            </>
          ) : isNum(e?.hold_decision_ms) ? (
            <>hold {fmtMs(e?.hold_decision_ms)}</>
          ) : (
            DASH
          )
        }
      />
    </div>
  );
}
