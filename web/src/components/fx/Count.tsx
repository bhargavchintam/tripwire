import NumberFlow from "@number-flow/react";
import { DASH, isNum } from "../../lib/format";
import { cn } from "../../lib/utils";

const SPIN = { duration: 520, easing: "cubic-bezier(0.22, 1, 0.36, 1)" } as const;
const FADE = { duration: 220, easing: "ease-out" } as const;

/**
 * Honest count-up number: rolls digits only when the real `value` changes.
 * Unmeasured (null / undefined / NaN) renders the DASH "—" in text-dim, never 0.
 * NumberFlow already respects prefers-reduced-motion.
 *   <Count value={ev?.hold_decision_ms} suffix=" ms" />
 *   <Count value={ev?.cost_akashml} digits={4} prefix="$" />
 *   <Count value={n} className="num-display text-[40px]" />   // display number
 */
export function Count({
  value,
  digits = 0,
  minDigits = 0,
  prefix,
  suffix,
  className,
}: {
  value: number | null | undefined;
  digits?: number;
  minDigits?: number;
  prefix?: string;
  suffix?: string;
  className?: string;
}) {
  if (!isNum(value)) return <span className={cn("text-dim", className)}>{DASH}</span>;
  return (
    <NumberFlow
      className={cn("tabular-nums", className)}
      value={value}
      format={{ maximumFractionDigits: digits, minimumFractionDigits: minDigits }}
      prefix={prefix}
      suffix={suffix}
      spinTiming={SPIN}
      transformTiming={SPIN}
      opacityTiming={FADE}
      willChange
    />
  );
}
