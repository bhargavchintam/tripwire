import type { ReactNode } from "react";
import { cn } from "../../lib/utils";
import { toneText, toneVar, type Tone } from "./tone";

/**
 * Mono uppercase label (12px, 0.08em tracking, tertiary ink). `size="lg"` = 13px for page headers.
 * `dot` adds a small static tone dot in front (a legend, not a heartbeat).
 */
export function Eyebrow({
  children,
  icon,
  tone,
  dot = false,
  size = "sm",
  as: Tag = "div",
  className,
}: {
  children: ReactNode;
  icon?: ReactNode;
  tone?: Tone;
  dot?: boolean;
  size?: "sm" | "lg";
  as?: "div" | "span" | "p" | "h2" | "h3" | "h4";
  className?: string;
}) {
  return (
    <Tag
      className={cn(
        "eyebrow inline-flex items-center gap-2 [&_svg]:size-3.5 [&_svg]:shrink-0",
        size === "lg" && "eyebrow-lg",
        tone && toneText[tone],
        className,
      )}
    >
      {dot && (
        <span
          aria-hidden
          className="size-1.5 shrink-0 rounded-full"
          style={{ background: tone ? toneVar[tone] : "var(--color-line-strong)" }}
        />
      )}
      {icon}
      {children}
    </Tag>
  );
}
