import { Binary, FileKey, Hand, Lock, OctagonX, Send, Workflow } from "lucide-react";
import type { ReactNode } from "react";
import { motion } from "motion/react";
import { CopyId, GlowCard, SPRING, toneSoftVar, toneTint, toneVar, type Tone } from "./fx";
import { Tooltip } from "./ui/tooltip";
import { isHoldReason } from "./live/streamChange";
import { fmtClock } from "../lib/format";
import { cn } from "../lib/utils";
import type { AgentMode, Incident, IncidentStep } from "../lib/types";

const SECRET_RE = /\.env|secret|credential|passw|token|\.pem|id_rsa|\.aws/i;
const ENCODE_RE = /base64|xxd|openssl|gzip|encode|uuencode/i;

/** Which stages of the chain the latest incident's recorded steps reached (display only). */
function stagesFor(inc: Incident | undefined, mode: AgentMode | undefined) {
  const steps = inc?.steps ?? [];
  const read = steps.find((s) => s.action === "read_file" && SECRET_RE.test(s.target));
  const encode = steps.find((s) => s.action === "run_command" && ENCODE_RE.test(s.target));
  const send = steps.find((s) => s.action === "http_post");
  const stopped = steps.find((s) => s.action === "http_post" && s.result === "denied");
  const quarantined = !!inc && (inc.contained_ms != null || mode === "quarantined");
  return { read, encode, send, stopped, quarantined };
}

interface StageDef {
  key: string;
  on: boolean;
  tone: Extract<Tone, "bad" | "held">;
  icon: ReactNode;
  label: string;
  sub?: string;
  step?: IncidentStep;
}

const DRAW_FROM = "inset(0% 100% 100% 0%)";
const DRAW_TO = "inset(0% 0% 0% 0%)";

function StageNode({ s, n, next, chainKey, last }: { s: StageDef; n: number; next?: StageDef; chainKey: string; last: boolean }) {
  const color = toneVar[s.tone];
  const detail = s.on ? (s.sub ?? "seen") : "not seen";
  return (
    <li className="relative flex min-w-0 flex-1 gap-3.5 pb-5 last:pb-0 @lg:flex-col @lg:items-center @lg:gap-0 @lg:pb-0 @lg:text-center">
      {/* connector to the next stage: dashed track; draws in solid once the chain really reaches it */}
      {!last && (
        <span
          aria-hidden
          className="absolute top-11 bottom-1 left-[calc(1.125rem-1px)] w-0.5 @lg:top-[calc(1.125rem-1px)] @lg:right-[calc(-50%+1.125rem+8px)] @lg:bottom-auto @lg:left-[calc(50%+1.125rem+8px)] @lg:h-0.5 @lg:w-auto"
        >
          <span className="absolute inset-0 m-auto h-full w-px border-l border-dashed border-line-strong @lg:h-px @lg:w-full @lg:border-t @lg:border-l-0" />
          {next?.on && (
            <motion.span
              key={`${chainKey}-${next.key}`}
              className="absolute inset-0 rounded-full"
              style={{ background: `color-mix(in oklab, ${toneVar[next.tone]} 70%, transparent)` }}
              initial={{ clipPath: DRAW_FROM }}
              animate={{ clipPath: DRAW_TO }}
              transition={{ duration: 0.55, ease: [0.22, 1, 0.36, 1], delay: 0.12 + 0.12 * n }}
            />
          )}
        </span>
      )}

      <motion.div
        key={`${chainKey}-${s.key}-${s.on ? "on" : "off"}`}
        initial={s.on ? { scale: 0.7, opacity: 0 } : false}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ ...SPRING, delay: 0.12 * n }}
        className={cn(
          "relative z-10 grid size-9 shrink-0 place-items-center rounded-full border transition-colors duration-500 [&_svg]:size-4",
          s.on ? toneTint[s.tone] : "border-dashed border-line-strong bg-panel text-dim",
        )}
        style={s.on ? { boxShadow: `0 0 0 4px ${toneSoftVar[s.tone]}, var(--shadow-sm)` } : undefined}
      >
        {s.icon}
      </motion.div>

      <div className="flex min-w-0 flex-col gap-0.5 pt-1 @lg:mt-3 @lg:w-full @lg:items-center @lg:px-1.5 @lg:pt-0">
        <span className={cn("text-[14px] font-semibold leading-tight tracking-[-0.01em]", s.on ? "text-fg" : "text-dim")}>
          <span className="mr-1.5 font-mono text-[11px] font-medium text-dim">{String(n + 1).padStart(2, "0")}</span>
          {s.label}
        </span>
        <span className="max-w-full truncate font-mono text-[12px]" style={{ color: s.on ? color : "var(--color-dim)" }} title={detail}>
          {detail}
        </span>
        {s.on && s.step && (
          <Tooltip content={<span className="font-mono">{fmtClock(s.step.ts_ms)} · {s.step.target}</span>}>
            <span className="max-w-full cursor-default truncate font-mono text-[12px] text-muted">{s.step.target}</span>
          </Tooltip>
        )}
      </div>
    </li>
  );
}

export function AttackChain({ incident, mode }: { incident: Incident | undefined; mode: AgentMode | undefined }) {
  const s = stagesFor(incident, mode);
  const held = !!s.stopped && isHoldReason(s.stopped.reason);
  const stages: StageDef[] = [
    { key: "read", on: !!s.read, tone: "bad", icon: <FileKey />, label: "Secret read", sub: "read_file", step: s.read },
    { key: "encode", on: !!s.encode, tone: "bad", icon: <Binary />, label: "Encode", sub: "run_command", step: s.encode },
    { key: "send", on: !!s.send, tone: "bad", icon: <Send />, label: "External send", sub: "http_post", step: s.send },
    {
      key: "stop",
      on: !!s.stopped,
      tone: held ? "held" : "bad",
      icon: held || !s.stopped ? <Hand /> : <OctagonX />,
      label: "Held / denied",
      sub: s.stopped ? `denied · ${s.stopped.reason || "—"}` : undefined,
      step: s.stopped,
    },
    {
      key: "quarantine",
      on: s.quarantined,
      tone: "bad",
      icon: <Lock />,
      label: "Quarantined",
      sub: incident?.closed_ms ? "contained · since restored" : "agent blocked",
    },
  ];
  const reached = stages.filter((x) => x.on).length;
  const tone: Tone = s.quarantined ? "bad" : s.stopped ? (held ? "held" : "bad") : reached ? "bad" : "neutral";
  const chainKey = incident?.id ?? "none";

  return (
    <GlowCard
      tone={tone}
      glow="soft"
      reveal={9}
      eyebrow={
        <>
          <Workflow /> Attack chain · latest incident
        </>
      }
      title={
        incident ? (
          <span className="inline-flex items-baseline gap-2">
            <span className="num-display text-[20px]">
              {reached}
              <span className="text-dim"> / {stages.length}</span>
            </span>
            <span className="text-[14px] font-medium text-muted">stages reached</span>
          </span>
        ) : (
          <span className="text-[14px] font-normal text-dim">Waiting for an incident</span>
        )
      }
      actions={
        incident ? (
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-3 gap-y-1">
            <span className="inline-flex items-center gap-1.5 text-[12px] text-dim">
              agent <CopyId value={incident.agent_id} />
            </span>
            <span className="inline-flex items-center gap-1.5 text-[12px] text-dim">
              incident <CopyId value={incident.id} />
            </span>
          </div>
        ) : undefined
      }
      bodyClassName="@container pt-3"
    >
      <ol className="flex flex-col @lg:flex-row @lg:items-start">
        {stages.map((st, i) => (
          <StageNode
            key={st.key}
            s={st}
            n={i}
            next={stages[i + 1]}
            chainKey={chainKey}
            last={i === stages.length - 1}
          />
        ))}
      </ol>
    </GlowCard>
  );
}
