import { useState, type ReactNode } from "react";
import { Ban, Database, Globe, Hand, LoaderCircle, Lock, Server, ShieldCheck, Sparkles, TriangleAlert } from "lucide-react";
import { toast } from "sonner";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge, type BadgeVariant } from "../components/ui/badge";
import { Button } from "../components/ui/button";
import { BacktestCard } from "../components/BacktestCard";
import { api, ApiError } from "../lib/api";
import type { Policy } from "../lib/types";
import { usePolicy, useTripwire } from "../hooks/useTripwire";

function Chips({ items, variant, icon, diff }: { items?: string[]; variant: BadgeVariant; icon: ReactNode; diff?: Set<string> }) {
  if (!items?.length) return <span className="text-sm text-dim">—</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((x) => (
        <Badge
          key={x}
          variant={variant}
          className={`px-2 py-1 font-mono text-xs ${diff?.has(x) ? "ring-2 ring-info/70" : ""}`}
          title={diff?.has(x) ? "new in the copilot preview" : undefined}
        >
          {icon} {x}
        </Badge>
      ))}
    </div>
  );
}

function Section({ title, children, hint }: { title: string; children: ReactNode; hint?: string }) {
  return (
    <Card>
      <CardHeader>
        <CardTitle>{title}</CardTitle>
        {hint && <span className="text-xs text-dim">{hint}</span>}
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}

/** Items in `next` that are not in `prev` (for highlighting a preview). */
function added(prev: string[] | undefined, next: string[] | undefined): Set<string> {
  const p = new Set(prev ?? []);
  return new Set((next ?? []).filter((x) => !p.has(x)));
}

function PolicyView({ p, base, compact = false }: { p: Policy; base?: Policy; compact?: boolean }) {
  const grid = compact ? "grid gap-4" : "grid gap-4 md:grid-cols-2";
  return (
    <div className="flex flex-col gap-4">
      <Section title="Allowlists · destinations each agent may post to without a hold">
        <div className="flex flex-col gap-3">
          {Object.entries(p.allowlists ?? {}).length === 0 && <span className="text-sm text-dim">—</span>}
          {Object.entries(p.allowlists ?? {}).map(([agent, hosts]) => (
            <div key={agent} className="flex flex-wrap items-center gap-3">
              <span className="w-36 font-mono text-sm font-semibold">{agent}</span>
              <Chips items={hosts} variant="ok" icon={<ShieldCheck />} diff={base ? added(base.allowlists?.[agent], hosts) : undefined} />
            </div>
          ))}
        </div>
      </Section>
      <div className={grid}>
        <Section title="Fleet denylist" hint="known-bad destinations (outbreak pushes here)">
          <Chips items={p.denylist} variant="bad" icon={<Ban />} diff={base ? added(base.denylist, p.denylist) : undefined} />
        </Section>
        <Section title="High-risk actions" hint="decided synchronously in hold mode">
          <Chips items={p.high_risk_actions} variant="held" icon={<Hand />} diff={base ? added(base.high_risk_actions, p.high_risk_actions) : undefined} />
        </Section>
        <Section title="Always denied" hint="fixed policy, no model call">
          <Chips items={p.fixed_deny_actions} variant="bad" icon={<Lock />} diff={base ? added(base.fixed_deny_actions, p.fixed_deny_actions) : undefined} />
        </Section>
        <Section title="Untrusted input prefixes" hint="used for outbreak tracing">
          <Chips items={p.untrusted_sources} variant="held" icon={<TriangleAlert />} diff={base ? added(base.untrusted_sources, p.untrusted_sources) : undefined} />
        </Section>
        <Section title="Internal hosts" hint="is_external = 0">
          <Chips items={p.internal_hosts} variant="info" icon={<Server />} diff={base ? added(base.internal_hosts, p.internal_hosts) : undefined} />
        </Section>
        {!compact && (
          <Section title="External by default">
            <div className="flex items-center gap-2 text-sm text-muted">
              <Globe className="size-4" /> Any host not listed as internal is treated as external.
            </div>
          </Section>
        )}
      </div>
    </div>
  );
}

function Copilot({ current, onApplied }: { current: Policy; onApplied: () => void }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [applying, setApplying] = useState(false);
  const [preview, setPreview] = useState<Policy | null>(null);
  const [msg, setMsg] = useState<{ tone: "held" | "bad"; text: string } | null>(null);

  async function draft() {
    if (!text.trim()) return;
    setBusy(true);
    setMsg(null);
    setPreview(null);
    try {
      setPreview(await api.copilot(text.trim()));
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      if (status === 503) setMsg({ tone: "held", text: "Policy copilot not available yet (ai/copilot.py)" });
      else if (status === 501) setMsg({ tone: "held", text: "Policy copilot endpoint not wired on this checkpoint yet (501)" });
      else setMsg({ tone: "bad", text: `Copilot failed: ${(e as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  async function apply() {
    if (!preview) return;
    setApplying(true);
    try {
      const saved = await api.putPolicy(preview);
      toast.success(`Policy applied · v${saved.version ?? "?"}`);
      setPreview(null);
      setText("");
      onApplied();
    } catch (e) {
      toast.error(`Apply failed: ${(e as Error).message}`);
    } finally {
      setApplying(false);
    }
  }

  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Sparkles className="size-4" /> Policy copilot · plain English → policy preview
        </CardTitle>
        <span className="text-xs text-dim">never auto-applied</span>
      </CardHeader>
      <CardContent className="flex flex-col gap-3">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          rows={3}
          placeholder='e.g. "support-bot may post to hooks.chat.example; block paste.example.org for everyone"'
          className="w-full resize-y rounded-lg border border-line bg-panel-2 p-3 font-mono text-sm text-fg placeholder:text-dim focus:border-info focus:outline-none"
        />
        <div className="flex flex-wrap items-center gap-2">
          <Button variant="outline" onClick={draft} disabled={busy || !text.trim()}>
            {busy ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Draft policy
          </Button>
          {preview && (
            <>
              <Button variant="ok" onClick={apply} disabled={applying}>
                {applying ? <LoaderCircle className="animate-spin" /> : <ShieldCheck />} Apply (PUT /policy)
              </Button>
              <Button variant="ghost" onClick={() => setPreview(null)}>
                Discard
              </Button>
            </>
          )}
          {msg && <span className={`text-sm ${msg.tone === "held" ? "text-held" : "text-bad"}`}>{msg.text}</span>}
        </div>
        {preview && (
          <div className="grid gap-4 lg:grid-cols-2">
            <div>
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-muted">
                Current <span className="font-mono text-fg">v{current.version}</span>
              </div>
              <PolicyView p={current} compact />
            </div>
            <div>
              <div className="mb-2 flex items-center gap-2 text-sm font-semibold text-info">
                Preview (not applied) {preview.mock ? <Badge variant="held">mock</Badge> : null}
                <span className="text-xs font-normal text-dim">ringed = new</span>
              </div>
              <PolicyView p={preview} base={current} compact />
            </div>
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function PolicyTab() {
  const { state, recordBacktest } = useTripwire();
  const { data: p, isError, error, isLoading, refetch } = usePolicy(state.policyVersion);
  const [bt, setBt] = useState(state.lastBacktest);
  const [running, setRunning] = useState(false);
  if (isError) return <Card className="p-4 text-sm text-held">GET /policy failed: {(error as Error).message}</Card>;
  if (isLoading || !p) return <Card className="p-4 text-sm text-dim">Loading policy…</Card>;

  async function backtest() {
    if (!p) return;
    setRunning(true);
    try {
      const r = await api.backtest(p);
      setBt(r);
      recordBacktest(r);
    } catch (e) {
      const status = e instanceof ApiError ? e.status : 0;
      toast.error(status === 501 ? "Backtest not implemented on this checkpoint yet (501)" : `Backtest failed: ${(e as Error).message}`);
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <div className="flex flex-wrap items-center gap-3 text-sm text-muted">
        <span>
          Policy version <span className="font-mono font-bold text-fg">v{p.version}</span>
        </span>
        {p.mock ? <Badge variant="held">mock</Badge> : null}
        <Button variant="outline" size="sm" className="ml-auto" onClick={backtest} disabled={running}>
          {running ? <LoaderCircle className="animate-spin" /> : <Database />} Backtest current policy
        </Button>
      </div>
      {(bt || running) && <BacktestCard bt={bt} title="Backtest · current policy over all of tripwire.events" />}
      <Copilot current={p} onApplied={() => void refetch()} />
      <PolicyView p={p} />
    </div>
  );
}

export default PolicyTab;
