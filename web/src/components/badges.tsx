// Status badges. Rule: never colour alone: every state ships an icon + a word, and a tooltip says
// what it means. Visual: soft-tint pills (state text on its soft fill, 1px tinted border), sentence
// case Geist 12px. Colour meaning is unchanged:
//   ok = allowed / benign / active · held = held / heightened / honeytoken / uncertain
//   bad = denied / malicious / quarantined · model = AkashML / quorum / OpenAI · info = rule / policy.
import type * as React from "react";
import {
  Brain,
  CircleCheck,
  CircleQuestionMark,
  CircleX,
  Eye,
  Hand,
  KeyRound,
  Lock,
  OctagonX,
  ScrollText,
  ShieldCheck,
  Users,
} from "lucide-react";
import { Badge, type BadgeVariant } from "./ui/badge";
import { Tooltip } from "./ui/tooltip";
import { cn } from "../lib/utils";
import { DASH, modeLabel } from "../lib/format";
import type { AgentMode } from "../lib/types";

/** "QUARANTINED" -> "Quarantined" (labels stay the same words, just not shouted). */
const sentence = (s: string) => (s ? s.charAt(0).toUpperCase() + s.slice(1).toLowerCase() : s);

/**
 * Status pill shared by every badge here (and AuditBadge): tint + icon + word. `tip` wraps it in the
 * ink tooltip. `caps` is kept for API compatibility (it now only sets the label weight).
 */
export function StatusPill({
  variant,
  icon,
  caps = false,
  tip,
  className,
  children,
  ...props
}: React.ComponentProps<"span"> & {
  variant: BadgeVariant;
  icon?: React.ReactNode;
  caps?: boolean;
  tip?: React.ReactNode;
}) {
  const pill = (
    <Badge
      variant={variant}
      className={cn(
        "gap-1 [&_svg]:size-3 [&_svg]:stroke-[2.1]",
        icon ? "pl-[7px] pr-2.5" : "px-2.5",
        caps && "font-medium",
        className,
      )}
      {...props}
    >
      {icon}
      {children}
    </Badge>
  );
  return tip ? <Tooltip content={tip}>{pill}</Tooltip> : pill;
}

export function ResultBadge({ result, reason }: { result: string; reason?: string }) {
  if (result === "ok")
    return (
      <StatusPill variant="ok" icon={<CircleCheck />} tip="Tool call allowed">
        Allowed
      </StatusPill>
    );
  if (result === "denied") {
    const held = reason?.startsWith("hold_");
    return (
      <StatusPill
        variant={held ? "held" : "bad"}
        icon={held ? <Hand /> : <OctagonX />}
        tip={
          held ? (
            <>
              Held, then denied <span className="font-mono text-muted">· {reason}</span>
            </>
          ) : (
            <>
              Tool call denied{reason ? <span className="font-mono text-muted"> · {reason}</span> : null}
            </>
          )
        }
      >
        {held ? "Held · denied" : "Denied"}
      </StatusPill>
    );
  }
  return (
    <StatusPill variant="muted" icon={<CircleX />} tip={`Result: ${result || "error"}`}>
      {sentence(result) || "Error"}
    </StatusPill>
  );
}

const MODE_TIP: Record<AgentMode, string> = {
  quarantined: "Quarantined: its tool calls are denied until restored",
  heightened: "Heightened: under closer watch",
  normal: "Active: normal operation",
};

export function ModeBadge({ mode, className }: { mode: AgentMode | undefined; className?: string }) {
  const label = sentence(modeLabel(mode));
  if (mode === "quarantined")
    return (
      <StatusPill variant="bad" icon={<Lock />} className={className} tip={MODE_TIP.quarantined}>
        {label}
      </StatusPill>
    );
  if (mode === "heightened")
    return (
      <StatusPill variant="held" icon={<Eye />} className={className} tip={MODE_TIP.heightened}>
        {label}
      </StatusPill>
    );
  return (
    <StatusPill variant="ok" icon={<ShieldCheck />} className={className} tip={MODE_TIP.normal}>
      {label}
    </StatusPill>
  );
}

const SOURCE: Record<string, { label: string; variant: BadgeVariant; icon: typeof Brain }> = {
  akashml: { label: "AkashML model", variant: "model", icon: Brain },
  quorum: { label: "2-model quorum", variant: "model", icon: Users },
  rule_only: { label: "Rule only", variant: "info", icon: ScrollText },
  honeytoken: { label: "Honeytoken", variant: "held", icon: KeyRound },
  policy: { label: "Policy", variant: "info", icon: ScrollText },
  // OpenAI fallback model (Sripadha's CCR, 11:20): labelled as its own provider, never as AkashML.
  openai: { label: "OpenAI model (fallback)", variant: "model", icon: Brain },
};

/** decision_source shown truthfully: a rule decision is never labelled as a model one. */
export function DecisionSourceBadge({ source }: { source: string | undefined }) {
  if (!source)
    return (
      <StatusPill variant="muted" tip="No decision_source recorded">
        {DASH}
      </StatusPill>
    );
  const s = SOURCE[source] ?? { label: source, variant: "muted" as BadgeVariant, icon: CircleQuestionMark };
  const Icon = s.icon;
  return (
    <StatusPill
      variant={s.variant}
      icon={<Icon />}
      tip={
        <>
          What decided it <span className="font-mono text-muted">· decision_source = {source}</span>
        </>
      }
    >
      {s.label}
    </StatusPill>
  );
}

export function VerdictBadge({ verdict, confidence }: { verdict?: string; confidence?: number }) {
  if (!verdict)
    return (
      <StatusPill variant="muted" tip="No verdict recorded yet">
        No verdict yet
      </StatusPill>
    );
  const pct = typeof confidence === "number" ? `${(confidence * 100).toFixed(0)}%` : null;
  const conf = pct ? <span className="font-mono text-[11px] font-medium tabular-nums opacity-75">{pct}</span> : null;
  const tip = (
    <>
      Verdict: {verdict}
      {pct ? <span className="font-mono text-muted"> · confidence {pct}</span> : null}
    </>
  );
  if (verdict === "malicious")
    return (
      <StatusPill variant="bad" icon={<OctagonX />} tip={tip}>
        Malicious {conf}
      </StatusPill>
    );
  if (verdict === "benign")
    return (
      <StatusPill variant="ok" icon={<CircleCheck />} tip={tip}>
        Benign {conf}
      </StatusPill>
    );
  return (
    <StatusPill variant="held" icon={<CircleQuestionMark />} tip={tip}>
      Uncertain {conf}
    </StatusPill>
  );
}

export function ReasonText({ reason }: { reason?: string }) {
  if (!reason) return <span className="text-dim">{DASH}</span>;
  const tone = reason.startsWith("hold_") || reason === "honeytoken" ? "text-held" : "text-bad";
  return <span className={`font-mono text-xs ${tone}`}>{reason}</span>;
}
