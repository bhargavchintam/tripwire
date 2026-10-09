import type * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

const chipVariants = cva(
  "inline-flex items-center gap-1.5 whitespace-nowrap rounded-full border font-medium leading-none [&_svg]:shrink-0",
  {
    variants: {
      tone: {
        /** White chip with hairline + shadow-sm (the default "fact" chip). */
        glass: "border-line bg-panel text-fg shadow-sm",
        /** Warm paper chip (editorial, not a state). */
        paper: "border-paper-2 bg-paper text-ink",
        neutral: "tint-neutral",
        ok: "tint-ok",
        held: "tint-held",
        bad: "tint-bad",
        model: "tint-model",
        info: "tint-info",
      },
      size: {
        sm: "h-6 px-2.5 text-[12px] [&_svg]:size-3",
        md: "h-7 px-3 text-[13px] [&_svg]:size-3.5",
      },
      mono: { true: "font-mono tracking-normal", false: "" },
    },
    defaultVariants: { tone: "glass", size: "sm", mono: false },
  },
);

export type ChipTone = NonNullable<VariantProps<typeof chipVariants>["tone"]>;

/**
 * Small pill stating a fact that is TRUE right now (agent id, sponsor, decision_source, model id).
 * State tones are soft tints with a tinted 1px border. `dot` adds a dot coloured like the text.
 * Keep chips scarce: max ~3 per card.
 */
export function Chip({
  className,
  tone,
  size,
  mono,
  dot = false,
  icon,
  children,
  ...props
}: React.ComponentProps<"span"> &
  VariantProps<typeof chipVariants> & { dot?: boolean; icon?: React.ReactNode }) {
  return (
    <span className={cn(chipVariants({ tone, size, mono }), className)} {...props}>
      {dot && <span aria-hidden className="size-1.5 rounded-full bg-current" />}
      {icon}
      {children}
    </span>
  );
}
