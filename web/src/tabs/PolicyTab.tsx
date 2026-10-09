import { useState, type KeyboardEvent, type ReactNode } from "react";
import {
  Ban,
  Database,
  Globe,
  Hand,
  LoaderCircle,
  Lock,
  Server,
  ShieldCheck,
  Sparkles,
  TriangleAlert,
} from "lucide-react";
import { toast } from "sonner";
import { Badge } from "../components/ui/badge";
import { Button, ButtonArrow } from "../components/ui/button";
import { Tooltip } from "../components/ui/tooltip";
import { Chip, CopyId, GlowCard, Kbd, Reveal, SectionHeader, Skeleton, type ChipTone } from "../components/fx";
import { BacktestHero } from "../components/analytics/BacktestHero";
import { api, ApiError } from "../lib/api";
import { cn } from "../lib/utils";
import type { Policy } from "../lib/types";
import { usePolicy, useTripwire } from "../hooks/useTripwire";

type StateTone = Extract<ChipTone, "ok" | "held" | "bad" | "info" | "neutral">;

const TILE: Record<StateTone, string> = {
  ok: "tint-ok",
  held: "tint-held",
  bad: "tint-bad",
  info: "tint-info",
  neutral: "tint-neutral",
};

/** Tidy chip group. Chips pop in once on mount, staggered; items new in a copilot preview get a ring. */
function Chips({
  items,
  tone,
  diff,
  empty = "None in this version",
}: {
  items?: string[];
  tone: StateTone;
  diff?: Set<string>;
  empty?: string;
}) {
  if (!items?.length) return <span className="text-[13px] text-dim">{empty}</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((x, i) => {
        const isNew = diff?.has(x);
        const chip = (
          <Chip
            key={x}
            tone={tone}
            mono
            size="md"
            tabIndex={isNew ? 0 : undefined}
            className={cn(
              "animate-pop-in transition-[transform,box-shadow] duration-200 motion-safe:hover:-translate-y-px hover:shadow-sm",
              isNew && "ring-2 ring-info ring-offset-2 ring-offset-panel",
            )}
            style={{ animationDelay: `${Math.min(i * 30, 360)}ms` }}
          >
            {x}
          </Chip>
        );
        return isNew ? (
          <Tooltip key={x} content="New in the copilot preview">
            {chip}
          </Tooltip>
        ) : (
          chip
        );
      })}
    </div>
  );
}

/** Count pill next to a section title. */
function CountPill({ n, label }: { n: number; label: string }) {
  return (
    <Tooltip content={`${n} ${label}`}>
      <span
        tabIndex={0}
        className="inline-flex h-[22px] min-w-[22px] items-center justify-center rounded-full border border-line bg-panel-2 px-1.5 font-mono text-[12px] font-medium text-muted tabular-nums outline-none focus-visible:ring-2 focus-visible:ring-brand"
      >
        {n}
      </span>
    </Tooltip>
  );
}

function Section({
  title,
  icon,
  tone,
  hint,
  count,
  countLabel = "entries",
  compact,
  reveal,
  className,
  bodyClassName,
  footer,
  children,
}: {
  title: string;
  icon: ReactNode;
  tone: StateTone;
  hint?: string;
  count?: number;
  countLabel?: string;
  compact?: boolean;
  reveal?: number;
  className?: string;
  bodyClassName?: string;
  footer?: ReactNode;
  children: ReactNode;
}) {
  if (compact) {
    return (
      <div className={cn("surface-raised flex flex-col gap-2.5 p-3.5", className)}>
        <div className="flex items-center gap-2 text-[13px] font-semibold text-fg [&_svg]:size-3.5 [&_svg]:text-dim">
          {icon}
          <span className="min-w-0 flex-1 truncate">{title}</span>
          {count !== undefined && <span className="font-mono text-[12px] font-medium text-dim">{count}</span>}
        </div>
        {children}
      </div>
    );
  }
  return (
    <GlowCard
      reveal={reveal}
      className={className}
      title={
        <span className="flex items-center gap-2.5">
          <span
            className={cn(
              "inline-flex size-7 shrink-0 items-center justify-center rounded-lg border [&_svg]:size-4 [&_svg]:stroke-[1.75]",
              TILE[tone],
            )}
          >
            {icon}
          </span>
          <span>{title}</span>
        </span>
      }
      description={hint}
      actions={count !== undefined ? <CountPill n={count} label={countLabel} /> : null}
      footer={footer}
      bodyClassName={bodyClassName}
    >
      {children}
    </GlowCard>
  );
}

/** Items in `next` that are not in `prev` (for highlighting a preview). */
function added(prev: string[] | undefined, next: string[] | undefined): Set<string> {
  const p = new Set(prev ?? []);
  return new Set((next ?? []).filter((x) => !p.has(x)));
}

function Allowlists({ p, base, compact }: { p: Policy; base?: Policy; compact?: boolean }) {
  const allow = Object.entries(p.allowlists ?? {});
  if (allow.length === 0) {
    return compact ? (
      <span className="text-[13px] text-dim">None in this version</span>
    ) : (
      <div className="flex min-h-[120px] flex-1 flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-line-strong bg-panel-2/60 px-4 text-center text-[13px] text-dim">
        <ShieldCheck className="size-5 text-line-strong" strokeWidth={1.75} />
        No per-agent allowlists in this version.
      </div>
    );
  }
  return (
    <div className={cn("flex flex-col", compact ? "gap-2.5" : "-mx-5")}>
      {allow.map(([agent, hosts], i) => (
        <div
          key={agent}
          className={cn(
            "flex flex-col gap-2",
            !compact && "px-5 py-3.5 transition-colors duration-150 hover:bg-panel-3/60",
            !compact && i > 0 && "border-t border-line",
          )}
        >
          <div className="flex items-center gap-2">
            <CopyId value={agent} className="text-[13px] font-semibold" />
            <span className="font-mono text-[12px] text-dim tabular-nums">
              {hosts.length} {hosts.length === 1 ? "host" : "hosts"}
            </span>
          </div>
          <Chips items={hosts} tone="ok" diff={base ? added(base.allowlists?.[agent], hosts) : undefined} />
        </div>
      ))}
    </div>
  );
}

function PolicyView({ p, base, compact = false }: { p: Policy; base?: Policy; compact?: boolean }) {
  const allowCount = Object.keys(p.allowlists ?? {}).length;
  const d = (prev: string[] | undefined, next: string[] | undefined) => (base ? added(prev, next) : undefined);
  const sections = {
    allow: (
      <Section
        compact={compact}
        reveal={3}
        tone="ok"
        icon={<ShieldCheck />}
        title="Allowlists"
        hint="Destinations each agent may post to without a hold."
        count={allowCount}
        countLabel={allowCount === 1 ? "agent with an allowlist" : "agents with an allowlist"}
        className={compact ? undefined : "h-full"}
        bodyClassName="flex flex-col"
      >
        <Allowlists p={p} base={base} compact={compact} />
      </Section>
    ),
    deny: (
      <Section
        compact={compact}
        reveal={4}
        tone="bad"
        icon={<Ban />}
        title="Fleet denylist"
        hint="Known-bad destinations (outbreak pushes here)."
        count={p.denylist?.length ?? 0}
        countLabel="denied destinations"
      >
        <Chips items={p.denylist} tone="bad" diff={d(base?.denylist, p.denylist)} />
      </Section>
    ),
    internal: (
      <Section
        compact={compact}
        reveal={5}
        tone="info"
        icon={<Server />}
        title="Internal hosts"
        hint="is_external = 0"
        count={p.internal_hosts?.length ?? 0}
        countLabel="internal hosts"
        footer={
          compact ? undefined : (
            <div className="flex items-center gap-2 border-t border-line pt-3.5 text-[12px] text-muted">
              <Globe className="size-3.5 shrink-0 text-dim" strokeWidth={1.75} />
              Any host not listed as internal is treated as external.
            </div>
          )
        }
      >
        <Chips items={p.internal_hosts} tone="info" diff={d(base?.internal_hosts, p.internal_hosts)} />
      </Section>
    ),
    risk: (
      <Section
        compact={compact}
        reveal={6}
        tone="held"
        icon={<Hand />}
        title="High-risk actions"
        hint="Decided synchronously in hold mode."
        count={p.high_risk_actions?.length ?? 0}
        countLabel="high-risk actions"
        className={compact ? undefined : "h-full"}
      >
        <Chips items={p.high_risk_actions} tone="held" diff={d(base?.high_risk_actions, p.high_risk_actions)} />
      </Section>
    ),
    fixed: (
      <Section
        compact={compact}
        reveal={7}
        tone="bad"
        icon={<Lock />}
        title="Always denied"
        hint="Fixed policy, no model call."
        count={p.fixed_deny_actions?.length ?? 0}
        countLabel="always-denied actions"
        className={compact ? undefined : "h-full"}
      >
        <Chips items={p.fixed_deny_actions} tone="bad" diff={d(base?.fixed_deny_actions, p.fixed_deny_actions)} />
      </Section>
    ),
    untrusted: (
      <Section
        compact={compact}
        reveal={8}
        tone="held"
        icon={<TriangleAlert />}
        title="Untrusted input prefixes"
        hint="Used for outbreak tracing."
        count={p.untrusted_sources?.length ?? 0}
        countLabel="untrusted prefixes"
        className={compact ? undefined : "h-full"}
      >
        <Chips items={p.untrusted_sources} tone="held" diff={d(base?.untrusted_sources, p.untrusted_sources)} />
      </Section>
    ),
  };

  if (compact) {
    return (
      <div className="flex flex-col gap-2.5">
        {sections.allow}
        {sections.deny}
        {sections.risk}
        {sections.fixed}
        {sections.untrusted}
        {sections.internal}
      </div>
    );
  }
  return (
    <div className="grid grid-cols-12 gap-4">
      <div className="col-span-12 lg:col-span-7">{sections.allow}</div>
      <div className="col-span-12 flex flex-col gap-4 lg:col-span-5">
        {sections.deny}
        {sections.internal}
      </div>
      <div className="col-span-12 md:col-span-4">{sections.risk}</div>
      <div className="col-span-12 md:col-span-4">{sections.fixed}</div>
      <div className="col-span-12 md:col-span-4">{sections.untrusted}</div>
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
    if (!text.trim() || busy) return;
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

  function onKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    // Only an explicit Cmd/Ctrl+Enter drafts; nothing is ever applied from the keyboard.
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault();
      void draft();
    }
  }

  return (
    <GlowCard
      tone="model"
      glow="soft"
      reveal={2}
      aria-busy={busy}
      eyebrow={
        <>
          <Sparkles className="text-model" /> Policy copilot · plain English → policy preview
        </>
      }
      title="Describe a change in plain English"
      description="A draft appears next to the current policy. Nothing changes until you press Apply."
      actions={
        <Chip tone="neutral" mono>
          never auto-applied
        </Chip>
      }
      bodyClassName="flex flex-col gap-3"
    >
      <div className="relative">
        <textarea
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={onKeyDown}
          rows={3}
          aria-label="Describe the policy change in plain English"
          placeholder='e.g. "support-bot may post to hooks.chat.example; block paste.example.org for everyone"'
          className={cn(
            "block w-full resize-y rounded-[var(--radius-control)] border border-line bg-panel-2 px-4 py-3 text-sm leading-6 text-fg",
            "shadow-[inset_0_1px_2px_rgb(21_22_26/0.04)] transition-[border-color,box-shadow,background-color] duration-200 placeholder:text-dim",
            "hover:border-line-strong focus:border-model-line focus:bg-panel focus:shadow-[0_0_0_3px_var(--color-model-soft)] focus:outline-none",
          )}
        />
        {busy && (
          <div aria-hidden className="pointer-events-none absolute inset-x-3 bottom-0 h-px overflow-hidden">
            <span className="absolute inset-y-0 left-0 w-1/3 animate-scan bg-model" />
          </div>
        )}
      </div>
      <div className="flex flex-wrap items-center gap-2">
        <Button variant="outline" onClick={draft} disabled={busy || !text.trim()}>
          {busy ? <LoaderCircle className="animate-spin" /> : <Sparkles />} Draft policy
          <Kbd className="ml-1.5">⌘↵</Kbd>
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
        {msg && (
          <span
            role="status"
            className={cn(
              "inline-flex items-center gap-1.5 rounded-full border px-3 py-1.5 text-[13px] animate-pop-in",
              msg.tone === "held" ? "tint-held" : "tint-bad",
            )}
          >
            <TriangleAlert className="size-3.5 shrink-0" strokeWidth={1.75} /> {msg.text}
          </span>
        )}
      </div>
      {preview && (
        <div className="mt-2 grid gap-4 border-t border-line pt-4 lg:grid-cols-2">
          <Reveal index={0} trigger="mount">
            <div className="mb-2.5 flex items-center gap-2">
              <span className="eyebrow">Current</span>
              <Chip tone="paper" mono>
                v{current.version}
              </Chip>
            </div>
            <PolicyView p={current} compact />
          </Reveal>
          <Reveal index={2} trigger="mount">
            <div className="mb-2.5 flex flex-wrap items-center gap-2">
              <span className="eyebrow text-info">Preview · not applied</span>
              {preview.mock ? <Badge variant="held">mock</Badge> : null}
              <span className="ml-auto flex items-center gap-1.5 text-[12px] text-dim">
                <span aria-hidden className="size-2.5 rounded-full ring-2 ring-info ring-offset-1 ring-offset-panel" />
                ringed = new
              </span>
            </div>
            <PolicyView p={preview} base={current} compact />
          </Reveal>
        </div>
      )}
    </GlowCard>
  );
}

function VersionPill({ p }: { p: Policy }) {
  return (
    <Tooltip content="Current policy version (GET /policy)">
      <span
        tabIndex={0}
        className="inline-flex h-9 items-center gap-2 rounded-full border border-line bg-panel pr-1.5 pl-3 shadow-sm outline-none focus-visible:ring-2 focus-visible:ring-brand"
      >
        <span className="text-[12px] font-medium text-dim">Version</span>
        <span
          key={p.version}
          className="inline-flex h-6 items-center rounded-full bg-fg px-2.5 font-mono text-[13px] font-semibold text-white tabular-nums animate-pop-in"
        >
          v{p.version}
        </span>
      </span>
    </Tooltip>
  );
}

function Header({ p, actions }: { p?: Policy; actions?: ReactNode }) {
  return (
    <SectionHeader
      eyebrow="Policy · copilot + backtest"
      eyebrowTone="model"
      pre="Policy"
      em="as code"
      description="Allowlists, denylists and high-risk actions enforced on every agent tool call. Backtest the current version over everything in tripwire.events before you trust it."
      actions={
        p ? (
          <>
            {p.mock ? <Badge variant="held">mock</Badge> : null}
            <VersionPill p={p} />
            {actions}
          </>
        ) : null
      }
    />
  );
}

function PolicySkeleton() {
  return (
    <div role="status" aria-label="Loading policy" className="grid grid-cols-12 gap-4">
      <Skeleton className="col-span-12 h-48 rounded-[var(--radius-card)]" />
      <Skeleton className="col-span-12 h-40 rounded-[var(--radius-card)] lg:col-span-7" />
      <Skeleton className="col-span-12 h-40 rounded-[var(--radius-card)] lg:col-span-5" />
      <Skeleton className="col-span-12 h-32 rounded-[var(--radius-card)] md:col-span-4" />
      <Skeleton className="col-span-12 h-32 rounded-[var(--radius-card)] md:col-span-4" />
      <Skeleton className="col-span-12 h-32 rounded-[var(--radius-card)] md:col-span-4" />
    </div>
  );
}

export function PolicyTab() {
  const { state, recordBacktest } = useTripwire();
  const { data: p, isError, error, isLoading, refetch } = usePolicy(state.policyVersion);
  const [bt, setBt] = useState(state.lastBacktest);
  const [running, setRunning] = useState(false);
  if (isError)
    return (
      <div className="flex flex-col gap-6">
        <Header />
        <div className="rounded-[var(--radius-card)] border border-held-line bg-held-soft px-5 py-4 text-[13px] text-held">
          GET /policy failed: {(error as Error).message}
        </div>
      </div>
    );
  if (isLoading || !p)
    return (
      <div className="flex flex-col gap-6">
        <Header />
        <PolicySkeleton />
      </div>
    );

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
    <div className="flex flex-col gap-6">
      <Header
        p={p}
        actions={
          <Button onClick={backtest} disabled={running}>
            {running ? <LoaderCircle className="animate-spin" /> : <Database />} Backtest current policy
            <ButtonArrow />
          </Button>
        }
      />
      <BacktestHero
        bt={bt}
        running={running}
        title="Current policy over all of tripwire.events"
        emptyHint="No backtest in this session yet."
      />
      <Copilot current={p} onApplied={() => void refetch()} />
      <PolicyView p={p} />
    </div>
  );
}

export default PolicyTab;
