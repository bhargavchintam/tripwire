import { lazy, Suspense, useState } from "react";
import { Ban, Biohazard, History, Receipt, Route, Users } from "lucide-react";
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "./ui/sheet";
import { Badge } from "./ui/badge";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { DecisionSourceBadge, ReasonText, ResultBadge, VerdictBadge } from "./badges";
import { QuorumChips, TagList } from "./IncidentFeed";
import { AttackChain } from "./AttackChain";
import { AuditBadge } from "./AuditBadge";
import { GuardrailPanel } from "./GuardrailPanel";
import { DASH, fmtClock, fmtInt, fmtMs } from "../lib/format";
import type { AgentMode, Incident, Outbreak } from "../lib/types";
import { useIncident, useTripwire } from "../hooks/useTripwire";

const ReportMarkdown = lazy(() => import("./ReportMarkdown"));

/** D9 time travel: the incident as it looked after `pos` steps (pos = steps.length → live). */
function incidentAt(inc: Incident, pos: number, liveMode: AgentMode | undefined): { inc: Incident; mode: AgentMode | undefined } {
  const n = inc.steps.length;
  if (pos >= n) return { inc, mode: liveMode };
  const shown = inc.steps.slice(0, pos);
  const lastTs = shown.length ? shown[shown.length - 1].ts_ms : -Infinity;
  const contained =
    shown.some((s) => s.reason === "blocked") || (inc.contained_ms != null && inc.contained_ms <= lastTs);
  return {
    inc: { ...inc, steps: shown, contained_ms: contained ? inc.contained_ms : null, closed_ms: null },
    mode: contained ? "quarantined" : "normal",
  };
}

export function OutbreakCard({ ob }: { ob: Outbreak | null | undefined }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Biohazard className="size-4" /> Outbreak trace
        </CardTitle>
        {ob?.query_ms != null && <span className="font-mono text-xs text-dim">{fmtMs(ob.query_ms)}</span>}
      </CardHeader>
      <CardContent>
        {!ob ? (
          <div className="text-sm text-dim">No outbreak trace yet.</div>
        ) : (
          <div className="grid gap-3 md:grid-cols-3">
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">Patient zero</div>
              <Badge variant="bad" className="font-mono">
                <Route /> {ob.source_id}
              </Badge>
            </div>
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">Exposed agents</div>
              <div className="flex flex-wrap gap-1">
                {ob.exposed_agents.length ? (
                  ob.exposed_agents.map((a) => (
                    <Badge key={a} variant="held" className="font-mono">
                      <Users /> {a}
                    </Badge>
                  ))
                ) : (
                  <span className="text-dim">{DASH}</span>
                )}
              </div>
            </div>
            <div>
              <div className="mb-1 text-[11px] uppercase tracking-wider text-muted">Blocked destinations</div>
              <div className="flex flex-wrap gap-1">
                {ob.blocked_destinations.length ? (
                  ob.blocked_destinations.map((h) => (
                    <Badge key={h} variant="bad" className="font-mono">
                      <Ban /> {h}
                    </Badge>
                  ))
                ) : (
                  <span className="text-dim">{DASH}</span>
                )}
              </div>
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function Receipts({ receipts }: { receipts: Incident["receipts"] }) {
  if (!receipts?.length) return <div className="text-sm text-dim">No SQL receipts yet.</div>;
  return (
    <div className="flex flex-col gap-2">
      {receipts.map((r, i) => (
        <div key={i} className="rounded-lg border border-line bg-panel-2 p-3">
          <div className="mb-1.5 flex flex-wrap items-center gap-2 text-xs">
            <Receipt className="size-3.5 text-muted" />
            {r.label ? <span className="font-semibold">{String(r.label)}</span> : null}
            <span className="font-mono text-muted">{fmtMs(r.ms as number | null | undefined)}</span>
            <span className="font-mono text-muted">· {fmtInt(r.rows_read as number | null | undefined)} rows read</span>
            {r.mock ? <Badge variant="held">mock</Badge> : null}
          </div>
          <pre className="overflow-x-auto whitespace-pre-wrap break-words font-mono text-[12px] leading-5 text-info">
            {r.sql ?? DASH}
          </pre>
        </div>
      ))}
    </div>
  );
}

export function IncidentSheet({ id, onClose }: { id: string | null; onClose: () => void }) {
  const { state } = useTripwire();
  const q = useIncident(id);
  const inc: Incident | undefined = (q.data && !q.isError ? q.data : undefined) ?? (id ? state.incidents[id] : undefined);
  const v = inc?.verdict;
  // null = follow live (always the latest step); a number = scrubbed position.
  const [scrub, setScrub] = useState<{ id: string | null; pos: number | null }>({ id: null, pos: null });
  const n = inc?.steps.length ?? 0;
  const pos = scrub.id === id && scrub.pos !== null ? Math.min(scrub.pos, n) : n;
  const at = inc ? incidentAt(inc, pos, state.modes[inc.agent_id]) : null;
  return (
    <Sheet open={!!id} onOpenChange={(o) => !o && onClose()}>
      <SheetContent>
        <SheetHeader>
          <SheetTitle className="flex flex-wrap items-center gap-2">
            <span className="font-mono">{inc?.rule ?? "incident"}</span>
            <span className="font-mono text-sm text-muted">{inc?.agent_id}</span>
            {inc?.closed_ms ? <Badge variant="muted">closed</Badge> : <Badge variant="bad">open</Badge>}
            <AuditBadge agentId={inc?.agent_id} />
          </SheetTitle>
          <SheetDescription className="font-mono text-xs">
            {id} · opened {fmtClock(inc?.opened_ms)} · contained {inc?.contained_ms ? fmtClock(inc.contained_ms) : DASH}
          </SheetDescription>
        </SheetHeader>
        {!inc || !at ? (
          <div className="p-6 text-sm text-dim">{q.isLoading ? "Loading…" : "Incident not found."}</div>
        ) : (
          <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-6">
            <div className="flex flex-wrap items-center gap-2">
              <VerdictBadge verdict={v?.verdict} confidence={v?.confidence} />
              <DecisionSourceBadge source={v?.decision_source} />
              {v?.latency_ms ? <span className="font-mono text-xs text-muted">{fmtMs(v.latency_ms)}</span> : null}
            </div>
            <QuorumChips inc={inc} votes={state.quorums[inc.id]} />
            {v?.reason && <p className="text-sm text-fg/90">{v.reason}</p>}
            <TagList tags={inc.tags} />

            <AttackChain incident={at.inc} mode={at.mode} />

            <Card>
              <CardHeader>
                <CardTitle className="flex items-center gap-1.5">
                  <History className="size-4" /> Timeline · time travel
                </CardTitle>
                <span className="font-mono text-xs text-dim">
                  {pos >= n ? "live" : `step ${pos} of ${n}`}
                  {pos > 0 && pos <= n ? ` · ${fmtClock(inc.steps[pos - 1].ts_ms)}` : ""}
                </span>
              </CardHeader>
              <CardContent>
                {n > 0 && (
                  <div className="mb-3 flex items-center gap-3">
                    <input
                      type="range"
                      min={0}
                      max={n}
                      step={1}
                      value={pos}
                      onChange={(e) => {
                        const p = Number(e.target.value);
                        setScrub({ id, pos: p >= n ? null : p });
                      }}
                      aria-label="Scrub through incident steps"
                      className="h-2 w-full cursor-pointer accent-[var(--color-held)]"
                    />
                    <button
                      className="shrink-0 rounded border border-line px-2 py-0.5 font-mono text-[11px] text-muted hover:text-fg disabled:opacity-40"
                      disabled={pos >= n}
                      onClick={() => setScrub({ id, pos: null })}
                    >
                      live
                    </button>
                  </div>
                )}
                <ol className="relative ml-2 border-l border-line">
                  {inc.steps.map((s, i) => {
                    const future = i >= pos;
                    const current = i === pos - 1 && pos < n;
                    return (
                      <li
                        key={`${s.ts_ms}-${i}`}
                        className={`mb-3 ml-4 transition-opacity duration-300 ${future ? "opacity-20" : ""} ${
                          current ? "rounded-md bg-held/10 ring-1 ring-held/50" : ""
                        }`}
                      >
                        <span
                          className={`absolute -left-[5px] mt-1.5 size-2.5 rounded-full ${
                            s.result === "denied" && !future ? "bg-bad" : "bg-muted"
                          }`}
                        />
                        <div className="flex flex-wrap items-center gap-2">
                          <span className="font-mono text-xs text-muted">{fmtClock(s.ts_ms)}</span>
                          <span className="font-mono text-sm font-semibold">{s.action}</span>
                          {future ? (
                            <span className="font-mono text-[11px] text-dim">not yet</span>
                          ) : (
                            <>
                              <ResultBadge result={s.result} reason={s.reason} />
                              <ReasonText reason={s.reason} />
                            </>
                          )}
                        </div>
                        <div className="truncate font-mono text-xs text-muted" title={s.target}>
                          {s.target}
                        </div>
                      </li>
                    );
                  })}
                  {n === 0 && <li className="ml-4 text-sm text-dim">No steps recorded.</li>}
                </ol>
              </CardContent>
            </Card>

            <GuardrailPanel key={inc.id} incidentId={inc.id} />

            <OutbreakCard ob={inc.outbreak} />

            <Card>
              <CardHeader>
                <CardTitle>Investigator report</CardTitle>
              </CardHeader>
              <CardContent>
                {inc.report_md ? (
                  <Suspense fallback={<div className="text-sm text-dim">Rendering report…</div>}>
                    <ReportMarkdown md={inc.report_md} />
                  </Suspense>
                ) : (
                  <div className="text-sm text-dim">Report not ready yet.</div>
                )}
              </CardContent>
            </Card>

            <Card>
              <CardHeader>
                <CardTitle>SQL receipts</CardTitle>
              </CardHeader>
              <CardContent>
                <Receipts receipts={inc.receipts} />
              </CardContent>
            </Card>
          </div>
        )}
      </SheetContent>
    </Sheet>
  );
}
