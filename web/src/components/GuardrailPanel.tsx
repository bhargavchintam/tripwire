import { useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import {
  Ban,
  ExternalLink,
  FlaskConical,
  LoaderCircle,
  ShieldCheck,
  ShieldPlus,
  TriangleAlert,
  Workflow,
} from "lucide-react";
import { toast } from "sonner";
import { Button } from "./ui/button";
import { Badge } from "./ui/badge";
import { Tooltip } from "./ui/tooltip";
import { BacktestCard } from "./BacktestCard";
import { SectionHead } from "./incidents/SheetSection";
import { Chip, EASE_OUT, SPRING_SOFT, toneText, type Tone } from "./fx";
import { api, ApiError } from "../lib/api";
import { DASH, fmtClock, fmtMs } from "../lib/format";
import { cn } from "../lib/utils";
import type { GuardrailGate, GuardrailProof } from "../lib/types";
import { useTripwire } from "../hooks/useTripwire";

const GATE_LABEL: Record<string, string> = {
  replay_refused: "Replay of the incident is refused",
  normal_ops_ok: "Normal operations still allowed",
  backtest: "Backtest over fleet history",
  policy_lint: "Policy lint (valid, no allow/deny conflict)",
};

/** Seconds between gates appearing (unchanged from v1: gate i appears at 0.45 * i). */
const GATE_STEP = 0.45;

/** Circle + check / cross / dash that draws itself once, after its gate row has risen in. */
function DrawnMark({
  kind,
  delay,
  className = "size-[22px] bg-panel",
}: {
  kind: "pass" | "fail" | "skip";
  delay: number;
  className?: string;
}) {
  const reduce = useReducedMotion();
  const from = reduce ? false : { pathLength: 0, opacity: 0 };
  const to = { pathLength: 1, opacity: 1 };
  const t = (extra: number, duration = 0.32) => ({ duration, ease: EASE_OUT, delay: delay + extra });
  return (
    <svg
      viewBox="0 0 24 24"
      aria-hidden
      className={cn("shrink-0 rounded-full", className)}
      fill="none"
      stroke="currentColor"
      strokeWidth={2}
      strokeLinecap="round"
      strokeLinejoin="round"
    >
      <g transform="rotate(-90 12 12)">
        <motion.circle cx={12} cy={12} r={10} strokeOpacity={0.35} initial={from} animate={to} transition={t(0, 0.45)} />
      </g>
      {kind === "pass" && <motion.path d="M7.5 12.4l3.1 3.1 6-6.4" initial={from} animate={to} transition={t(0.35)} />}
      {kind === "fail" && (
        <>
          <motion.path d="M9 9l6 6" initial={from} animate={to} transition={t(0.35, 0.2)} />
          <motion.path d="M15 9l-6 6" initial={from} animate={to} transition={t(0.5, 0.2)} />
        </>
      )}
      {kind === "skip" && <motion.path d="M8 12h8" initial={from} animate={to} transition={t(0.35)} />}
    </svg>
  );
}

/** One row of the vertical checklist: drawn mark, gate name + detail, per-gate ms, result word. */
function Gate({ g, i, last }: { g: GuardrailGate; i: number; last: boolean }) {
  const skipped = g.passed === null || g.passed === undefined;
  const kind = skipped ? "skip" : g.passed ? "pass" : "fail";
  const tone: Tone = skipped ? "neutral" : g.passed ? "ok" : "bad";
  const reduce = useReducedMotion();
  return (
    <motion.li
      initial={reduce ? false : { opacity: 0, y: 6 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ ...SPRING_SOFT, delay: GATE_STEP * i }}
      className={cn(
        "relative flex items-start gap-3 px-4 py-3 transition-colors duration-150 hover:bg-panel-2",
        !skipped && !g.passed && "bg-bad-soft/50 hover:bg-bad-soft/70",
      )}
    >
      {/* spine segment to the next gate's mark; draws down as that gate lands */}
      {!last && (
        <motion.span
          aria-hidden
          className="absolute left-[26px] top-6 -bottom-[25px] z-[1] w-px origin-top bg-line-strong"
          initial={reduce ? false : { scaleY: 0 }}
          animate={{ scaleY: 1 }}
          transition={{ duration: GATE_STEP, ease: EASE_OUT, delay: GATE_STEP * i + 0.2 }}
        />
      )}
      <span className={cn("relative z-10 mt-px", toneText[tone])}>
        <DrawnMark kind={kind} delay={GATE_STEP * i + 0.12} />
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-baseline gap-3">
          <span className="min-w-0 text-sm font-medium leading-snug text-fg">{GATE_LABEL[g.name] ?? g.name}</span>
          <span className="ml-auto flex shrink-0 items-baseline gap-2.5 whitespace-nowrap">
            <Tooltip content="Time this gate took, as measured">
              <span className="font-mono text-xs tabular-nums text-muted">{fmtMs(g.ms)}</span>
            </Tooltip>
            <span className={cn("w-14 text-right text-xs font-medium", toneText[tone])}>
              {skipped ? "Skipped" : g.passed ? "Passed" : "Failed"}
            </span>
          </span>
        </div>
        <div className="mt-0.5 flex flex-wrap items-baseline gap-x-2 text-xs text-dim">
          <span className="font-mono">gate {i + 1}</span>
          {g.detail ? <span className="min-w-0 break-words font-mono text-muted">{g.detail}</span> : null}
        </div>
      </div>
    </motion.li>
  );
}

function HostChips({ items, tone, icon }: { items: string[]; tone: "ok" | "bad"; icon: ReactNode }) {
  if (!items.length) return <span className="text-sm text-dim">{DASH}</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((h) => (
        <Chip key={h} tone={tone} mono size="md" icon={icon} title={h} className="max-w-full">
          <span className="truncate">{h}</span>
        </Chip>
      ))}
    </div>
  );
}

/** Proven cure: prove (gates + backtest) -> human approves -> policy applied, agent restored. */
export function GuardrailPanel({ incidentId }: { incidentId: string }) {
  const { state, recordApproved } = useTripwire();
  const reduce = useReducedMotion();
  const [local, setLocal] = useState<GuardrailProof | null>(null);
  const [proving, setProving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [runKey, setRunKey] = useState(0);
  const [asking, setAsking] = useState(false);
  const [guildUrl, setGuildUrl] = useState<string | null>(null);
  const proof = local ?? state.guardrails[incidentId]?.proof ?? null;
  const approved = state.guardrails[incidentId]?.approved;
  const nGates = proof?.gates.length ?? 0;
  const gatesDelay = nGates * GATE_STEP;
  // Incident destinations the outbreak trace had already pushed to the denylist (so not "added" here).
  const incident = state.incidents[incidentId];
  const incHosts = new Set(
    (incident?.steps ?? [])
      .filter((s) => s.action.startsWith("http_"))
      .map((s) => s.target.split("://").pop()?.split("/")[0]?.split("?")[0]?.toLowerCase() ?? ""),
  );
  const already = (proof?.candidate?.denylist ?? []).filter(
    (h) => incHosts.has(h.toLowerCase()) && !proof?.added_denylist.includes(h),
  );
  // Section hue from real state: approved / all gates passed = ok, a failed proof = bad, nothing yet = neutral.
  const tone: Tone = approved ? "ok" : proof ? (proof.all_passed ? "ok" : "bad") : "neutral";

  /** Human approval step on Guild: the Responder agent drafts the case from this incident and pauses
   *  on ui_prompt until a person replies APPROVE / REJECT in the Guild session. Applying stays here. */
  async function askGuild() {
    if (!proof) return;
    setAsking(true);
    try {
      const steps = (incident?.steps ?? []).slice(-6).map((s) => `${s.action} ${s.target} -> ${s.result}${s.reason ? ` (${s.reason})` : ""}`);
      const v = incident?.verdict;
      const caseJson = {
        incident_id: incidentId,
        agent_id: incident?.agent_id,
        report_md:
          incident?.report_md ||
          `(no investigator report) rule: ${incident?.rule}; verdict: ${v?.verdict} by ${v?.decision_source}; steps: ${steps.join("; ")}`,
        proposed_cure: `add ${proof.added_denylist.join(", ") || "no new hosts"} to the fleet denylist (policy v${proof.candidate?.version ?? "?"}); proof gates: ${proof.gates.map((g) => `${g.name}=${g.passed === null ? "skipped" : g.passed ? "pass" : "fail"}`).join(", ")}`,
      };
      const r = await api.guildRun({ role: "responder", prompt: JSON.stringify(caseJson) });
      setGuildUrl(r.session_url ?? null);
      if (r.status >= 200 && r.status < 300) toast.success("Approval requested from a human in Guild");
      else toast.error(`Guild answered HTTP ${r.status}`);
    } catch (e) {
      toast.error(`Guild request failed: ${(e as Error).message}`);
    } finally {
      setAsking(false);
    }
  }

  async function prove() {
    setProving(true);
    setErr(null);
    try {
      const p = await api.prove(incidentId);
      setLocal(p);
      setRunKey((k) => k + 1);
    } catch (e) {
      const msg = (e as Error).message;
      setErr(e instanceof ApiError && e.status === 501 ? "Not implemented on this checkpoint yet (501)." : msg);
      toast.error(`Prove failed: ${msg}`);
    } finally {
      setProving(false);
    }
  }

  async function approve() {
    setApproving(true);
    try {
      const r = await api.approve(incidentId);
      recordApproved(r);
      toast.success(`Guardrail approved · policy v${r.policy_version}`, {
        description: r.restored?.length ? `Restored ${r.restored.join(", ")}` : "No agent needed restoring",
      });
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      // 409 = unproven OR already approved; the checkpoint's detail says which.
      toast.error(status === 409 ? `Approve refused: ${(e as Error).message}` : `Approve failed: ${(e as Error).message}`);
    } finally {
      setApproving(false);
    }
  }

  const approveDisabled = !proof?.all_passed || approving || !!approved;

  return (
    <div className="flex flex-col">
      <SectionHead
        icon={<FlaskConical className={tone === "neutral" ? undefined : toneText[tone]} />}
        label="Proven cure · prove → approve"
        meta={
          <>
            {proof ? (
              <Tooltip content="When the proof ran (checkpoint clock)">
                <span className="font-mono text-xs tabular-nums text-dim">
                  proved {fmtClock(proof.proved_at_ms, false)}
                  {proof.mock ? " · mock" : ""}
                </span>
              </Tooltip>
            ) : null}
            {approved ? (
              <Badge key="approved" variant="ok" className="animate-pop-in">
                <ShieldCheck /> Approved · v{approved.policy_version}
              </Badge>
            ) : null}
          </>
        }
        className="mb-2"
      />
      <p className="max-w-xl text-sm leading-[1.55] text-muted">
        Tripwire proves the candidate guardrail against this incident and the fleet history; a human
        approves before it is applied.
      </p>

      {/* actions: secondary, secondary, then the one primary */}
      <div className="mt-4 flex flex-wrap items-center gap-2">
        <Button variant="outline" onClick={prove} disabled={proving}>
          {proving ? <LoaderCircle className="animate-spin" /> : <FlaskConical strokeWidth={1.75} />}
          {proof ? "Prove again" : "Prove guardrail"}
        </Button>
        {proof?.all_passed && !approved && (
          <Button variant="outline" onClick={askGuild} disabled={asking} className="animate-pop-in">
            {asking ? <LoaderCircle className="animate-spin" /> : <Workflow strokeWidth={1.75} />}
            Ask a human in Guild
          </Button>
        )}
        <Tooltip
          content={
            approved
              ? `Applied as policy v${approved.policy_version}`
              : !proof
                ? "Prove the guardrail first"
                : !proof.all_passed
                  ? "Every gate must pass before approval"
                  : "Apply the policy and restore the agent"
          }
        >
          {/* span wrapper so the tooltip still works while the button is disabled */}
          <span className="inline-flex">
            <Button variant="default" onClick={approve} disabled={approveDisabled}>
              {approving ? <LoaderCircle className="animate-spin" /> : <ShieldCheck strokeWidth={1.75} />}
              {approved ? `Approved · v${approved.policy_version}` : "Approve & restore"}
            </Button>
          </span>
        </Tooltip>
        {guildUrl && (
          <Tooltip content="Open the Guild session">
            <a
              href={guildUrl}
              target="_blank"
              rel="noreferrer"
              className="tint-info inline-flex h-7 animate-pop-in items-center gap-1.5 rounded-full border px-3 text-[13px] font-medium transition-[border-color,box-shadow] duration-200 hover:border-info hover:shadow-sm focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
            >
              <Workflow className="size-3.5" strokeWidth={1.75} aria-hidden />
              Approval requested in Guild
              <ExternalLink className="size-3" strokeWidth={1.75} aria-hidden />
            </a>
          </Tooltip>
        )}
      </div>

      {!proof && !err && (
        <div className="mt-4 flex flex-wrap items-center gap-x-2 gap-y-1 text-[13px] text-dim">
          <span>Gates:</span>
          <span className="text-muted">replay refused · normal ops ok · backtest · lint</span>
          <span aria-hidden>→</span>
          <span className="text-muted">human approves</span>
        </div>
      )}
      {proof && !proof.all_passed && (
        <div className="mt-3 flex items-center gap-1.5 text-[13px] text-bad">
          <TriangleAlert className="size-3.5" strokeWidth={1.75} aria-hidden />
          Not all gates passed. Approval is disabled.
        </div>
      )}
      {err && (
        <div className="tint-held mt-4 flex items-start gap-2 rounded-xl border px-3.5 py-2.5 text-sm">
          <TriangleAlert className="mt-0.5 size-4 shrink-0" strokeWidth={1.75} aria-hidden />
          {err}
        </div>
      )}

      {proof && (
        <div key={runKey} className="mt-5 flex flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <div className="min-w-0 rounded-xl border border-line bg-panel-2 p-3.5">
              <div className="eyebrow mb-2.5">
                Added to allowlist · <span className="tabular-nums">{proof.added_allowlist.length}</span>
              </div>
              <HostChips items={proof.added_allowlist} tone="ok" icon={<ShieldPlus />} />
            </div>
            <div className="min-w-0 rounded-xl border border-line bg-panel-2 p-3.5">
              <div className="eyebrow mb-2.5">
                Added to denylist · <span className="tabular-nums">{proof.added_denylist.length}</span>
              </div>
              <HostChips items={proof.added_denylist} tone="bad" icon={<Ban />} />
              {already.length > 0 && (
                <div className="mt-2.5 flex flex-wrap items-center gap-1.5">
                  <span className="text-xs text-dim">Already denied fleet-wide:</span>
                  {already.map((h) => (
                    <Chip key={h} tone="neutral" mono icon={<Ban />}>
                      {h}
                    </Chip>
                  ))}
                </div>
              )}
            </div>
          </div>

          {/* vertical checklist: a spine draws down from mark to mark as each gate lands */}
          <div className="overflow-hidden rounded-xl border border-line bg-panel shadow-sm">
            <ol className="divide-y divide-line" aria-label="Proof gates">
              {proof.gates.map((g, i) => (
                <Gate key={`${g.name}-${i}`} g={g} i={i} last={i === nGates - 1} />
              ))}
            </ol>
          </div>

          <motion.div
            initial={reduce ? false : { opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...SPRING_SOFT, delay: gatesDelay }}
          >
            <BacktestCard bt={proof.backtest} title="Backtest of the candidate" inkDelay={gatesDelay + 0.35} />
          </motion.div>

          <motion.div
            initial={reduce ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ ...SPRING_SOFT, delay: gatesDelay + 0.2 }}
            className={cn(
              "flex items-center gap-3 rounded-xl border px-4 py-3 text-sm font-semibold",
              proof.all_passed ? "tint-ok" : "tint-bad",
            )}
          >
            <DrawnMark kind={proof.all_passed ? "pass" : "fail"} delay={gatesDelay + 0.3} className="size-6" />
            {proof.all_passed ? "All gates passed. Ready for human approval." : "Proof failed"}
          </motion.div>
        </div>
      )}
    </div>
  );
}
