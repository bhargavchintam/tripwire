import { useEffect, useRef, type CSSProperties } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Radio } from "lucide-react";
import { Button } from "../ui/button";
import { ReasonText, ResultBadge } from "../badges";
import { EASE_OUT, SPRING, toneVar, type Tone } from "../fx";
import { fmtClock } from "../../lib/format";
import { cn } from "../../lib/utils";
import type { IncidentStep } from "../../lib/types";

/** Tone per step result: ok = green, held denial = amber, other denial = red. */
export function stepTone(s: IncidentStep): Tone {
  if (s.result === "ok") return "ok";
  if (s.result === "denied") return s.reason?.startsWith("hold_") ? "held" : "bad";
  return "neutral";
}

const DOT: Record<Tone, string> = {
  ok: "bg-ok ring-ok-soft",
  held: "bg-held ring-held-soft",
  bad: "bg-bad ring-bad-soft",
  model: "bg-model ring-model-soft",
  info: "bg-info ring-info-soft",
  paper: "bg-paper-2 ring-paper",
  neutral: "bg-muted ring-neutral-soft",
};

/** Above this many steps only the non-ok steps get a tick under the scrubber (keeps it legible). */
const TICK_ALL_MAX = 80;

/**
 * Time-travel scrubber: brand-indigo range (0 = before the first step, n = live) with one tick per
 * recorded step underneath, coloured by that step's real result. "Live" jumps back to following.
 */
export function Scrubber({
  steps,
  pos,
  onScrub,
}: {
  steps: IncidentStep[];
  pos: number;
  /** null = follow live. */
  onScrub: (p: number | null) => void;
}) {
  const n = steps.length;
  const pct = n ? (pos / n) * 100 : 100;
  const ticks = steps
    .map((s, i) => ({ i, tone: stepTone(s) }))
    .filter((t) => n <= TICK_ALL_MAX || t.tone !== "ok");
  return (
    <div className="mb-5 flex items-center gap-3 rounded-xl border border-line bg-panel-2 py-2 pl-4 pr-2">
      <div className="relative min-w-0 flex-1 pb-2">
        <input
          type="range"
          min={0}
          max={n}
          step={1}
          value={pos}
          onChange={(e) => {
            const p = Number(e.target.value);
            onScrub(p >= n ? null : p);
          }}
          aria-label="Scrub through incident steps"
          aria-valuetext={pos >= n ? "live" : `step ${pos} of ${n}`}
          style={{ "--p": `${pct}%` } as CSSProperties}
          className={cn(
            "relative z-10 block h-5 w-full cursor-pointer appearance-none bg-transparent focus-visible:outline-none",
            "[&::-webkit-slider-runnable-track]:h-1 [&::-webkit-slider-runnable-track]:rounded-full",
            "[&::-webkit-slider-runnable-track]:bg-[linear-gradient(to_right,var(--color-brand)_var(--p),var(--color-line-strong)_var(--p))]",
            "[&::-webkit-slider-thumb]:-mt-1.5 [&::-webkit-slider-thumb]:size-4 [&::-webkit-slider-thumb]:appearance-none [&::-webkit-slider-thumb]:rounded-full",
            "[&::-webkit-slider-thumb]:border [&::-webkit-slider-thumb]:border-brand [&::-webkit-slider-thumb]:bg-panel [&::-webkit-slider-thumb]:shadow-sm",
            "[&::-webkit-slider-thumb]:transition-[transform,box-shadow] [&::-webkit-slider-thumb]:duration-150 hover:[&::-webkit-slider-thumb]:scale-110",
            "focus-visible:[&::-webkit-slider-thumb]:shadow-[0_0_0_4px_var(--color-brand-soft)]",
            "[&::-moz-range-track]:h-1 [&::-moz-range-track]:rounded-full [&::-moz-range-track]:bg-line-strong",
            "[&::-moz-range-progress]:h-1 [&::-moz-range-progress]:rounded-full [&::-moz-range-progress]:bg-brand",
            "[&::-moz-range-thumb]:size-4 [&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:border [&::-moz-range-thumb]:border-brand [&::-moz-range-thumb]:bg-panel",
          )}
        />
        {/* one tick per real step, aligned with the thumb positions (thumb is 16px wide) */}
        <div aria-hidden className="pointer-events-none absolute inset-x-0 bottom-0 h-1.5">
          {ticks.map(({ i, tone }) => (
            <span
              key={i}
              className="absolute top-0 size-1.5 -translate-x-1/2 rounded-full transition-opacity duration-200"
              style={{
                left: `calc(8px + (100% - 16px) * ${(i + 1) / n})`,
                background: toneVar[tone],
                opacity: i < pos ? 0.9 : 0.3,
              }}
            />
          ))}
        </div>
      </div>
      <Button variant="outline" size="sm" disabled={pos >= n} onClick={() => onScrub(null)}>
        <Radio strokeWidth={1.75} /> Live
      </Button>
    </div>
  );
}

/**
 * Vertical timeline: a hairline draws down through the steps (one segment per gap) and each dot is
 * coloured by the step's real result; held / denied steps sit on their soft tint. The draw-down plays
 * once when the sheet opens; a step that arrives later just appears (no fake delay). The caller keys
 * it by incident id so switching incidents replays it.
 */
export function TimelineSteps({ steps, pos }: { steps: IncidentStep[]; pos: number }) {
  const n = steps.length;
  const reduce = useReducedMotion();
  const mounting = useRef(true);
  useEffect(() => {
    mounting.current = false;
  }, []);
  if (n === 0) return <div className="text-sm text-dim">No steps recorded.</div>;
  return (
    <ol className="relative">
      {steps.map((s, i) => {
        const future = i >= pos;
        const current = i === pos - 1 && pos < n;
        const tone: Tone = future ? "neutral" : stepTone(s);
        const flagged = !future && (tone === "held" || tone === "bad");
        const play = mounting.current && !reduce && i < 40;
        const d = play ? Math.min(0.08 + i * 0.05, 0.9) : 0;
        return (
          <li key={`${s.ts_ms}-${i}`} className="relative pb-1.5 pl-8 last:pb-0">
            {i < n - 1 && (
              <motion.span
                aria-hidden
                className="absolute left-[9px] top-5 -bottom-5 w-px origin-top transition-colors duration-300"
                style={{ background: i + 1 < pos ? "var(--color-line-strong)" : "var(--color-line)" }}
                initial={play ? { scaleY: 0 } : false}
                animate={{ scaleY: 1 }}
                transition={{ delay: d + 0.1, duration: 0.32, ease: EASE_OUT }}
              />
            )}
            <motion.span
              aria-hidden
              className={cn(
                "absolute left-[5px] top-4 size-[9px] rounded-full transition-[background-color,box-shadow] duration-300",
                future ? "border border-line-strong bg-panel" : cn("ring-4", DOT[tone]),
              )}
              initial={play ? { opacity: 0, scale: 0.4 } : false}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ ...SPRING, delay: d }}
            />
            <motion.div
              initial={play ? { opacity: 0, x: -4 } : false}
              animate={{ opacity: future ? 0.45 : 1, x: 0 }}
              transition={{ delay: play ? d + 0.04 : 0, duration: 0.24, ease: EASE_OUT }}
              className={cn(
                "rounded-xl border px-3 py-2 transition-[background-color,border-color,box-shadow] duration-200",
                flagged && tone === "held" && "border-held-line bg-held-soft/70",
                flagged && tone === "bad" && "border-bad-line bg-bad-soft/70",
                !flagged && "border-transparent hover:bg-panel-2",
                current && "ring-2 ring-brand-line",
              )}
            >
              <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                <span className="font-mono text-xs tabular-nums text-dim">{fmtClock(s.ts_ms)}</span>
                <span className="font-mono text-[13px] font-semibold text-fg">{s.action}</span>
                {future ? (
                  <span className="text-xs text-dim">not yet</span>
                ) : (
                  <>
                    <ResultBadge result={s.result} reason={s.reason} />
                    {s.reason ? <ReasonText reason={s.reason} /> : null}
                  </>
                )}
              </div>
              <div className="mt-0.5 truncate font-mono text-xs text-muted" title={s.target}>
                {s.target}
              </div>
            </motion.div>
          </li>
        );
      })}
    </ol>
  );
}
