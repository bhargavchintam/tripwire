import type * as React from "react";
import { Slot } from "radix-ui";
import { ArrowUpRight } from "lucide-react";
import { cva, type VariantProps } from "class-variance-authority";
import { cn } from "../../lib/utils";
export { Kbd } from "../fx/Kbd";

const buttonVariants = cva(
  [
    "group/btn relative inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-[var(--radius-control)] text-sm font-medium tracking-[-0.01em] cursor-pointer select-none",
    "transition-[background-color,border-color,color,box-shadow,transform] duration-200 ease-out",
    "motion-safe:hover:-translate-y-px motion-safe:active:translate-y-0 motion-safe:active:scale-[0.98]",
    "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-panel",
    "disabled:pointer-events-none disabled:opacity-45 [&_svg]:size-4 [&_svg]:shrink-0",
  ].join(" "),
  {
    variants: {
      variant: {
        /** Primary: solid signal indigo. One per view. Add <ButtonArrow /> as the last child for the arrow chip. */
        default:
          "bg-brand text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.18),0_1px_2px_rgb(21_22_26/0.12),0_6px_16px_-8px_rgb(61_58_232/0.55)] hover:bg-brand-hover",
        /** Secondary: white with hairline + shadow-sm. */
        outline:
          "border border-line bg-panel text-fg shadow-sm hover:border-line-strong hover:shadow-md",
        /** Alias of outline. */
        secondary:
          "border border-line bg-panel text-fg shadow-sm hover:border-line-strong hover:shadow-md",
        ghost: "text-muted hover:bg-panel-3 hover:text-fg",
        /** Destructive / attack CTA: solid state red, white text. */
        danger:
          "bg-bad text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.16),0_1px_2px_rgb(21_22_26/0.12),0_6px_16px_-8px_rgb(200_40_30/0.5)] hover:bg-[#B0221A]",
        /** State variants: soft tints with a tinted hairline. */
        held: "tint-held border shadow-sm hover:border-held/60",
        ok: "tint-ok border shadow-sm hover:border-ok/60",
        bad: "tint-bad border shadow-sm hover:border-bad/60",
        model: "tint-model border shadow-sm hover:border-model/60",
        /** Warm paper (editorial accent, not a state). */
        paper: "border border-paper-2 bg-paper text-ink shadow-sm hover:bg-paper-2",
        /** Text link in brand indigo. */
        link: "h-auto px-0 text-brand underline-offset-4 hover:underline motion-safe:hover:translate-y-0",
      },
      size: {
        sm: "h-8 px-3 text-[13px] [&_svg]:size-3.5",
        default: "h-9 px-3.5",
        lg: "h-11 px-5 text-base",
        icon: "size-9 p-0",
      },
    },
    defaultVariants: { variant: "default", size: "default" },
  },
);

export type ButtonVariant = NonNullable<VariantProps<typeof buttonVariants>["variant"]>;

export function Button({
  className,
  variant,
  size,
  asChild = false,
  ...props
}: React.ComponentProps<"button"> & VariantProps<typeof buttonVariants> & { asChild?: boolean }) {
  const Comp = asChild ? Slot.Root : "button";
  return <Comp className={cn(buttonVariants({ variant, size }), className)} {...props} />;
}

/**
 * Arrow chip at the end of a CTA: <Button>Get started <ButtonArrow /></Button>.
 * Nudges diagonally on hover. Pass `icon` to swap the arrow.
 */
export function ButtonArrow({ icon, className }: { icon?: React.ReactNode; className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "-mr-1.5 ml-0.5 inline-flex size-5 items-center justify-center rounded-md bg-current/12 [&_svg]:size-3.5",
        "transition-transform duration-200 motion-safe:group-hover/btn:translate-x-0.5 motion-safe:group-hover/btn:-translate-y-0.5",
        className,
      )}
    >
      {icon ?? <ArrowUpRight />}
    </span>
  );
}
