import { Binary, FileKey, Hand, Lock, OctagonX, Send, Workflow } from "lucide-react";
import { useLayoutEffect, useMemo, useRef, useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Chip, CopyId, GlowCard, SPRING, toneSoftVar, toneTint, toneVar, type Tone } from "./fx";
import { Tooltip } from "./ui/tooltip";
import { isHoldReason } from "./live/streamChange";
import { isEncode, isSecretRead, LIVE_WINDOW_MS, useLiveChain, type LiveChain } from "./live/useLiveChain";
import { useTripwire } from "../hooks/useTripwire";
import { fmtClock } from "../lib/format";
import { cn } from "../lib/utils";
import type { AgentMode, Incident, IncidentStep } from "../lib/types";

type ChainStep = IncidentStep & { tainted_by?: string };

interface ChainStages {
  read?: ChainStep;
  encode?: ChainStep;
  send?: ChainStep;
  stopped?: ChainStep;
  quarantined: boolean;
}

/** Which stages of the chain the incident's recorded steps reached (display only). */
function stagesFor(inc: Incident | undefined, mode: AgentMode | undefined): ChainStages {
  const steps: ChainStep[] = inc?.steps ?? [];
  const read = steps.find(isSecretRead);
  const encode = steps.find(isEncode);
  const send = steps.find((s) => s.action === "http_post");
  const stopped = steps.find((s) => s.action === "http_post" && s.result === "denied");
  const quarantined = !!inc && (inc.contained_ms != null || mode === "quarantined");
  return { read, encode, send, stopped, quarantined };
}

/** The same five stages, straight from the live chain's real tool events (no incident yet). */
function stagesLive(c: LiveChain): ChainStages {
  return { read: c.read, encode: c.encode, send: c.send, stopped: c.stopped, quarantined: c.quarantined };
}

interface StageDef {
  key: string;
  on: boolean;
  tone: Extract<Tone, "bad" | "held">;
  icon: ReactNode;
  label: string;
  sub?: string;
  step?: ChainStep;
}

const DRAW_FROM = "inset(0% 100% 100% 0%)";
const DRAW_TO = "inset(0% 0% 0% 0%)";

function StageNode({
  s,
  n,
  next,
  chainKey,
  last,
  stagger,
}: {
  s: StageDef;
  n: number;
  next?: StageDef;
  chainKey: string;
  last: boolean;
  stagger: boolean;
}) {
  const reduce = useReducedMotion();
  const color = toneVar[s.tone];
  // Live: a stage lights the moment its real event arrives (no stagger). Incident: a staggered entrance.
  const delay = stagger ? 0.12 * n : 0;
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
              transition={reduce ? { duration: 0 } : { duration: 0.55, ease: [0.22, 1, 0.36, 1], delay: 0.12 + delay }}
            />
          )}
        </span>
      )}

      <motion.div
        key={`${chainKey}-${s.key}-${s.on ? "on" : "off"}`}
        initial={s.on ? { scale: 0.7, opacity: 0 } : false}
        animate={{ scale: 1, opacity: 1 }}
        transition={{ ...SPRING, delay }}
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
          <Tooltip
            content={
              <span className="font-mono">
                {fmtClock(s.step.ts_ms)} · {s.step.target}
                {s.step.tainted_by ? ` · tainted by ${s.step.tainted_by}` : ""}
              </span>
            }
          >
            <span className="max-w-full cursor-default truncate font-mono text-[12px] text-muted">{s.step.target}</span>
          </Tooltip>
        )}
      </div>
    </li>
  );
}

/**
 * The attack chain. On the Live tab (no incident passed, or the latest incident in state) it runs LIVE:
 * while the focused agent has no open incident, the stages come from that agent's real tool calls of the
 * last minute and light one by one as each event arrives. A secret read alone (the benign deploy-bot reads
 * /app/.env every session) stays neutral: the live chain appears only once an encode or external send
 * follows the read, or the read carries tainted_by / a honeytoken hit (see useLiveChain chainOf). Once the checkpoint opens an incident, the
 * chain switches to the incident's recorded steps exactly as before. `live={false}` forces incident only.
 */
export function AttackChain({
  incident,
  mode,
  live = "auto",
}: {
  incident: Incident | undefined;
  mode: AgentMode | undefined;
  live?: boolean | "auto";
}) {
  const { state } = useTripwire();
  // Same selection as the Live tab (newest opened_ms); identity tells the Live tab apart from a
  // scrubbed / fetched copy in the incident sheet, which must always show the incident itself.
  const latest = useMemo(
    () => Object.values(state.incidents).reduce<Incident | undefined>((a, b) => (!a || b.opened_ms > a.opened_ms ? b : a), undefined),
    [state.incidents],
  );
  // The incident sheet (a dialog) always shows its own incident, even before its fetch replaces the object.
  const listRef = useRef<HTMLOListElement>(null);
  const [inDialog, setInDialog] = useState(false);
  useLayoutEffect(() => setInDialog(!!listRef.current?.closest('[role="dialog"]')), []);
  const liveOk = live === "auto" ? !inDialog && (incident === undefined || incident === latest) : live;
  const openIncident = !!incident && incident.closed_ms == null;
  const chain = useLiveChain(liveOk && !openIncident);
  // A live chain replaces a CLOSED incident only once it has real calls newer than that incident.
  const showLive = !!chain && (!incident || chain.lastTs > (incident.closed_ms ?? incident.opened_ms));

  const s = showLive && chain ? stagesLive(chain) : stagesFor(incident, mode);
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
      sub: !showLive && incident?.closed_ms ? "contained · since restored" : "agent blocked",
    },
  ];
  const reached = stages.filter((x) => x.on).length;
  const tone: Tone = s.quarantined ? "bad" : s.stopped ? (held ? "held" : "bad") : reached ? "bad" : "neutral";
  // Keyed by agent + the real secret-read time, so the live chain hands over to its incident (same
  // recorded steps) without replaying the entrance; a snapshot rebuild keeps the same key too.
  const agentId = showLive && chain ? chain.agentId : incident?.agent_id;
  const chainKey = agentId && s.read ? `${agentId}:${s.read.ts_ms}` : (incident?.id ?? "none");
  const taintedBy = showLive && chain ? chain.taintedBy : undefined;

  return (
    <GlowCard
      tone={tone}
      glow="soft"
      reveal={9}
      eyebrow={
        showLive ? (
          <>
            <Workflow /> Attack chain · live · from tool calls
          </>
        ) : incident ? (
          <>
            <Workflow /> Attack chain · incident
          </>
        ) : (
          <>
            <Workflow /> Attack chain{liveOk ? " · live · from tool calls" : ""}
          </>
        )
      }
      title={
        incident || showLive ? (
          <span className="inline-flex items-baseline gap-2">
            <span className="num-display text-[20px]">
              {reached}
              <span className="text-dim"> / {stages.length}</span>
            </span>
            <span className="text-[14px] font-medium text-muted">
              stages reached{showLive ? ` · last ${LIVE_WINDOW_MS / 1000} s` : ""}
            </span>
          </span>
        ) : (
          <span className="text-[14px] font-normal text-dim">
            {liveOk ? "Waiting for a secret read or an incident" : "Waiting for an incident"}
          </span>
        )
      }
      actions={
        showLive && chain ? (
          <div className="flex min-w-0 flex-wrap items-center justify-end gap-x-3 gap-y-1">
            <span className="inline-flex items-center gap-1.5 text-[12px] text-dim">
              agent <CopyId value={chain.agentId} />
            </span>
            {taintedBy && (
              <Tooltip content="tainted_by on the chain's tool calls, as recorded by the checkpoint">
                <Chip tone="held" mono className="max-w-[16rem] cursor-default truncate">
                  tainted by {taintedBy}
                </Chip>
              </Tooltip>
            )}
            <span className="text-[12px] text-dim">no incident yet</span>
          </div>
        ) : incident ? (
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
      <ol ref={listRef} className="flex flex-col @lg:flex-row @lg:items-start">
        {stages.map((st, i) => (
          <StageNode
            key={st.key}
            s={st}
            n={i}
            next={stages[i + 1]}
            chainKey={chainKey}
            last={i === stages.length - 1}
            stagger={!showLive}
          />
        ))}
      </ol>
    </GlowCard>
  );
}
