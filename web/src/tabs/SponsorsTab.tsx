import { useState, type ReactNode } from "react";
import { ArrowUpRight, Brain, Bug, Database, Hand, Info, Lightbulb, LoaderCircle, Play, Workflow } from "lucide-react";
import { Button } from "../components/ui/button";
import { Badge } from "../components/ui/badge";
import { Tooltip } from "../components/ui/tooltip";
import { ModeBadge, ResultBadge } from "../components/badges";
import { CopyId, Count, Eyebrow, GlowCard, InkMarker, SectionHeader, Skeleton, type Tone } from "../components/fx";
import { ArtFrame, CodeArt, ColumnsArt, LinkArt, LoopArt, RingArt, useChangeCount } from "../components/sponsors/art";
import { api, ApiError } from "../lib/api";
import { DASH, fmtInt, fmtMs, isNum } from "../lib/format";
import { cn } from "../lib/utils";
import type { Result, ToolEvent } from "../lib/types";
import { useEvidence, useHeatmap, useTripwire } from "../hooks/useTripwire";

const RESULT_TONE: Record<Result, Tone> = { ok: "ok", denied: "bad", error: "held" };

/** Split a millisecond value into a display number + unit, exactly like `fmtMs` (DASH when unmeasured). */
function msParts(v: number | null | undefined): { value: number | undefined; digits: number; unit: string } {
  if (!isNum(v)) return { value: undefined, digits: 0, unit: "" };
  if (v >= 10_000) return { value: v / 1000, digits: 1, unit: "s" };
  return { value: v, digits: v < 10 ? 1 : 0, unit: "ms" };
}

/* ------------------------------------------------------------------------------------------------
 * Proof panel: an inset well under each card. "Live proof" = read from this session's stream / API;
 * "Scan receipt" = a committed static fact (Semgrep), labelled as such. Every value is real or DASH.
 * ---------------------------------------------------------------------------------------------- */
function Proof({
  kind,
  live,
  source,
  children,
}: {
  kind: "live" | "receipt";
  /** Stream connection is live (colours the dot; the label never changes meaning). */
  live?: boolean;
  source: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className="overflow-hidden rounded-xl border border-line bg-panel-2">
      <header className="flex items-center justify-between gap-3 pl-4 pr-2 pt-2.5">
        {kind === "live" ? (
          <Eyebrow dot tone={live ? "ok" : "held"}>Live proof</Eyebrow>
        ) : (
          <Eyebrow dot>Scan receipt</Eyebrow>
        )}
        <Tooltip content={source} side="top" align="end">
          <button
            type="button"
            aria-label="Where this proof comes from"
            className="inline-flex h-7 cursor-help items-center gap-1 rounded-md px-2 text-[12px] text-dim transition-colors duration-150 hover:bg-panel-3 hover:text-fg"
          >
            <Info className="size-3.5" strokeWidth={1.75} />
            Source
          </button>
        </Tooltip>
      </header>
      {children}
    </section>
  );
}

/** Hero value of a proof panel: 40px display number, small unit, one muted label line. */
function Hero({
  children,
  unit,
  label,
  loading,
}: {
  children: ReactNode;
  unit?: string;
  label: ReactNode;
  loading?: boolean;
}) {
  return (
    <div className="px-4 pb-4 pt-2">
      {loading ? (
        <Skeleton className="h-10 w-36" />
      ) : (
        <div className="flex items-baseline gap-1.5 whitespace-nowrap">
          {children}
          {unit && <span className="text-[16px] font-medium text-muted">{unit}</span>}
        </div>
      )}
      <p className="mt-1.5 text-[13px] leading-snug text-muted">{label}</p>
    </div>
  );
}

function Rows({ children }: { children: ReactNode }) {
  return <div className="divide-y divide-line border-t border-line">{children}</div>;
}

const ROW = "flex min-h-10 items-center gap-4 px-4 py-2 text-[13px] transition-colors duration-150 hover:bg-panel-3/60";

/** Label (muted sans) left, value (mono, tabular) right. */
function Row({ label, children, labelClassName }: { label: ReactNode; children: ReactNode; labelClassName?: string }) {
  return (
    <div className={cn(ROW, "justify-between")}>
      <span className={cn("shrink-0 text-muted", labelClassName)}>{label}</span>
      <span className="flex min-w-0 items-center justify-end gap-2 text-right font-mono text-[13px] text-fg">{children}</span>
    </div>
  );
}

/** One id per row (model ids): model-tone dot + click-to-copy id. */
function IdRow({ value }: { value: string }) {
  return (
    <div className={ROW}>
      <span aria-hidden className="size-1.5 shrink-0 rounded-full bg-model" />
      <CopyId value={value} className="min-w-0 text-[13px]" />
    </div>
  );
}

const Dash = () => <span className="text-dim">{DASH}</span>;

/* ------------------------------------------------------------------------------------------------
 * Sponsor card: white bento card, sponsor row, framed decorative visual, headline + concise role
 * line, then the proof panel pinned to the bottom so proofs align across a row.
 * ---------------------------------------------------------------------------------------------- */
function SponsorCard({
  reveal,
  className,
  name,
  icon,
  tag,
  headline,
  role,
  art,
  proof,
  action,
  after,
}: {
  reveal: number;
  className?: string;
  name: string;
  icon: ReactNode;
  tag: ReactNode;
  headline: string;
  role: ReactNode;
  art: ReactNode;
  proof: ReactNode;
  /** Header-right control (e.g. Run Guild agent). */
  action?: ReactNode;
  /** Below the proof (e.g. the Guild run result). */
  after?: ReactNode;
}) {
  return (
    <GlowCard
      reveal={reveal}
      glow="none"
      className={cn("transition-[box-shadow,border-color] duration-200 ease-out hover:border-line-strong hover:shadow-lift", className)}
      bodyClassName="flex flex-col gap-5 p-6"
    >
      <div className="flex items-center gap-3">
        <span className="grid size-9 shrink-0 place-items-center rounded-[10px] border border-line bg-panel text-fg shadow-sm [&_svg]:size-[18px]">
          {icon}
        </span>
        <div className="min-w-0 flex-1">
          <h3 className="truncate text-[16px] font-semibold leading-tight tracking-[-0.01em] text-fg">{name}</h3>
          <p className="truncate text-[13px] leading-snug text-dim">{tag}</p>
        </div>
        {action}
      </div>
      {art}
      <div>
        <h4 className="text-[20px] font-semibold leading-tight tracking-[-0.02em] text-fg">{headline}</h4>
        <p className="mt-2 max-w-prose text-[14px] leading-[1.55] text-muted">{role}</p>
      </div>
      <div className="mt-auto flex flex-col gap-3">
        {proof}
        {after}
      </div>
    </GlowCard>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Guild: the Run button (header) and its real upstream answer (below the proof).
 * ---------------------------------------------------------------------------------------------- */
type GuildOut = { ok: boolean; text: string; url?: string | null };

function useGuildRun() {
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState<GuildOut | null>(null);
  async function run() {
    setBusy(true);
    try {
      const r = await api.guildRun();
      setOut({ ok: true, text: `upstream HTTP ${r.status} · ${r.body || "(empty body)"}`, url: r.session_url });
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
  return { busy, out, run };
}

function GuildRunButton({ busy, onRun }: { busy: boolean; onRun: () => void }) {
  return (
    <Tooltip content="Starts the Guild-hosted agent (POST /guild/run). Its tool calls then pass through this checkpoint.">
      <Button variant="outline" size="sm" className="shrink-0" onClick={onRun} disabled={busy}>
        {busy ? <LoaderCircle className="animate-spin" /> : <Play />} Run Guild agent
      </Button>
    </Tooltip>
  );
}

function GuildRunResult({ out }: { out: GuildOut | null }) {
  if (!out) return null;
  return (
    <div
      className={cn(
        "animate-pop-in break-words rounded-[10px] border px-3 py-2 font-mono text-[12px] leading-relaxed",
        out.ok ? "tint-ok" : "tint-held",
      )}
      title={out.text}
    >
      {out.text.length > 240 ? `${out.text.slice(0, 240)}…` : out.text}
      {out.url && (
        <a
          href={out.url}
          target="_blank"
          rel="noreferrer"
          className="mt-1.5 flex w-fit items-center gap-1 font-sans text-[13px] font-medium text-brand underline-offset-4 hover:underline"
        >
          Open Guild session <ArrowUpRight className="size-3.5" strokeWidth={1.75} />
        </a>
      )}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Tab
 * ---------------------------------------------------------------------------------------------- */
export function SponsorsTab() {
  const { state } = useTripwire();
  const { data: e, isLoading: evLoading } = useEvidence();
  const { data: hm, isLoading: hmLoading } = useHeatmap(72, false);
  const guild = useGuildRun();
  const live = state.connection === "live";

  const bt = state.lastBacktest;
  const guildAgents = [...new Set([...Object.keys(state.modes), ...Object.keys(state.stats)])].filter((a) =>
    a.startsWith("guild:"),
  );
  const lastCodeRef = state.events.find((x) => x.code_ref)?.code_ref;
  const lastGuild = guildAgents
    .map((g) => state.stats[g]?.last)
    .filter((x): x is ToolEvent => !!x)
    .reduce<ToolEvent | undefined>((a, b) => (!a || b.ts_ms > a.ts_ms ? b : a), undefined);
  const nModels = state.modelIds.length;
  const nApproved = state.approvedIds.length;
  const stored = e?.events_stored;
  const hold = msParts(e?.hold_decision_ms);
  const evPending = evLoading && !e;

  // One-off visual effects keyed on REAL changes only (see components/sponsors/art.tsx).
  const storedFlash = useChangeCount(stored);
  const modelPulse = useChangeCount(nModels, true);
  const guildPulse = useChangeCount(
    lastGuild ? `${lastGuild.agent_id}|${lastGuild.ts_ms}|${lastGuild.action}|${lastGuild.target}` : undefined,
    true,
  );
  const approvedRedraw = useChangeCount(nApproved);

  const shownModels = state.modelIds.slice(0, 3);
  const shownGuild = guildAgents.slice(0, 3);

  return (
    <div className="flex flex-col gap-6">
      <SectionHeader
        eyebrow="Sponsors · built with"
        pre="Our"
        em="stack"
        description="What each sponsor does inside Tripwire, with its proof beside it. Live proof is read from this session's stream and API; Semgrep's counts come from the committed scan receipt."
        actions={
          <div className="flex items-center gap-4 text-[13px] text-muted">
            <Tooltip content="Read live from this session's event stream and the checkpoint API">
              <span tabIndex={0} className="inline-flex cursor-help items-center gap-2 rounded-md">
                <span aria-hidden className={cn("size-1.5 rounded-full", live ? "bg-ok" : "bg-held")} />
                Live proof
              </span>
            </Tooltip>
            <Tooltip content="A committed, static fact from the repo (not a live value)">
              <span tabIndex={0} className="inline-flex cursor-help items-center gap-2 rounded-md">
                <span aria-hidden className="size-1.5 rounded-full bg-line-strong" />
                Scan receipt
              </span>
            </Tooltip>
          </div>
        }
      />

      <div className="grid grid-cols-12 gap-4">
        {/* ClickHouse */}
        <SponsorCard
          reveal={0}
          className="col-span-12 lg:col-span-7"
          name="ClickHouse"
          icon={<Database strokeWidth={1.75} />}
          tag="Event store and analytics"
          headline="Every call, on record"
          role="Every tool call lands in tripwire.events. Detection queries it every second; the fleet heatmap and policy backtests scan the whole table, each with an SQL receipt (ms + rows read)."
          art={
            <ArtFrame>
              <ColumnsArt flash={storedFlash} />
            </ArtFrame>
          }
          proof={
            <Proof
              kind="live"
              live={live}
              source={
                <>
                  Source: <span className="font-mono">GET /evidence</span> events_stored ·{" "}
                  <span className="font-mono">GET /fleet/heatmap</span> query_ms + rows_read · the last policy backtest
                  run in this session
                </>
              }
            >
              <Hero loading={evPending} label="events stored in tripwire.events">
                <InkMarker tone="info" trigger={stored} active={isNum(stored)}>
                  <Count value={stored} className="num-display text-[40px]" />
                </InkMarker>
              </Hero>
              <Rows>
                <Row label="Fleet heatmap query">
                  {hmLoading && !hm ? (
                    <Skeleton className="h-3.5 w-40" />
                  ) : hm ? (
                    <span className="truncate">
                      {fmtMs(hm.query_ms)} · {fmtInt(hm.rows_read)} rows read
                    </span>
                  ) : (
                    <Dash />
                  )}
                </Row>
                <Row label="Policy backtest">
                  {bt ? (
                    <span className="truncate">
                      {fmtMs(bt.query_ms)} · {fmtInt(bt.events_scanned)} rows scanned
                    </span>
                  ) : (
                    <Dash />
                  )}
                </Row>
              </Rows>
            </Proof>
          }
        />

        {/* AkashML */}
        <SponsorCard
          reveal={1}
          className="col-span-12 md:col-span-6 lg:col-span-5"
          name="Akash (AkashML)"
          icon={<Brain strokeWidth={1.75} />}
          tag="Model verdicts"
          headline="Verdicts, on Akash"
          role="Verdicts on held actions and detections run on AkashML (meta-llama/Llama-3.3-70B-Instruct, chosen by measured latency). Cost per 1,000 events vs OpenAI is measured by the eval and shown on the Evidence tab; at today's list prices OpenAI's gpt-4o-mini is slightly cheaper. We run on AkashML for open models on decentralized compute."
          art={
            <ArtFrame>
              <RingArt pulse={modelPulse} />
            </ArtFrame>
          }
          proof={
            <Proof
              kind="live"
              live={live}
              source="Source: model_ids on incident verdicts, alerts and quorum votes seen on this session's stream"
            >
              <Hero label={`distinct verdict model id${nModels === 1 ? "" : "s"} seen`}>
                <Count value={nModels > 0 ? nModels : undefined} className="num-display text-[40px]" />
              </Hero>
              <Rows>
                {shownModels.length ? (
                  shownModels.map((m) => <IdRow key={m} value={m} />)
                ) : (
                  <Row label="Model ids">
                    <Dash />
                  </Row>
                )}
                {nModels > shownModels.length && (
                  <Row label="More">
                    <Tooltip content={<span className="font-mono">{state.modelIds.slice(3).join(", ")}</span>}>
                      <span tabIndex={0} className="cursor-help text-muted">
                        +{nModels - shownModels.length} more
                      </span>
                    </Tooltip>
                  </Row>
                )}
              </Rows>
            </Proof>
          }
        />

        {/* Semgrep */}
        <SponsorCard
          reveal={2}
          className="col-span-12 md:col-span-6 lg:col-span-4"
          name="Semgrep"
          icon={<Bug strokeWidth={1.75} />}
          tag="Agent-security rules"
          headline="Our own code, scanned"
          role="Semgrep scans our own AI-written code with 6 custom agent-security rules plus registry packs. Every finding is triaged; the real one (untrusted text reaching our verdict model, OWASP LLM01) is fixed. Runtime events carry code_ref (file:line), so a denial links back to code."
          art={
            <ArtFrame>
              <CodeArt />
            </ArtFrame>
          }
          proof={
            // The scan counts are a static fact from the committed receipt (semgrep/FINDINGS.md), not a
            // live value, and are labelled as such. Only the code_ref row is live.
            <Proof
              kind="receipt"
              source="Static fact from the committed scan receipt semgrep/FINDINGS.md (re-scan 13:58 PT). Not a live value."
            >
              <div className="px-4 pb-4 pt-2">
                <p className="text-[14px] leading-[1.6] text-muted">
                  <span className="num-display text-[28px] text-fg">223</span> files ·{" "}
                  <span className="num-display text-[28px] text-fg">13</span> findings triaged ·{" "}
                  <span className="num-display text-[28px] text-ok">0</span> open true positives (#1 LLM01 fixed)
                </p>
                <p className="mt-1.5 font-mono text-[12px] text-dim">receipt: semgrep/FINDINGS.md (re-scan 13:58)</p>
              </div>
              <Rows>
                <Row
                  label={
                    <Tooltip content="Live: code_ref on the newest stream event that carries one">
                      <span tabIndex={0} className="inline-flex cursor-help items-center gap-2 rounded-md">
                        <span aria-hidden className={cn("size-1.5 rounded-full", live ? "bg-ok" : "bg-held")} />
                        Last code_ref
                      </span>
                    </Tooltip>
                  }
                >
                  {lastCodeRef ? <CopyId key={lastCodeRef} value={lastCodeRef} className="animate-pop-in text-[13px]" /> : <Dash />}
                </Row>
              </Rows>
            </Proof>
          }
        />

        {/* Guild */}
        <SponsorCard
          reveal={3}
          className="col-span-12 md:col-span-6 lg:col-span-4"
          name="Guild"
          icon={<Workflow strokeWidth={1.75} />}
          tag="Hosted agent"
          headline="Guild agents, governed"
          role="A Guild-hosted agent (guild:*) runs in the Guild workspace and is governed by the same checkpoint: its tool calls pass through Tripwire like any other agent's."
          action={<GuildRunButton busy={guild.busy} onRun={guild.run} />}
          art={
            <ArtFrame>
              <LinkArt pulse={guildPulse} pulseTone={lastGuild ? (RESULT_TONE[lastGuild.result] ?? "info") : "info"} />
            </ArtFrame>
          }
          proof={
            <Proof
              kind="live"
              live={live}
              source="Source: agents whose id starts with guild: on this session's stream, their mode, and their newest tool call"
            >
              <Hero label={`guild:* agent${guildAgents.length === 1 ? "" : "s"} seen this session`}>
                <Count value={guildAgents.length > 0 ? guildAgents.length : undefined} className="num-display text-[40px]" />
              </Hero>
              <Rows>
                {shownGuild.map((g) => (
                  <Row key={g} label={<CopyId value={g} className="text-[13px]" />} labelClassName="min-w-0 shrink text-fg">
                    <ModeBadge mode={state.modes[g]} />
                  </Row>
                ))}
                <Row label="Last call">
                  {lastGuild ? (
                    <>
                      <span className="truncate" title={`${lastGuild.action} ${lastGuild.target}`}>
                        {lastGuild.action}
                      </span>
                      <ResultBadge result={lastGuild.result} reason={lastGuild.reason} />
                    </>
                  ) : (
                    <Dash />
                  )}
                </Row>
              </Rows>
            </Proof>
          }
          after={<GuildRunResult out={guild.out} />}
        />

        {/* Pi · Most Innovative */}
        <SponsorCard
          reveal={4}
          className="col-span-12 md:col-span-6 lg:col-span-4"
          name="Pi · Most Innovative"
          icon={<Lightbulb strokeWidth={1.75} />}
          tag="Prevent → trip → trace → cure"
          headline="Cure, with proof"
          role="The risky send is held before it runs, patient zero is traced across the fleet, and the fix is proven by replay + backtest before a human approves it."
          art={
            <ArtFrame>
              <LoopArt cured={nApproved > 0} redraw={approvedRedraw} />
            </ArtFrame>
          }
          proof={
            <Proof
              kind="live"
              live={live}
              source={
                <>
                  Source: <span className="font-mono">GET /evidence</span> hold_decision_ms (median of measured hold
                  decisions) · hold mode from the checkpoint · guardrails approved in this session
                </>
              }
            >
              <Hero loading={evPending} unit={hold.unit} label="median hold decision">
                <InkMarker tone="held" trigger={hold.value} active={isNum(hold.value)}>
                  <Count
                    value={hold.value}
                    digits={hold.digits}
                    minDigits={hold.digits}
                    className="num-display text-[40px]"
                  />
                </InkMarker>
              </Hero>
              <Rows>
                <Row label="Hold mode">
                  {state.holdEnabled === null ? (
                    <Dash />
                  ) : (
                    <Tooltip
                      content={
                        state.holdEnabled
                          ? "Hold mode ON — risky actions wait for a verdict"
                          : "Hold mode OFF — detect after the fact"
                      }
                    >
                      <Badge tabIndex={0} variant={state.holdEnabled ? "held" : "muted"} className="cursor-help font-sans">
                        {state.holdEnabled && <Hand strokeWidth={2} />}
                        {state.holdEnabled ? "ON" : "OFF"}
                      </Badge>
                    </Tooltip>
                  )}
                </Row>
                <Row label="Guardrails proven + approved">
                  <span>
                    <Count value={nApproved} /> <span className="text-muted">this session</span>
                  </span>
                </Row>
              </Rows>
            </Proof>
          }
        />
      </div>
    </div>
  );
}

export default SponsorsTab;
