import type { ReactNode } from "react";
import { cn } from "../../lib/utils";
import { toneText, type Tone } from "./tone";

const SIZE = {
  sm: "text-[28px]",
  md: "text-[32px] md:text-[40px]",
  lg: "text-[40px] md:text-[48px]",
  xl: "text-[44px] md:text-[56px]",
  hero: "text-[clamp(2.5rem,5vw,3.5rem)]",
} as const;

/**
 * Serif page title with ONE italic word: <DisplayTitle pre="Live" em="fleet" /> renders "Live *fleet*".
 * Or pass children and use <em> yourself. The italic word stays ink by default (`emTone="paper"`);
 * only tint it with a state tone when the word IS that state.
 */
export function DisplayTitle({
  pre,
  em,
  post,
  children,
  as: Tag = "h2",
  size = "md",
  emTone = "paper",
  className,
}: {
  pre?: ReactNode;
  em?: ReactNode;
  post?: ReactNode;
  children?: ReactNode;
  as?: "h1" | "h2" | "h3" | "p" | "span" | "div";
  size?: keyof typeof SIZE;
  emTone?: Tone;
  className?: string;
}) {
  return (
    <Tag className={cn("display text-fg", SIZE[size], className)}>
      {children ?? (
        <>
          {pre}
          {pre && em ? " " : null}
          {em && <em className={toneText[emTone]}>{em}</em>}
          {post && em ? " " : null}
          {post}
        </>
      )}
    </Tag>
  );
}
