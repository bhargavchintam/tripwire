import { useState, type ReactNode } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Ban, CircleCheck, CircleMinus, CircleX, FlaskConical, LoaderCircle, ShieldCheck, ShieldPlus } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Badge } from "./ui/badge";
import { Button } from "./ui/button";
import { BacktestCard } from "./BacktestCard";
import { api, ApiError } from "../lib/api";
import { DASH, fmtClock, fmtMs } from "../lib/format";
import type { GuardrailGate, GuardrailProof } from "../lib/types";
import { useTripwire } from "../hooks/useTripwire";

const GATE_LABEL: Record<string, string> = {
  replay_refused: "Replay of the incident is refused",
  normal_ops_ok: "Normal operations still allowed",
  backtest: "Backtest over fleet history",
  policy_lint: "Policy lint (valid, no allow/deny conflict)",
};

function Gate({ g, i }: { g: GuardrailGate; i: number }) {
  const skipped = g.passed === null || g.passed === undefined;
  const Icon = skipped ? CircleMinus : g.passed ? CircleCheck : CircleX;
  const tone = skipped ? "text-muted border-line" : g.passed ? "text-ok border-ok/50" : "text-bad border-bad/60";
  return (
    <motion.li
      initial={{ opacity: 0, x: -12 }}
      animate={{ opacity: 1, x: 0 }}
      transition={{ delay: 0.45 * i, duration: 0.3 }}
      className={`flex items-start gap-2 rounded-lg border bg-panel-2 px-3 py-2 ${tone}`}
    >
      <Icon className="mt-0.5 size-4 shrink-0" />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-sm font-semibold text-fg">{GATE_LABEL[g.name] ?? g.name}</span>
          <span className="font-mono text-[11px]">{skipped ? "skipped" : g.passed ? "passed" : "failed"}</span>
          <span className="ml-auto font-mono text-[11px] text-muted">{fmtMs(g.ms)}</span>
        </div>
        {g.detail ? <div className="break-words font-mono text-[11px] text-muted">{g.detail}</div> : null}
      </div>
    </motion.li>
  );
}

function Chips({ items, variant, icon }: { items: string[]; variant: "ok" | "bad"; icon: ReactNode }) {
  if (!items.length) return <span className="text-sm text-dim">{DASH}</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((h) => (
        <Badge key={h} variant={variant} className="px-2 py-1 font-mono text-xs">
          {icon} {h}
        </Badge>
      ))}
    </div>
  );
}

/** Proven cure: prove (gates + backtest) → human approves → policy applied, agent restored. */
export function GuardrailPanel({ incidentId }: { incidentId: string }) {
  const { state, recordApproved } = useTripwire();
  const [local, setLocal] = useState<GuardrailProof | null>(null);
  const [proving, setProving] = useState(false);
  const [approving, setApproving] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [runKey, setRunKey] = useState(0);
  const proof = local ?? state.guardrails[incidentId]?.proof ?? null;
  const approved = state.guardrails[incidentId]?.approved;
  const gatesDelay = (proof?.gates.length ?? 0) * 0.45;
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

  return (
    <Card className={proof?.all_passed ? "border-ok/50" : undefined}>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <FlaskConical className="size-4" /> Proven cure
        </CardTitle>
        {proof ? (
          <span className="font-mono text-xs text-dim">
            proved {fmtClock(proof.proved_at_ms, false)}
            {proof.mock ? " · mock" : ""}
          </span>
        ) : null}
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="held" onClick={prove} disabled={proving}>
            {proving ? <LoaderCircle className="animate-spin" /> : <FlaskConical />}
            {proof ? "Prove again" : "Prove guardrail"}
          </Button>
          <Button variant="ok" onClick={approve} disabled={!proof?.all_passed || approving || !!approved}>
            {approving ? <LoaderCircle className="animate-spin" /> : <ShieldCheck />}
            {approved ? `Approved · v${approved.policy_version}` : "Approve & restore"}
          </Button>
          {!proof && !err && (
            <span className="text-xs text-dim">replay refused · normal ops ok · backtest · lint → human approves</span>
          )}
          {proof && !proof.all_passed && <span className="text-xs text-bad">not all gates passed — approval disabled</span>}
        </div>
        {err && <div className="text-sm text-held">{err}</div>}

        {proof && (
          <div key={runKey} className="flex flex-col gap-3">
            <div className="grid gap-3 md:grid-cols-2">
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
                  Added to allowlist · {proof.added_allowlist.length}
                </div>
                <Chips items={proof.added_allowlist} variant="ok" icon={<ShieldPlus />} />
              </div>
              <div>
                <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
                  Added to denylist · {proof.added_denylist.length}
                </div>
                <Chips items={proof.added_denylist} variant="bad" icon={<Ban />} />
                {already.length > 0 && (
                  <div className="mt-1.5 flex flex-wrap items-center gap-1.5">
                    <span className="text-[11px] text-dim">already denied fleet-wide:</span>
                    {already.map((h) => (
                      <Badge key={h} variant="muted" className="font-mono text-[11px]">
                        <Ban /> {h}
                      </Badge>
                    ))}
                  </div>
                )}
              </div>
            </div>
            <ol className="flex flex-col gap-1.5">
              {proof.gates.map((g, i) => (
                <Gate key={`${g.name}-${i}`} g={g} i={i} />
              ))}
            </ol>
            <AnimatePresence>
              <motion.div initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: gatesDelay }}>
                <BacktestCard bt={proof.backtest} title="Backtest of the candidate" />
              </motion.div>
            </AnimatePresence>
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ delay: gatesDelay + 0.2 }}
              className={`flex items-center gap-2 font-mono text-sm font-bold ${proof.all_passed ? "text-ok" : "text-bad"}`}
            >
              {proof.all_passed ? <CircleCheck className="size-4" /> : <CircleX className="size-4" />}
              {proof.all_passed ? "All gates passed — ready for human approval" : "Proof failed"}
            </motion.div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}
