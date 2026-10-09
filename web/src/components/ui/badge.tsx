import type * as React from "react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";

/** Status pill: soft tint + tinted 1px border + state-coloured text. Pair colour with an icon or word. */
const badgeVariants = cva(
  "inline-flex h-[22px] items-center gap-1 whitespace-nowrap rounded-full border px-2 text-[12px] font-medium leading-none tracking-[-0.005em] [&_svg]:size-3 [&_svg]:shrink-0",
  {
    variants: {
      variant: {
        default: "border-line bg-panel text-fg shadow-sm",
        ok: "tint-ok",
        held: "tint-held",
        bad: "tint-bad",
        model: "tint-model",
        info: "tint-info",
        muted: "tint-neutral",
        paper: "border-paper-2 bg-paper text-ink",
        brand: "tint-brand",
      },
    },
    defaultVariants: { variant: "default" },
  },
);

export type BadgeVariant = NonNullable<VariantProps<typeof badgeVariants>["variant"]>;

export function Badge({
  className,
  variant,
  ...props
}: React.ComponentProps<"span"> & VariantProps<typeof badgeVariants>) {
  return <span className={cn(badgeVariants({ variant }), className)} {...props} />;
}
