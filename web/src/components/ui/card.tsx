import type * as React from "react";
import { cn } from "../../lib/utils";

/**
 * Base card: white surface, hairline border, 16px radius, shadow-card (sm + inner top highlight).
 * For a state accent / chips / hover lift use fx/GlowCard.
 */
export function Card({ className, ...props }: React.ComponentProps<"div">) {
  return (
    <div
      className={cn("relative rounded-[var(--radius-card)] border border-line surface text-fg shadow-card", className)}
      {...props}
    />
  );
}
export function CardHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex items-center justify-between gap-3 px-5 pt-5 pb-3", className)} {...props} />;
}
/** Card title: Geist 600 14px ink; a leading lucide icon renders 16px in tertiary ink. */
export function CardTitle({ className, ...props }: React.ComponentProps<"h3">) {
  return (
    <h3
      className={cn(
        "flex items-center gap-2 text-sm font-semibold tracking-[-0.01em] text-fg [&_svg]:size-4 [&_svg]:text-dim",
        className,
      )}
      {...props}
    />
  );
}
export function CardDescription({ className, ...props }: React.ComponentProps<"p">) {
  return <p className={cn("text-[13px] text-muted", className)} {...props} />;
}
export function CardContent({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("px-5 pb-5", className)} {...props} />;
}
export function CardFooter({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("flex items-center gap-2 border-t border-line px-5 py-3", className)} {...props} />;
}
