import { useId, type ReactNode } from "react";
import { motion } from "motion/react";
import { cn } from "../../lib/utils";
import type { Tone } from "../fx/tone";

const THUMB: Record<Tone | "brand", string> = {
  ok: "bg-ok-soft ring-1 ring-ok-line",
  held: "bg-held-soft ring-1 ring-held-line",
  bad: "bg-bad-soft ring-1 ring-bad-line",
  model: "bg-model-soft ring-1 ring-model-line",
  info: "bg-info-soft ring-1 ring-info-line",
  paper: "bg-paper ring-1 ring-paper-2",
  neutral: "bg-panel shadow-sm ring-1 ring-line",
  brand: "bg-brand-soft ring-1 ring-brand-line",
};
const TEXT: Record<Tone | "brand", string> = {
  ok: "text-ok",
  held: "text-held",
  bad: "text-bad",
  model: "text-model",
  info: "text-info",
  paper: "text-ink",
  neutral: "text-fg",
  brand: "text-brand",
};

export type SegmentedOption<T extends string> = {
  value: T;
  label: ReactNode;
  /** Tone of the sliding thumb when this option is selected (default neutral = white thumb). */
  tone?: Tone | "brand";
  icon?: ReactNode;
};

/**
 * Segmented control with a sliding thumb (shared layoutId spring). `value={null}` = unknown: no thumb.
 * Radio semantics (arrow keys move, Enter/Space select). Selecting the current option is a no-op.
 *   <Segmented label="Hold mode" value={on ? "on" : "off"} onChange={set}
 *     options={[{ value: "off", label: "OFF" }, { value: "on", label: "ON", tone: "held" }]} />
 */
export function Segmented<T extends string>({
  options,
  value,
  onChange,
  label,
  size = "sm",
  className,
}: {
  options: SegmentedOption<T>[];
  value: T | null;
  onChange: (v: T) => void;
  label: string;
  size?: "sm" | "md";
  className?: string;
}) {
  const id = useId();
  return (
    <div
      role="radiogroup"
      aria-label={label}
      className={cn(
        "relative inline-flex items-center gap-0.5 rounded-full border border-line bg-panel-3 p-0.5",
        className,
      )}
      onKeyDown={(e) => {
        if (e.key !== "ArrowLeft" && e.key !== "ArrowRight") return;
        e.preventDefault();
        const i = Math.max(0, options.findIndex((o) => o.value === value));
        const next = options[(i + (e.key === "ArrowRight" ? 1 : options.length - 1)) % options.length];
        if (next && next.value !== value) onChange(next.value);
      }}
    >
      {options.map((o) => {
        const active = o.value === value;
        const tone = o.tone ?? "neutral";
        return (
          <button
            key={o.value}
            type="button"
            role="radio"
            aria-checked={active}
            tabIndex={active || (value === null && o === options[0]) ? 0 : -1}
            onClick={() => !active && onChange(o.value)}
            className={cn(
              "relative isolate inline-flex cursor-pointer items-center justify-center gap-1.5 rounded-full font-mono font-medium tracking-[0.04em] transition-colors duration-200",
              "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
              size === "sm" ? "h-7 min-w-11 px-2.5 text-[12px]" : "h-8 min-w-12 px-3 text-[13px]",
              "[&_svg]:size-3.5",
              active ? TEXT[tone] : "text-dim hover:text-fg",
            )}
          >
            {active && (
              <motion.span
                layoutId={`seg-thumb-${id}`}
                aria-hidden
                className={cn("absolute inset-0 -z-10 rounded-full", THUMB[tone])}
                transition={{ type: "spring", stiffness: 320, damping: 30 }}
              />
            )}
            {o.icon}
            {o.label}
          </button>
        );
      })}
    </div>
  );
}
