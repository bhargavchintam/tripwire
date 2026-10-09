import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { Bot, Hand, OctagonX, Undo2, Workflow } from "lucide-react";
import { toast } from "sonner";
import { Button } from "./ui/button";
import { Tooltip } from "./ui/tooltip";
import { ModeBadge, ResultBadge } from "./badges";
import { Count, CopyId, GlowCard, Kbd, toneSoftVar, toneTint, toneVar, type Tone } from "./fx";
import { isHoldReason, isSnapshotSwap } from "./live/streamChange";
import { api } from "../lib/api";
import { fmtClock } from "../lib/format";
import { cn } from "../lib/utils";
import type { AgentMode, Alert, ToolEvent } from "../lib/types";
import { useTripwire } from "../hooks/useTripwire";
import { usePresenter } from "../hooks/usePresenter";

export async function restoreAgent(agentId: string) {
  try {
    await api.restore(agentId);
    toast.success(`Restored ${agentId}`, { description: "Block cleared, watermark advanced" });
  } catch (e) {
    toast.error(`Restore failed: ${(e as Error).message}`);
  }
}

/** DOM id of an agent's card (the Live-tab orbit rings it). */
export function agentDomId(agentId: string): string {
  return `agent-card-${agentId.replace(/[^a-zA-Z0-9_-]/g, "-")}`;
}

const MODE_TONE: Record<AgentMode, Tone> = { normal: "ok", heightened: "held", quarantined: "bad" };
const MODE_NOTE: Record<AgentMode, string> = {
  normal: "Normal mode: calls pass the checkpoint",
  heightened: "On watch: risky sends are held",
  quarantined: "Quarantined: blocked at the checkpoint",
};
const HELD_MS = 1200;

const deniedKeyOf = (ev: ToolEvent | undefined) => (ev ? `${ev.ts_ms}-${ev.action}-${ev.target}` : null);

interface Moment {
  key: string | null;
  alerts: Alert[];
  timings: number[];
  /** Key of the newest denial that ARRIVED LIVE while this card was on screen (null = history). */
  fresh: string | null;
  /** That fresh denial was a hold decision and its ~1.2 s HELD phase is still running. */
  held: boolean;
}

/**
 * The denial "moment" of a card, derived synchronously during render (no effect -> extra paint):
 * when a new denial lands live, the SAME render that first sees it already knows whether it opens
 * with the amber HELD phase, so the red DENIED flash can never paint first.
 * Denials already present at mount, or delivered by a /stream snapshot (load, reconnect, reset), are
 * history: they render calmly, without the flash / HELD replay.
 */
function useDenialMoment(ev: ToolEvent | undefined): { fresh: string | null; holding: boolean } {
  const { state } = useTripwire();
  const key = deniedKeyOf(ev);
  const [m, setM] = useState<Moment>(() => ({
    key,
    alerts: state.alerts,
    timings: state.queryTimings,
    fresh: null,
    held: false,
  }));
  let cur = m;
  if (m.key !== key || m.alerts !== state.alerts || m.timings !== state.queryTimings) {
    const snapshot = isSnapshotSwap(
      { alerts: m.alerts, queryTimings: m.timings },
      { alerts: state.alerts, queryTimings: state.queryTimings },
    );
    const arrived = !!key && key !== m.key && !snapshot;
    cur = {
      key,
      alerts: state.alerts,
      timings: state.queryTimings,
      fresh: arrived ? key : m.fresh === key ? m.fresh : null,
      held: arrived ? isHoldReason(ev?.reason) : m.fresh === key && m.held,
    };
    // Render-phase update: React re-renders with `cur` before committing, so nothing stale paints.
    setM(cur);
  }
  const { fresh, held } = cur;
  useEffect(() => {
    if (!held || !fresh) return;
    const t = setTimeout(() => setM((p) => (p.fresh === fresh ? { ...p, held: false } : p)), HELD_MS);
    return () => clearTimeout(t);
  }, [held, fresh]);
  return { fresh, holding: held };
}

/** Rubber-stamp DENIED (red ink, slight rotate). Springs in only when the denial arrived live. */
function DeniedStamp({ ev, fresh }: { ev: ToolEvent; fresh: string | null }) {
  return (
    <Tooltip
      content={
        <span className="font-mono">
          Last denied {fmtClock(ev.ts_ms, false)} · {ev.action} {ev.target}
          {ev.reason ? ` · ${ev.reason}` : ""}
        </span>
      }
    >
      <motion.span
        key={`denied-${fresh ?? "calm"}`}
        initial={fresh ? { scale: 1.9, rotate: -18, opacity: 0 } : { opacity: 0 }}
        animate={{ scale: 1, rotate: -7, opacity: 1 }}
        transition={fresh ? { type: "spring", stiffness: 320, damping: 19, mass: 0.9 } : { duration: 0.24 }}
        className="relative ml-1 inline-flex shrink-0 cursor-default select-none items-center gap-1 rounded-[7px] border-2 border-bad bg-bad-soft/60 px-2 py-[3px] font-mono text-[12px] font-bold leading-none tracking-[0.18em] text-bad [box-shadow:inset_0_0_0_1.5px_var(--color-panel),inset_0_0_0_2.5px_color-mix(in_oklab,var(--color-bad)_40%,transparent)]"
      >
        {fresh && (
          <motion.span
            aria-hidden
            className="pointer-events-none absolute -inset-1 rounded-[10px] border-2 border-bad"
            initial={{ scale: 1, opacity: 0.7 }}
            animate={{ scale: 1.55, opacity: 0 }}
            transition={{ delay: 0.1, duration: 0.6, ease: "easeOut" }}
          />
        )}
        <OctagonX className="size-3.5" strokeWidth={2.25} />
        DENIED
      </motion.span>
    </Tooltip>
  );
}

export function AgentCard({
  agentId,
  index,
  highlighted = false,
  onHover,
  className,
}: {
  agentId: string;
  /** Stagger slot for the mount rise-in. */
  index?: number;
  /** Briefly ring the card (the orbit planet for this agent was clicked). */
  highlighted?: boolean;
  /** Hover / focus enter (id) and leave (null): the orbit rings this agent's planet. */
  onHover?: (agentId: string | null) => void;
  className?: string;
}) {
  const { state } = useTripwire();
  const presenter = usePresenter().on;
  const mode: AgentMode = state.modes[agentId] ?? "normal";
  const st = state.stats[agentId];
  const last = st?.last;
  const lastDenied = st?.lastDenied;
  const { fresh, holding } = useDenialMoment(lastDenied);
  const quarantined = mode === "quarantined";
  const tone = MODE_TONE[mode];
  const guild = agentId.startsWith("guild:");
  const restoreVisible = mode !== "normal" || presenter;
  // Same object when the agent's latest call is the denial (the reducer stores one event in both).
  const lastIsDenial = !!last && last === lastDenied;
  // X restores the MOST RECENTLY quarantined agent: only that card shows the X hint.
  const xTarget = [...state.quarantineOrder].reverse().find((a) => state.modes[a] === "quarantined");
  const xHint = quarantined && xTarget === agentId;

  return (
    <motion.div
      initial={false}
      animate={{ scale: quarantined ? [1, 1.015, 1] : 1 }}
      transition={{ duration: 0.45 }}
      onMouseEnter={() => onHover?.(agentId)}
      onMouseLeave={() => onHover?.(null)}
      onFocus={() => onHover?.(agentId)}
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget as Node | null)) onHover?.(null);
      }}
      className={cn(
        "h-full min-w-0 rounded-[var(--radius-card)]",
        holding ? "animate-held" : quarantined ? "animate-quarantine" : "",
        className,
      )}
    >
      <GlowCard
        id={agentDomId(agentId)}
        tabIndex={-1}
        tone={tone}
        glow="none"
        reveal={index}
        aria-label={`Agent ${agentId}`}
        className={cn(
          "@container h-full scroll-mt-28 outline-2 outline-offset-2 outline-transparent transition-[outline-color,box-shadow,translate] duration-300 ease-out",
          "hover:shadow-lift motion-safe:hover:-translate-y-px",
          highlighted && "outline-brand",
          quarantined && "shadow-[0_0_0_3px_var(--color-bad-soft),var(--shadow-card)]",
        )}
        bodyClassName="flex flex-col p-0"
      >
        {/* 3px state bar */}
        <span
          aria-hidden
          className={cn("absolute inset-x-0 top-0 z-10 h-[3px] transition-colors duration-500", quarantined && "animate-breathe")}
          style={{ background: toneVar[tone] }}
        />

        {/* Fresh denial lands live: a soft red wash fades across the card (never for history, never during HELD). */}
        <AnimatePresence>
          {fresh && !holding && (
            <motion.div
              key={`wash-${fresh}`}
              aria-hidden
              className="pointer-events-none absolute inset-0 z-20 rounded-[inherit]"
              style={{
                background:
                  "linear-gradient(180deg, color-mix(in oklab, var(--color-bad) 22%, transparent), color-mix(in oklab, var(--color-bad) 6%, transparent))",
              }}
              initial={{ opacity: 1 }}
              animate={{ opacity: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.85, ease: "easeOut" }}
            />
          )}
        </AnimatePresence>

        {/* header band: soft state tint */}
        <div
          className="flex items-center gap-2.5 border-b px-4 pb-2 pt-3 transition-[background,border-color] duration-500"
          style={{
            background: `linear-gradient(180deg, ${toneSoftVar[tone]} 0%, color-mix(in oklab, ${toneSoftVar[tone]} 45%, var(--color-panel)) 100%)`,
            borderColor: `color-mix(in oklab, ${toneVar[tone]} 14%, var(--color-line))`,
          }}
        >
          <Tooltip content={guild ? "Agent id starts with guild:, it runs through Guild" : "Live agent"}>
            <span
              className={cn(
                "grid size-7 shrink-0 place-items-center rounded-lg border bg-panel shadow-sm transition-colors duration-500 [&_svg]:size-4",
                toneTint[tone],
              )}
            >
              {guild ? <Workflow strokeWidth={1.75} /> : <Bot strokeWidth={1.75} />}
            </span>
          </Tooltip>
          <div className="flex min-w-0 flex-1 items-center gap-2">
            <CopyId
              value={agentId}
              className="min-w-0"
              label={<span className="font-sans text-[17px] font-semibold tracking-[-0.02em] text-fg">{agentId}</span>}
            />
            {guild && (
              <span className="hidden shrink-0 rounded-full border border-paper-2 bg-paper px-2 py-0.5 font-mono text-[11px] text-ink @[400px]:inline">
                via Guild
              </span>
            )}
          </div>
          <Tooltip
            content={
              mode === "normal" ? (
                "Nothing to restore: agent is in normal mode"
              ) : (
                <>
                  Restore {agentId}: clear the block, advance the watermark
                  {xHint && (
                    <>
                      {" "}
                      <span className="kbd ml-1">X</span>
                    </>
                  )}
                </>
              )
            }
          >
            {/* span wrapper keeps the tooltip working while the button is disabled */}
            <span
              className={cn(
                "shrink-0 transition-opacity duration-200",
                restoreVisible
                  ? "opacity-100"
                  : "opacity-0 group-hover/card:opacity-100 group-focus-within/card:opacity-100 [@media(hover:none)]:opacity-100",
              )}
            >
              <Button
                size="sm"
                variant={mode === "normal" ? "ghost" : "ok"}
                disabled={mode === "normal"}
                onClick={() => restoreAgent(agentId)}
                className="h-7 px-2.5"
              >
                <Undo2 /> Restore
                {xHint && !presenter && <Kbd className="ml-0.5 h-[18px] min-w-[18px] text-[10px]">X</Kbd>}
              </Button>
            </span>
          </Tooltip>
          <Tooltip content={MODE_NOTE[mode]}>
            <span className="inline-flex shrink-0 cursor-default">
              <AnimatePresence mode="wait" initial={false}>
                <motion.span
                  key={mode}
                  initial={{ opacity: 0, y: -5 }}
                  animate={{ opacity: 1, y: 0 }}
                  exit={{ opacity: 0, y: 5 }}
                  transition={{ duration: 0.18 }}
                  className="inline-flex"
                >
                  <ModeBadge mode={mode} />
                </motion.span>
              </AnimatePresence>
            </span>
          </Tooltip>
        </div>

        {/* body: last real call | denied count + stamp */}
        <div className="grid flex-1 grid-cols-1 content-center gap-2.5 px-4 py-2.5 @[380px]:grid-cols-[minmax(0,1fr)_auto] @[380px]:items-center @[380px]:gap-5">
          <div className="min-w-0">
            {!presenter && (
              <div className="mb-1.5 flex items-baseline gap-2">
                <span className="eyebrow">Last action</span>
                <span className="font-mono text-[12px] text-dim">{last ? fmtClock(last.ts_ms, false) : ""}</span>
              </div>
            )}
            <div className="flex min-w-0 items-center gap-2">
              {last ? (
                <>
                  {presenter && (
                    <span className="shrink-0 font-mono text-[12px] text-dim" title="Last action">
                      {fmtClock(last.ts_ms, false)}
                    </span>
                  )}
                  <span
                    className="min-w-0 truncate font-mono text-[13px] leading-6"
                    title={`${last.action} ${last.target}`}
                  >
                    <span className="font-semibold text-fg">{last.action}</span>{" "}
                    <span className="text-muted">{last.target}</span>
                  </span>
                  {/* The latest call was a denial: it gets the stamp (HELD first when it was a live hold). */}
                  <AnimatePresence mode="wait" initial={false}>
                    {lastIsDenial && lastDenied ? (
                      holding ? (
                        <motion.span
                          key={`held-${fresh}`}
                          initial={{ scale: 0.85, opacity: 0 }}
                          animate={{ scale: [1, 1.05, 1], opacity: 1 }}
                          exit={{ scale: 0.9, opacity: 0, transition: { duration: 0.12 } }}
                          transition={{
                            scale: { duration: 0.6, repeat: Infinity, ease: "easeInOut" },
                            opacity: { duration: 0.15 },
                          }}
                          className="ml-1 inline-flex shrink-0 items-center gap-1 rounded-[7px] border-2 border-held bg-held-soft px-2 py-[3px] font-mono text-[12px] font-bold leading-none tracking-[0.18em] text-held"
                        >
                          <Hand className="size-3.5" strokeWidth={2.25} /> HELD
                        </motion.span>
                      ) : (
                        <DeniedStamp key={`stamp-${fresh ?? "calm"}`} ev={lastDenied} fresh={fresh} />
                      )
                    ) : (
                      <motion.span
                        key="result"
                        className="shrink-0"
                        initial={{ opacity: 0 }}
                        animate={{ opacity: 1 }}
                        exit={{ opacity: 0, transition: { duration: 0.1 } }}
                        transition={{ duration: 0.18 }}
                      >
                        <ResultBadge result={last.result} reason={last.reason} />
                      </motion.span>
                    )}
                  </AnimatePresence>
                </>
              ) : (
                <span className="font-mono text-[13px] leading-6 text-dim">— no calls on the stream yet</span>
              )}
            </div>
          </div>

          <div className="flex items-center @[380px]:justify-end">
            <Tooltip content="Denied actions seen on the live stream since this page loaded">
              <div className="flex cursor-default items-baseline gap-2">
                <span className="eyebrow">Denied</span>
                <Count
                  value={st?.denied ?? 0}
                  className={cn("num-display text-[26px] leading-none", st?.denied ? "text-bad" : "text-fg")}
                />
              </div>
            </Tooltip>
          </div>
        </div>
      </GlowCard>
    </motion.div>
  );
}
