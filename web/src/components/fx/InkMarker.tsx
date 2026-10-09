import type { CSSProperties, ReactNode } from "react";
import { toneVar, type Tone } from "./tone";

/**
 * Subtle highlighter stroke behind its children, in the tone's colour at low strength (not neon).
 * - Sweeps on mount, and re-sweeps whenever `trigger` changes (pass the real value, e.g. the number).
 * - `active={false}` draws nothing: use it when the value is unmeasured (DASH).
 */
export function InkMarker({
  children,
  tone = "held",
  trigger,
  active = true,
  delay = 0,
  strength = 40,
  className = "",
}: {
  children: ReactNode;
  tone?: Tone;
  trigger?: unknown;
  active?: boolean;
  /** Seconds. */
  delay?: number;
  /** Relative strength (legacy scale 0-100, default 40 ≈ an 18% tint). */
  strength?: number;
  className?: string;
}) {
  const pct = tone === "paper" ? 100 : Math.max(8, Math.min(20, Math.round(strength * 0.4)));
  const style = { "--ink": toneVar[tone], "--ink-strength": `${pct}%`, animationDelay: `${delay}s` } as CSSProperties;
  return (
    <span className={`ink ${className}`}>
      {active && <span aria-hidden key={String(trigger)} className="ink-mark" style={style} />}
      {children}
    </span>
  );
}
