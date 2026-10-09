import { Ban, Globe, Hand, Lock, Server, ShieldCheck, TriangleAlert } from "lucide-react";
import type { ReactNode } from "react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge, type BadgeVariant } from "../components/ui/badge";
import { usePolicy, useTripwire } from "../hooks/useTripwire";

function Chips({ items, variant, icon }: { items?: string[]; variant: BadgeVariant; icon: ReactNode }) {
  if (!items?.length) return <span className="text-sm text-dim">—</span>;
  return (
    <div className="flex flex-wrap gap-1.5">
      {items.map((x) => (
        <Badge key={x} variant={variant} className="px-2 py-1 font-mono text-xs">
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

export function PolicyTab() {
  const { state } = useTripwire();
  const { data: p, isError, error, isLoading } = usePolicy(state.policyVersion);
  if (isError) return <Card className="p-4 text-sm text-held">GET /policy failed: {(error as Error).message}</Card>;
  if (isLoading || !p) return <Card className="p-4 text-sm text-dim">Loading policy…</Card>;
  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center gap-2 text-sm text-muted">
        Policy version <span className="font-mono font-bold text-fg">v{p.version}</span>
        {p.mock ? <Badge variant="held">mock</Badge> : null}
      </div>
      <Section title="Allowlists · destinations each agent may post to without a hold">
        <div className="flex flex-col gap-3">
          {Object.entries(p.allowlists ?? {}).length === 0 && <span className="text-sm text-dim">—</span>}
          {Object.entries(p.allowlists ?? {}).map(([agent, hosts]) => (
            <div key={agent} className="flex flex-wrap items-center gap-3">
              <span className="w-36 font-mono text-sm font-semibold">{agent}</span>
              <Chips items={hosts} variant="ok" icon={<ShieldCheck />} />
            </div>
          ))}
        </div>
      </Section>
      <div className="grid gap-4 md:grid-cols-2">
        <Section title="Fleet denylist" hint="known-bad destinations (outbreak pushes here)">
          <Chips items={p.denylist} variant="bad" icon={<Ban />} />
        </Section>
        <Section title="High-risk actions" hint="decided synchronously in hold mode">
          <Chips items={p.high_risk_actions} variant="held" icon={<Hand />} />
        </Section>
        <Section title="Always denied" hint="fixed policy, no model call">
          <Chips items={p.fixed_deny_actions} variant="bad" icon={<Lock />} />
        </Section>
        <Section title="Untrusted input prefixes" hint="used for outbreak tracing">
          <Chips items={p.untrusted_sources} variant="held" icon={<TriangleAlert />} />
        </Section>
        <Section title="Internal hosts" hint="is_external = 0">
          <Chips items={p.internal_hosts} variant="info" icon={<Server />} />
        </Section>
        <Section title="External by default">
          <div className="flex items-center gap-2 text-sm text-muted">
            <Globe className="size-4" /> Any host not listed as internal is treated as external.
          </div>
        </Section>
      </div>
    </div>
  );
}
