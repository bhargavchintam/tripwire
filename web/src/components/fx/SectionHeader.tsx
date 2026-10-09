import type { ReactNode } from "react";
import { cn } from "../../lib/utils";
import { DisplayTitle } from "./DisplayTitle";
import { Eyebrow } from "./Eyebrow";
import { RuleDraw } from "./RuleDraw";
import type { Tone } from "./tone";

/**
 * Standard top-of-tab header: 13px mono eyebrow, Instrument Serif title (one italic word), one muted
 * line, optional actions on the right (baseline-aligned), and a hairline that draws itself underneath.
 *   <SectionHeader eyebrow="Live · tool-call stream" pre="Live" em="fleet"
 *     description="Every agent tool call, checked in ClickHouse as it happens." actions={<Button…/>} />
 */
export function SectionHeader({
  eyebrow,
  eyebrowTone,
  pre,
  em,
  post,
  title,
  description,
  actions,
  size = "md",
  rule = true,
  className,
}: {
  eyebrow?: ReactNode;
  eyebrowTone?: Tone;
  pre?: ReactNode;
  em?: ReactNode;
  post?: ReactNode;
  /** Full custom title node (overrides pre/em/post). */
  title?: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  size?: "sm" | "md" | "lg";
  rule?: boolean;
  className?: string;
}) {
  return (
    <div className={cn("flex flex-col gap-5", className)}>
      <div className="flex flex-wrap items-end justify-between gap-x-8 gap-y-4">
        <div className="min-w-0 animate-rise-in">
          {eyebrow && (
            <Eyebrow size="lg" dot={!!eyebrowTone} tone={eyebrowTone} className="mb-3">
              {eyebrow}
            </Eyebrow>
          )}
          {title ?? <DisplayTitle as="h1" pre={pre} em={em} post={post} size={size} />}
          {description && <p className="mt-2.5 max-w-2xl text-[15px] leading-relaxed text-muted">{description}</p>}
        </div>
        {actions && (
          <div className="flex flex-wrap items-center gap-2 animate-rise-in [animation-delay:60ms]">{actions}</div>
        )}
      </div>
      {rule && <RuleDraw delay={0.08} />}
    </div>
  );
}
