import { Count } from "../fx";
import { isNum } from "../../lib/format";
import { cn } from "../../lib/utils";

/**
 * `fmtMs` as an honest count-up: < 10 ms → 1 decimal, < 10 000 ms → whole ms, ≥ 10 000 ms → seconds
 * with 1 decimal (the same unit switch fmtMs makes). Unmeasured renders the DASH with no unit.
 * The unit is a separate, smaller span so big numbers stay readable on a projector.
 */
export function MsCount({
  value,
  className,
  unitClassName = "ml-1 text-[0.42em] font-medium tracking-normal text-muted",
}: {
  value: number | null | undefined;
  className?: string;
  unitClassName?: string;
}) {
  if (!isNum(value)) return <Count value={null} className={className} />;
  const secs = value >= 10_000;
  const shown = secs ? value / 1000 : value;
  const digits = secs || value < 10 ? 1 : 0;
  return (
    <span className={cn("whitespace-nowrap", className)}>
      <Count value={shown} digits={digits} minDigits={digits} />
      <span className={unitClassName}>{secs ? "s" : "ms"}</span>
    </span>
  );
}
