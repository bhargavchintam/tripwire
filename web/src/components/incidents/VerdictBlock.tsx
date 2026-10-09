import type { ReactNode } from "react";
import { Brain, Tag } from "lucide-react";
import { Tooltip } from "../ui/tooltip";
import { DecisionSourceBadge, VerdictBadge } from "../badges";
import { Chip, Count, toneBorder, toneText, type Tone } from "../fx";
import { fmtInt, isNum } from "../../lib/format";
import { cn } from "../../lib/utils";
import type { Incident, QuorumVote } from "../../lib/types";

const VERDICT_TONE: Record<string, Tone> = { malicious: "bad", benign: "ok", uncertain: "held" };

export function verdictTone(v: string | undefined): Tone {
  return v ? (VERDICT_TONE[v] ?? "held") : "neutral";
}

/** Same precision rule as fmtMs (< 10 ms keeps one decimal). */
const msDigits = (v: number | null | undefined) => (isNum(v) && v < 10 ? 1 : 0);

/**
 * One chip per model id with that model's vote (from the SSE quorum if present, else the incident
 * verdict). Same rule as the live feed's QuorumChips; nothing is inferred beyond it.
 */
function ModelChips({ inc, votes }: { inc: Incident; votes?: QuorumVote[] }) {
  const ids = inc.verdict?.model_ids ?? [];
  if (ids.length === 0 && !votes?.length) return null;
  const byId = new Map((votes ?? []).map((v) => [v.model_id, v]));
  const list: QuorumVote[] = ids.length
    ? ids.map((m) => byId.get(m) ?? { model_id: m, verdict: inc.verdict?.verdict, confidence: undefined })
    : (votes ?? []);
  return (
    <div className="mt-3 flex flex-wrap gap-1.5">
      {list.map((v) => (
        <Tooltip key={v.model_id} content={byId.has(v.model_id) ? `Model ${v.model_id} voted ${v.verdict ?? "—"}` : `Model ${v.model_id} · incident verdict: ${v.verdict ?? "—"}`}>
          <Chip tone="model" mono size="md" icon={<Brain strokeWidth={1.75} />} className="h-auto min-h-7 max-w-full py-1">
            <span className="truncate">{v.model_id}</span>
            {v.verdict ? (
              <span className={cn("shrink-0", toneText[verdictTone(String(v.verdict))])}>
                · {String(v.verdict)}
                {typeof v.confidence === "number" ? ` ${(v.confidence * 100).toFixed(0)}%` : ""}
              </span>
            ) : null}
          </Chip>
        </Tooltip>
      ))}
    </div>
  );
}

function Stat({ label, children, caption }: { label: string; children: ReactNode; caption?: ReactNode }) {
  return (
    <div className="min-w-0">
      <dt className="text-xs text-dim">{label}</dt>
      <dd className="mt-1 whitespace-nowrap text-[28px] font-semibold leading-none tracking-[-0.03em] tabular-nums text-fg">
        {children}
      </dd>
      {caption ? <dd className="mt-1.5 font-mono text-xs text-dim">{caption}</dd> : null}
    </div>
  );
}

/**
 * Verdict block of the incident sheet: verdict + what decided it, the model chips, the model's own
 * reason, OWASP tags; confidence and latency as display numbers on the right. All from inc.verdict.
 */
export function VerdictBlock({ inc, votes }: { inc: Incident; votes?: QuorumVote[] }) {
  const v = inc.verdict;
  const tone = verdictTone(v?.verdict);
  const confidence = v?.confidence;
  const conf = isNum(confidence) ? confidence * 100 : null;
  const tIn = v?.tokens_in;
  const tOut = v?.tokens_out;
  const tokens = isNum(tIn) && isNum(tOut) ? `${fmtInt(tIn)} in · ${fmtInt(tOut)} out tokens` : null;
  return (
    <div className="grid gap-6 sm:grid-cols-[minmax(0,1fr)_auto]">
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-2">
          <VerdictBadge verdict={v?.verdict} />
          <DecisionSourceBadge source={v?.decision_source} />
        </div>
        <ModelChips inc={inc} votes={votes} />
        {v?.reason ? (
          <p className={cn("mt-4 border-l-2 pl-3.5 text-sm leading-[1.6] text-fg/85", toneBorder[tone])}>{v.reason}</p>
        ) : null}
        {inc.tags?.length ? (
          <div className="mt-4 flex flex-wrap gap-1.5">
            {inc.tags.map((t) => (
              <Chip key={t} tone="neutral" icon={<Tag strokeWidth={1.75} />}>
                {t}
              </Chip>
            ))}
          </div>
        ) : null}
      </div>
      <dl className="flex gap-8 sm:flex-col sm:gap-5 sm:border-l sm:border-line sm:pl-6">
        <Stat label="Confidence">
          <Count value={conf} suffix="%" />
        </Stat>
        <Stat label="Verdict latency" caption={tokens}>
          <Count value={v?.latency_ms} digits={msDigits(v?.latency_ms)} />
          {isNum(v?.latency_ms) ? <span className="ml-1 text-sm font-medium tracking-normal text-muted">ms</span> : null}
        </Stat>
      </dl>
    </div>
  );
}
