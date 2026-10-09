import type { ReactNode } from "react";
import { Info } from "lucide-react";
import { Tooltip } from "../ui/tooltip";
import { cn } from "../../lib/utils";

const SIZE = {
  /** Hero number: 32 -> 44px, shrinks with the viewport so nothing clips at 1280x720. */
  xl: "text-[clamp(32px,3vw,44px)]",
  lg: "text-[clamp(28px,2.6vw,40px)]",
  md: "text-[28px]",
} as const;

/**
 * One measured number with its label, unit and an honest caption.
 * - `label`: 12px mono eyebrow with a 14px lucide icon.
 * - `children`: the value (use <Count>, <MsCount>, <PctCount> so unmeasured renders DASH).
 * - `source`: shows a small info button whose tooltip reads "Source: …".
 * - `caption`: one 12px muted line under the number (what it is, never a claim).
 */
export function Metric({
  icon,
  label,
  children,
  caption,
  source,
  size = "lg",
  valueClassName,
  className,
}: {
  icon?: ReactNode;
  label: ReactNode;
  children: ReactNode;
  caption?: ReactNode;
  source?: ReactNode;
  size?: keyof typeof SIZE;
  valueClassName?: string;
  className?: string;
}) {
  return (
    <div className={cn("group/metric flex min-w-0 flex-col", className)}>
      <div className="flex min-h-5 items-center gap-1.5">
        <span className="eyebrow inline-flex min-w-0 items-center gap-1.5 truncate [&_svg]:size-3.5 [&_svg]:shrink-0 [&_svg]:stroke-[1.75]">
          {icon}
          {label}
        </span>
        {source ? (
          <Tooltip content={<>Source: {source}</>} side="top" align="start">
            <button
              type="button"
              aria-label="Source"
              className="-m-1 inline-flex size-6 shrink-0 items-center justify-center rounded-md text-dim opacity-60 transition-[opacity,background-color,color] duration-150 hover:bg-panel-3 hover:text-fg hover:opacity-100 focus-visible:opacity-100 group-hover/metric:opacity-100"
            >
              <Info className="size-3.5" strokeWidth={1.75} />
            </button>
          </Tooltip>
        ) : null}
      </div>
      <div className={cn("num-display mt-3 whitespace-nowrap text-fg", SIZE[size], valueClassName)}>{children}</div>
      {caption ? <p className="mt-2 text-[12px] leading-[1.45] text-muted">{caption}</p> : null}
    </div>
  );
}

/** Small unit after a display number ("ms", "%", "s"). */
export function Unit({ children, className }: { children: ReactNode; className?: string }) {
  return <span className={cn("ml-1 text-[0.42em] font-medium tracking-normal text-muted", className)}>{children}</span>;
}
