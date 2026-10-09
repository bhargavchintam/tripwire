import type { CSSProperties } from "react";
import { toneVar, type Tone } from "./tone";

/**
 * A 1px hairline that draws itself left -> right on mount (CSS `.rule-draw`, 600 ms ease-out).
 * Re-mount it (change its `key`) to redraw on a REAL state change. Timelines: use it as the spine.
 */
export function RuleDraw({
  tone = "neutral",
  delay = 0,
  className = "",
}: {
  tone?: Tone;
  /** Seconds. */
  delay?: number;
  className?: string;
}) {
  const style = {
    animationDelay: `${delay}s`,
    ...(tone !== "neutral" ? { "--rule": `color-mix(in oklab, ${toneVar[tone]} 55%, transparent)` } : {}),
  } as CSSProperties;
  return <span aria-hidden className={`rule-draw ${className}`} style={style} />;
}
