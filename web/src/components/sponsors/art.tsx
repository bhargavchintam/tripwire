// Framed, CSS/SVG-only visuals for the Sponsors bento ("Paper & Signal", light).
// Everything drawn here is DECORATIVE: aria-hidden, no numbers, no axes, no values, nothing that can
// be read as a measurement, an agent or an event. The only motion is:
//   - a one-off entrance on mount (switching to the tab remounts it),
//   - hover feedback driven by the pointer (group-hover/card),
//   - a one-off flash / pulse keyed on a REAL state change passed in by the caller
//     (events_stored changed, a new model id was seen, a guild:* tool_event arrived, an approval).
// Real values live in the proof panels (SponsorsTab), never in the art.
import { useState, type ReactNode } from "react";
import { motion, useReducedMotion } from "motion/react";
import { Brain, Check, Lightbulb, ShieldCheck, Workflow } from "lucide-react";
import { cn } from "../../lib/utils";
import { isNum } from "../../lib/format";
import { Count, EASE_OUT, SPRING_SOFT, toneVar, type Tone } from "../fx";

const EASE_INK = [0.65, 0, 0.35, 1] as const;

/**
 * Counts REAL changes of `key` after mount (React's "store the previous value in state" pattern).
 * A change from empty (null/undefined) only counts when `fromEmpty` is true, so a first data load
 * doesn't masquerade as new activity. Use the result as a React `key` to replay a one-off effect.
 */
export function useChangeCount(key: string | number | null | undefined, fromEmpty = false): number {
  const [prev, setPrev] = useState(key);
  const [n, setN] = useState(0);
  if (!Object.is(prev, key)) {
    setPrev(key);
    const had = prev !== undefined && prev !== null;
    const has = key !== undefined && key !== null;
    if (has && (had || fromEmpty)) setN(n + 1);
  }
  return n;
}

/** Count-up milliseconds that renders exactly like `fmtMs` (DASH when unmeasured). */
export function Ms({ v, className }: { v: number | null | undefined; className?: string }) {
  if (!isNum(v)) return <Count value={v} className={className} />;
  if (v >= 10_000) return <Count value={v / 1000} digits={1} minDigits={1} suffix=" s" className={className} />;
  const small = v < 10;
  return <Count value={v} digits={small ? 1 : 0} minDigits={small ? 1 : 0} suffix=" ms" className={className} />;
}

/**
 * The framed visual well of a sponsor card: warm off-white inset, hairline, faint dot grid fading
 * out at the edges. Purely decorative (aria-hidden). Optional mono `caption` bottom-left (a name,
 * never a value).
 */
export function ArtFrame({
  children,
  caption,
  className,
}: {
  children: ReactNode;
  caption?: ReactNode;
  className?: string;
}) {
  return (
    <div
      aria-hidden
      className={cn(
        "relative isolate h-40 overflow-hidden rounded-xl border border-line bg-panel-2",
        "shadow-[inset_0_1px_0_rgb(255_255_255/0.9)] transition-[border-color] duration-300 ease-out group-hover/card:border-line-strong",
        className,
      )}
    >
      <div className="pointer-events-none absolute inset-0 -z-10 [background-image:radial-gradient(var(--color-line-strong)_0.9px,transparent_1.1px)] [background-position:7px_7px] [background-size:14px_14px] [mask-image:radial-gradient(85%_85%_at_50%_45%,black_20%,transparent_80%)]" />
      {children}
      {caption && (
        <span className="pointer-events-none absolute bottom-2.5 left-3 font-mono text-[12px] leading-none text-dim">
          {caption}
        </span>
      )}
    </div>
  );
}

const enter = (reduce: boolean | null, i: number, base = 0.08) =>
  reduce
    ? { initial: false as const }
    : {
        initial: { opacity: 0, y: 8 },
        animate: { opacity: 1, y: 0 },
        transition: {
          y: { ...SPRING_SOFT, delay: base + i * 0.04 },
          opacity: { duration: 0.24, ease: EASE_OUT, delay: base + i * 0.04 },
        },
      };

/* ------------------------------------------------------------------------------------------------
 * ClickHouse: a column-oriented storage schematic of tripwire.events. Column NAMES are the real
 * schema columns (data/schema.sql); the cells are empty blocks of identical size (a picture of
 * columnar storage, not a chart). Columns rise in on mount and ripple up on hover; the newest row
 * sweeps once only when `flash` changes, i.e. when events_stored really changed.
 * ---------------------------------------------------------------------------------------------- */
const COLUMNS = ["ts", "agent_id", "action", "target", "result", "code_ref", "hash"] as const;
const CELLS = 6;

export function ColumnsArt({ flash }: { flash: number }) {
  const reduce = useReducedMotion();
  return (
    <div className="absolute inset-x-5 bottom-0 top-5 flex gap-2.5 [mask-image:linear-gradient(to_bottom,black_40%,transparent_96%)]">
      {COLUMNS.map((name, i) => (
        <motion.div
          key={name}
          className={cn("flex min-w-0 flex-1 flex-col gap-2", i >= 5 && "max-sm:hidden")}
          {...enter(reduce, i)}
        >
          <span className="truncate font-mono text-[12px] leading-none text-dim">{name}</span>
          <div
            className="flex flex-col gap-1 transition-transform duration-300 ease-out motion-safe:group-hover/card:-translate-y-1"
            style={{ transitionDelay: `${i * 30}ms` }}
          >
            {Array.from({ length: CELLS }, (_, j) => (
              <span
                key={j}
                className={cn(
                  "relative h-3 overflow-hidden rounded-[4px] border",
                  j === 0 ? "border-info-line bg-info-soft" : "border-line bg-panel",
                )}
              >
                {j === 0 && flash > 0 && !reduce && (
                  <motion.span
                    key={flash}
                    className="absolute inset-0 bg-info"
                    initial={{ opacity: 0.55 }}
                    animate={{ opacity: 0 }}
                    transition={{ duration: 1.1, ease: "easeOut", delay: i * 0.05 }}
                  />
                )}
              </span>
            ))}
          </div>
        </motion.div>
      ))}
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * AkashML: requests flow in from the left, through a calm "model" ring, and one verdict line leaves
 * on the right. Lines and rings draw themselves on mount; dashed rings turn on hover; one ring
 * ripples out only when `pulse` changes (a new distinct model id was really seen).
 * ---------------------------------------------------------------------------------------------- */
export function RingArt({ pulse }: { pulse: number }) {
  const reduce = useReducedMotion();
  const draw = (delay: number, duration = 1.1) =>
    reduce
      ? {}
      : {
          initial: { pathLength: 0, opacity: 0 },
          animate: { pathLength: 1, opacity: 1 },
          transition: { pathLength: { duration, ease: EASE_OUT, delay }, opacity: { duration: 0.2, delay } },
        };
  const spin = "origin-center [transform-box:fill-box] transition-transform duration-[1400ms] ease-out";
  return (
    <>
      {/* Flow lines wipe in left -> right (clip-path, so the dashes stay dashed). */}
      <motion.svg
        viewBox="0 0 400 160"
        preserveAspectRatio="none"
        className="absolute inset-0 h-full w-full"
        fill="none"
        strokeLinecap="round"
        initial={reduce ? false : { clipPath: "inset(0% 100% 0% 0%)" }}
        animate={{ clipPath: "inset(0% 0% 0% 0%)" }}
        transition={{ duration: 1.1, ease: EASE_INK, delay: 0.05 }}
      >
        {["M 0 52 C 110 52 130 80 200 80", "M 0 80 L 200 80", "M 0 108 C 110 108 130 80 200 80"].map((d) => (
          <path
            key={d}
            d={d}
            stroke="var(--color-line-strong)"
            strokeWidth={1}
            strokeDasharray="3 4"
            vectorEffect="non-scaling-stroke"
          />
        ))}
        <path
          d="M 200 80 L 400 80"
          stroke="var(--color-model)"
          strokeOpacity={0.45}
          strokeWidth={1.25}
          vectorEffect="non-scaling-stroke"
        />
      </motion.svg>
      <div className="absolute inset-0 grid place-items-center">
        <svg viewBox="-80 -80 160 160" className="size-[136px] overflow-visible">
          <circle r={76} fill="var(--color-panel-2)" />
          <motion.circle r={74} stroke="var(--color-model)" strokeOpacity={0.18} strokeWidth={1} fill="none" {...draw(0.1)} />
          <g className={cn(spin, "motion-safe:group-hover/card:rotate-[60deg]")}>
            <circle
              r={58}
              fill="none"
              stroke="var(--color-model)"
              strokeOpacity={0.45}
              strokeWidth={1.25}
              strokeDasharray="1.5 6"
              strokeLinecap="round"
            />
          </g>
          <motion.circle
            r={42}
            fill="var(--color-model-soft)"
            fillOpacity={0.55}
            stroke="var(--color-model)"
            strokeOpacity={0.3}
            strokeWidth={1}
            {...draw(0.3)}
          />
          <g className={cn(spin, "motion-safe:group-hover/card:-rotate-[45deg]")}>
            <circle r={30} fill="none" stroke="var(--color-model)" strokeOpacity={0.3} strokeWidth={1} strokeDasharray="8 5" />
          </g>
          {pulse > 0 && !reduce && (
            <motion.circle
              key={pulse}
              fill="none"
              stroke="var(--color-model)"
              strokeWidth={1.25}
              initial={{ r: 24, opacity: 0.7 }}
              animate={{ r: 80, opacity: 0 }}
              transition={{ duration: 1.6, ease: "easeOut" }}
            />
          )}
        </svg>
        <span className="absolute grid size-11 place-items-center rounded-full border border-model-line bg-panel text-model shadow-sm [&_svg]:size-[18px]">
          <Brain strokeWidth={1.75} />
        </span>
      </div>
    </>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Semgrep: a tiny editor window of abstract code (token bars, no text, no line numbers) with ONE
 * finding line. On mount the line is swept in state red (the finding), then settles to ok green
 * with a "fixed" check: the committed receipt says finding #1 (OWASP LLM01) is fixed.
 * Reduced motion: the settled (fixed) state, no sweep.
 * ---------------------------------------------------------------------------------------------- */
const CODE: { indent: number; segs: number[]; finding?: boolean }[] = [
  { indent: 0, segs: [16, 30] },
  { indent: 1, segs: [11, 22, 15] },
  { indent: 2, segs: [28, 10] },
  { indent: 2, segs: [9, 30, 11], finding: true },
  { indent: 1, segs: [20, 13] },
  { indent: 0, segs: [7] },
];
const SEG = ["bg-line-strong", "bg-line", "bg-panel-3"];
const FIX_AT = 1.25;

export function CodeArt() {
  const reduce = useReducedMotion();
  return (
    <div className="absolute inset-x-5 bottom-0 top-4 overflow-hidden rounded-t-[10px] border border-b-0 border-line bg-panel shadow-sm transition-transform duration-300 ease-out motion-safe:group-hover/card:-translate-y-1">
      <div className="flex items-center gap-1.5 border-b border-line px-3 py-2">
        {[0, 1, 2].map((i) => (
          <span key={i} className="size-1.5 rounded-full bg-line-strong" />
        ))}
        <span className="ml-2 h-1.5 w-16 rounded-full bg-panel-3" />
      </div>
      <div className="flex flex-col gap-1 p-2">
        {CODE.map((l, j) => (
          <motion.div
            key={j}
            className="relative isolate flex h-[18px] items-center gap-2 rounded-[5px] pr-1.5"
            {...(reduce
              ? { initial: false as const }
              : {
                  initial: { opacity: 0, x: -6 },
                  animate: { opacity: 1, x: 0 },
                  transition: { duration: 0.36, ease: EASE_OUT, delay: 0.08 + j * 0.04 },
                })}
          >
            {l.finding && (
              <>
                {!reduce && (
                  <motion.span
                    className="absolute inset-0 -z-10 origin-left rounded-[5px] border-l-2 border-bad bg-bad-soft"
                    initial={{ scaleX: 0, opacity: 1 }}
                    animate={{ scaleX: 1, opacity: 0 }}
                    transition={{
                      scaleX: { duration: 0.6, ease: EASE_INK, delay: 0.4 },
                      opacity: { duration: 0.35, ease: "easeOut", delay: FIX_AT },
                    }}
                  />
                )}
                <motion.span
                  className="absolute inset-0 -z-10 rounded-[5px] border-l-2 border-ok bg-ok-soft"
                  initial={reduce ? false : { opacity: 0 }}
                  animate={{ opacity: 1 }}
                  transition={{ duration: 0.35, ease: "easeOut", delay: FIX_AT }}
                />
              </>
            )}
            <span className="flex w-4 shrink-0 justify-end">
              <span className={cn("h-[3px] w-2.5 rounded-full", l.finding ? "bg-ok/50" : "bg-line")} />
            </span>
            <span className="flex min-w-0 flex-1 items-center gap-1.5" style={{ paddingLeft: l.indent * 12 }}>
              {l.segs.map((w, k) => (
                <span
                  key={k}
                  className={cn("h-[6px] shrink-0 rounded-full", SEG[(j + k) % SEG.length])}
                  style={{ width: `${w}%` }}
                />
              ))}
            </span>
            {l.finding && (
              <motion.span
                className="tint-ok inline-flex shrink-0 items-center gap-1 rounded-full border px-1.5 py-[3px] font-mono text-[11px] font-medium leading-none"
                initial={reduce ? false : { opacity: 0, scale: 0.9 }}
                animate={{ opacity: 1, scale: 1 }}
                transition={{ ...SPRING_SOFT, delay: FIX_AT + 0.05 }}
              >
                <Check className="size-3" strokeWidth={2} />
                LLM01
              </motion.span>
            )}
          </motion.div>
        ))}
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Guild: two linked nodes (Guild workspace -> Tripwire checkpoint). The connector draws on mount; a
 * dot travels along it only when `pulse` changes, i.e. a guild:* tool_event really arrived on the
 * stream, tinted by that event's real result.
 * ---------------------------------------------------------------------------------------------- */
function LinkNode({ icon, label, sub, i }: { icon: ReactNode; label: string; sub: string; i: number }) {
  const reduce = useReducedMotion();
  return (
    <motion.div className="flex w-28 shrink-0 flex-col items-center gap-2.5 text-center" {...enter(reduce, i, 0.1)}>
      <span className="grid size-12 place-items-center rounded-[14px] border border-line bg-panel text-fg shadow-sm transition-[transform,box-shadow,border-color] duration-300 ease-out group-hover/card:border-line-strong group-hover/card:shadow-md motion-safe:group-hover/card:-translate-y-0.5 [&_svg]:size-5">
        {icon}
      </span>
      <span className="text-[12px] font-medium leading-tight text-fg">
        {label}
        <span className="block font-normal text-dim">{sub}</span>
      </span>
    </motion.div>
  );
}

export function LinkArt({ pulse, pulseTone = "info" }: { pulse: number; pulseTone?: Tone }) {
  const reduce = useReducedMotion();
  return (
    <div className="absolute inset-0 flex items-center justify-center px-4">
      <div className="flex w-full max-w-sm items-start">
        <LinkNode icon={<Workflow strokeWidth={1.75} />} label="Guild" sub="workspace" i={0} />
        <div className="relative mt-6 h-px flex-1">
          <span className="absolute inset-x-0 -top-6 text-center text-[12px] leading-none text-dim">tool calls</span>
          <motion.span
            className="absolute inset-0 origin-left bg-[repeating-linear-gradient(90deg,var(--color-line-strong)_0_5px,transparent_5px_9px)]"
            initial={reduce ? false : { scaleX: 0 }}
            animate={{ scaleX: 1 }}
            transition={{ duration: 0.8, ease: EASE_INK, delay: 0.3 }}
          />
          <span className="absolute -right-0.5 top-1/2 size-1.5 -translate-y-1/2 rotate-45 border-r border-t border-line-strong" />
          {pulse > 0 && !reduce && (
            <motion.span
              key={pulse}
              className="absolute top-1/2 -ml-1 size-2 -translate-y-1/2 rounded-full"
              style={{ background: toneVar[pulseTone], boxShadow: `0 0 0 3px var(--color-panel-2)` }}
              initial={{ left: "0%", opacity: 0 }}
              animate={{ left: ["0%", "100%"], opacity: [0, 1, 1, 0] }}
              transition={{ duration: 0.9, ease: EASE_OUT, times: [0, 0.15, 0.8, 1] }}
            />
          )}
        </div>
        <LinkNode icon={<ShieldCheck strokeWidth={1.75} />} label="Tripwire" sub="checkpoint" i={1} />
      </div>
    </div>
  );
}

/* ------------------------------------------------------------------------------------------------
 * Pi: the prevent -> trip -> trace -> cure loop drawn as a ring that draws itself, arc by arc.
 * Stage markers are labelled steps of the method, not agents. "Cure" turns ok green only when a
 * guardrail was really approved this session (`cured`); `redraw` replays the drawing on each new
 * approval.
 * ---------------------------------------------------------------------------------------------- */
const R = 44;
const GAP = 14;
const STAGES = [
  { label: "Prevent", deg: -90, x: 0, y: -R - 12, anchor: "middle" },
  { label: "Trip", deg: 0, x: R + 12, y: 4, anchor: "start" },
  { label: "Trace", deg: 90, x: 0, y: R + 19, anchor: "middle" },
  { label: "Cure", deg: 180, x: -R - 12, y: 4, anchor: "end" },
] as const;
const rad = (d: number) => (d * Math.PI) / 180;
const pt = (d: number) => [R * Math.cos(rad(d)), R * Math.sin(rad(d))] as const;
function arcPath(from: number) {
  const [x1, y1] = pt(from + GAP);
  const [x2, y2] = pt(from + 90 - GAP);
  return `M ${x1.toFixed(2)} ${y1.toFixed(2)} A ${R} ${R} 0 0 1 ${x2.toFixed(2)} ${y2.toFixed(2)}`;
}

export function LoopArt({ cured, redraw }: { cured: boolean; redraw: number }) {
  const reduce = useReducedMotion();
  const STEP = 0.26;
  return (
    <div className="absolute inset-0 flex items-center justify-center">
      <svg key={redraw} viewBox="-100 -74 200 148" className="h-full max-h-[148px] w-full overflow-visible">
        <circle r={R} fill="var(--color-panel)" fillOpacity={0.7} />
        <g className="opacity-80 transition-opacity duration-300 group-hover/card:opacity-100">
          {STAGES.map((s, i) => {
            const end = s.deg + 90 - GAP;
            const [ax, ay] = pt(end);
            const delay = 0.2 + i * STEP;
            return (
              <g key={s.label}>
                <motion.path
                  d={arcPath(s.deg)}
                  fill="none"
                  stroke="var(--color-held)"
                  strokeOpacity={0.55}
                  strokeWidth={1.25}
                  strokeLinecap="round"
                  initial={reduce ? false : { pathLength: 0 }}
                  animate={{ pathLength: 1 }}
                  transition={{ duration: STEP, ease: "easeInOut", delay }}
                />
                <g transform={`translate(${ax.toFixed(2)} ${ay.toFixed(2)}) rotate(${end + 90})`}>
                  <motion.path
                    d="M -2.6 -2.6 L 2.6 0 L -2.6 2.6"
                    fill="none"
                    stroke="var(--color-held)"
                    strokeOpacity={0.7}
                    strokeWidth={1.25}
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    initial={reduce ? false : { opacity: 0 }}
                    animate={{ opacity: 1 }}
                    transition={{ duration: 0.18, delay: delay + STEP }}
                  />
                </g>
              </g>
            );
          })}
        </g>
        {STAGES.map((s, i) => {
          const [cx, cy] = pt(s.deg);
          const lit = s.label === "Cure" && cured;
          const c = lit ? "var(--color-ok)" : "var(--color-held)";
          return (
            <motion.g
              key={s.label}
              initial={reduce ? false : { opacity: 0 }}
              animate={{ opacity: 1 }}
              transition={{ duration: 0.3, delay: 0.1 + i * STEP }}
            >
              <circle cx={cx} cy={cy} r={6} fill="var(--color-panel)" stroke={lit ? c : "var(--color-line-strong)"} strokeWidth={1.25} />
              <circle cx={cx} cy={cy} r={2.4} fill={c} />
              <text
                x={s.x}
                y={s.y}
                textAnchor={s.anchor}
                fontSize={11}
                fontWeight={500}
                className="font-sans"
                fill={lit ? "var(--color-ok)" : "var(--color-muted)"}
              >
                {s.label}
              </text>
            </motion.g>
          );
        })}
      </svg>
      <span className="absolute grid size-10 place-items-center rounded-full border border-held-line bg-panel text-held shadow-sm [&_svg]:size-4">
        <Lightbulb strokeWidth={1.75} />
      </span>
    </div>
  );
}
