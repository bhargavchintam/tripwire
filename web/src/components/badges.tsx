// Status badges. Rule: never colour alone — every state ships an icon + text.
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
import { modeLabel } from "../lib/format";
import type { AgentMode } from "../lib/types";

export function ResultBadge({ result, reason }: { result: string; reason?: string }) {
  if (result === "ok")
    return (
      <Badge variant="ok">
        <CircleCheck /> ALLOWED
      </Badge>
    );
  if (result === "denied") {
    const held = reason?.startsWith("hold_");
    return (
      <Badge variant={held ? "held" : "bad"}>
        {held ? <Hand /> : <OctagonX />} {held ? "HELD · DENIED" : "DENIED"}
      </Badge>
    );
  }
  return (
    <Badge variant="muted">
      <CircleX /> {result.toUpperCase() || "ERROR"}
    </Badge>
  );
}

export function ModeBadge({ mode, className }: { mode: AgentMode | undefined; className?: string }) {
  if (mode === "quarantined")
    return (
      <Badge variant="bad" className={className}>
        <Lock /> {modeLabel(mode)}
      </Badge>
    );
  if (mode === "heightened")
    return (
      <Badge variant="held" className={className}>
        <Eye /> {modeLabel(mode)}
      </Badge>
    );
  return (
    <Badge variant="ok" className={className}>
      <ShieldCheck /> {modeLabel(mode)}
    </Badge>
  );
}

const SOURCE: Record<string, { label: string; variant: BadgeVariant; icon: typeof Brain }> = {
  akashml: { label: "AkashML model", variant: "model", icon: Brain },
  quorum: { label: "2-model quorum", variant: "model", icon: Users },
  rule_only: { label: "rule only", variant: "info", icon: ScrollText },
  honeytoken: { label: "honeytoken", variant: "held", icon: KeyRound },
  policy: { label: "policy", variant: "info", icon: ScrollText },
  // OpenAI fallback model (Sripadha's CCR, 11:20) — labelled as its own provider, never as AkashML.
  openai: { label: "OpenAI model (fallback)", variant: "model", icon: Brain },
};

/** decision_source shown truthfully: a rule decision is never labelled as a model one. */
export function DecisionSourceBadge({ source }: { source: string | undefined }) {
  if (!source) return <Badge variant="muted">—</Badge>;
  const s = SOURCE[source] ?? { label: source, variant: "muted" as BadgeVariant, icon: CircleQuestionMark };
  const Icon = s.icon;
  return (
    <Badge variant={s.variant} title={`decision_source = ${source}`}>
      <Icon /> {s.label}
    </Badge>
  );
}

export function VerdictBadge({ verdict, confidence }: { verdict?: string; confidence?: number }) {
  if (!verdict) return <Badge variant="muted">no verdict yet</Badge>;
  const conf = typeof confidence === "number" ? ` ${(confidence * 100).toFixed(0)}%` : "";
  if (verdict === "malicious")
    return (
      <Badge variant="bad">
        <OctagonX /> MALICIOUS{conf}
      </Badge>
    );
  if (verdict === "benign")
    return (
      <Badge variant="ok">
        <CircleCheck /> BENIGN{conf}
      </Badge>
    );
  return (
    <Badge variant="held">
      <CircleQuestionMark /> UNCERTAIN{conf}
    </Badge>
  );
}

export function ReasonText({ reason }: { reason?: string }) {
  if (!reason) return <span className="text-dim">—</span>;
  const tone = reason.startsWith("hold_") || reason === "honeytoken" ? "text-held" : "text-bad";
  return <span className={`font-mono text-xs ${tone}`}>{reason}</span>;
}
