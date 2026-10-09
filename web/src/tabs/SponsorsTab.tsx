import { useState, type ReactNode } from "react";
import { Brain, Bug, Database, Lightbulb, LoaderCircle, Play, Workflow } from "lucide-react";
import { Card, CardContent, CardHeader } from "../components/ui/card";
import { Button } from "../components/ui/button";
import { api, ApiError } from "../lib/api";
import { DASH, fmtInt, fmtMs } from "../lib/format";
import { useEvidence, useHeatmap, useTripwire } from "../hooks/useTripwire";

function Sponsor({ icon, name, role, proof, action }: { icon: ReactNode; name: string; role: string; proof: ReactNode; action?: ReactNode }) {
  return (
    <Card className="flex flex-col">
      <CardHeader className="justify-start gap-2 [&_svg]:size-5">
        {icon}
        <span className="text-lg font-bold">{name}</span>
      </CardHeader>
      <CardContent className="flex flex-1 flex-col gap-3">
        <p className="text-sm text-fg/85">{role}</p>
        {action}
        <div className="mt-auto rounded-lg border border-line bg-panel-2 p-3">
          <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Live proof</div>
          <div className="flex flex-col gap-0.5 font-mono text-sm">{proof}</div>
        </div>
      </CardContent>
    </Card>
  );
}

function GuildRun() {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<{ ok: boolean; text: string } | null>(null);
  async function run() {
    setBusy(true);
    try {
      const r = await api.guildRun();
      setOut({ ok: true, text: `upstream HTTP ${r.status} · ${r.body || "(empty body)"}` });
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      setOut({
        ok: false,
        text: status === 503 ? "Guild trigger not configured (503)" : (e as Error).message,
      });
    } finally {
      setBusy(false);
    }
  }
  return (
    <div className="flex flex-col gap-1.5">
      <Button variant="outline" size="sm" className="self-start" onClick={run} disabled={busy}>
        {busy ? <LoaderCircle className="animate-spin" /> : <Play />} Run Guild agent
      </Button>
      {out && (
        <div className={`break-words font-mono text-xs ${out.ok ? "text-ok" : "text-held"}`} title={out.text}>
          {out.text.length > 240 ? `${out.text.slice(0, 240)}…` : out.text}
        </div>
      )}
    </div>
  );
}

export function SponsorsTab() {
  const { state } = useTripwire();
  const { data: e } = useEvidence();
  const { data: hm } = useHeatmap(72, false);
  const bt = state.lastBacktest;
  const guildAgents = [...new Set([...Object.keys(state.modes), ...Object.keys(state.stats)])].filter((a) => a.startsWith("guild:"));
  const lastCodeRef = state.events.find((x) => x.code_ref)?.code_ref;
  return (
    <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
      <Sponsor
        icon={<Database className="text-held" />}
        name="ClickHouse"
        role="Every tool call lands in tripwire.events. Detection runs every second; the fleet heatmap and policy backtests scan the full table, each with an SQL receipt (ms + rows read)."
        proof={
          <>
            <span>{fmtInt(e?.events_stored)} events stored</span>
            <span>
              heatmap: {hm ? `${fmtMs(hm.query_ms)} · ${fmtInt(hm.rows_read)} rows read` : DASH}
            </span>
            <span>
              backtest: {bt ? `${fmtMs(bt.query_ms)} · ${fmtInt(bt.events_scanned)} rows scanned` : DASH}
            </span>
          </>
        }
      />
      <Sponsor
        icon={<Brain className="text-model" />}
        name="Akash (AkashML)"
        role="Model verdicts for held actions and detections run on AkashML (two-model quorum when available); cost per 1,000 events is compared with OpenAI."
        proof={
          state.modelIds.length ? (
            <>
              <span>{state.modelIds.length} distinct model id{state.modelIds.length === 1 ? "" : "s"} seen:</span>
              {state.modelIds.map((m) => (
                <span key={m} className="truncate text-model" title={m}>
                  {m}
                </span>
              ))}
            </>
          ) : (
            DASH
          )
        }
      />
      <Sponsor
        icon={<Bug className="text-bad" />}
        name="Semgrep"
        role="Semgrep scans the AI-written code with a custom agent-security ruleset; runtime events carry code_ref (file:line) so a denial can link back to code."
        proof={
          <>
            <span>scan results: semgrep/FINDINGS.md in the repo</span>
            <span className="text-muted">last code_ref: {lastCodeRef ?? DASH}</span>
          </>
        }
      />
      <Sponsor
        icon={<Workflow className="text-info" />}
        name="Guild"
        role="A Guild-hosted agent (guild:*) runs in the Guild workspace and is governed by the same checkpoint: its tool calls pass through Tripwire like any other agent's."
        action={<GuildRun />}
        proof={
          guildAgents.length ? (
            guildAgents.map((g) => (
              <span key={g}>
                {g} seen · {state.modes[g] ?? "normal"}
              </span>
            ))
          ) : (
            <span>no guild:* agent seen yet</span>
          )
        }
      />
      <Sponsor
        icon={<Lightbulb className="text-ok" />}
        name="Pi · Most Innovative"
        role="Prevent → trip → trace → cure-with-proof: the risky send is held before it runs, patient zero is traced across the fleet, and the fix is proven by replay + backtest before a human approves it."
        proof={
          <>
            <span>
              {state.approvedIds.length} guardrail{state.approvedIds.length === 1 ? "" : "s"} proven and approved this session
            </span>
            <span className="text-muted">hold decision (median): {fmtMs(e?.hold_decision_ms)}</span>
          </>
        }
      />
    </div>
  );
}

export default SponsorsTab;
