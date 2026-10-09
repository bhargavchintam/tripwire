import type * as React from "react";
import { motion } from "motion/react";
import { cn } from "../../lib/utils";
import { toneBorder, toneSoftVar, toneVar, type Tone } from "./tone";
import { GrainLayer } from "./Grain";
import { Starfield } from "./Starfield";
import { EASE_OUT, SPRING_SOFT, STAGGER, STAGGER_CAP } from "./Reveal";

export type GlowCardProps = Omit<React.ComponentProps<"div">, "title"> & {
  /** State of the card (tinted border + top accent bar). Pick it from REAL state (see DESIGN.md). */
  tone?: Tone;
  /**
   * How loudly the tone shows. "none" = plain card; "soft" = tinted hairline + 2px top accent bar;
   * "strong" = soft + a whisper of tint washing down + a 3px tint ring (the ONE card that matters now).
   */
  glow?: "none" | "soft" | "strong";
  /** Slow opacity breathing of the accent. Only while the state it encodes is really ongoing. */
  breathe?: boolean;
  /** Tint the 1px border with the tone (default true when tone is not neutral). */
  tintBorder?: boolean;
  /** Hover lift (1-2px) + shadow sm -> md, press scale. For clickable cards. */
  interactive?: boolean;
  /** Paper grain inside the card. */
  grain?: boolean;
  /** The ONE dark night-sky card (Live fleet-flow). Deep ink + scoped stars; night palette inside. */
  night?: boolean;
  /** Rise-in on mount with this stagger index (omit for no entrance animation). */
  reveal?: number;
  /** Small mono label above the title. */
  eyebrow?: React.ReactNode;
  /** Card title (Geist 600 16px; pass <DisplayTitle size="sm"/> for serif). */
  title?: React.ReactNode;
  /** One muted line under the title. */
  description?: React.ReactNode;
  /** Right side of the header row (buttons, badges). */
  actions?: React.ReactNode;
  /** Chips row, rendered above the header. */
  chips?: React.ReactNode;
  /** Bottom footer slot. */
  footer?: React.ReactNode;
  /** Class for the body wrapper around `children`. */
  bodyClassName?: string;
};

/**
 * Premium bento card: white surface, hairline border, 16px radius, layered soft shadow with a 1px
 * inner top highlight; optional state accent (tinted border + top bar), header / chips / footer slots,
 * hover lift. Use it for every card-like surface. Padding: 20px (px-5), header pt-5.
 */
export function GlowCard({
  tone = "neutral",
  glow = "soft",
  breathe = false,
  tintBorder,
  interactive = false,
  grain = false,
  night = false,
  reveal,
  eyebrow,
  title,
  description,
  actions,
  chips,
  footer,
  bodyClassName,
  className,
  children,
  style,
  ...props
}: GlowCardProps) {
  const lit = tone !== "neutral" && glow !== "none";
  const tinted = tintBorder ?? tone !== "neutral";
  const strong = lit && glow === "strong";
  const hasHeader = eyebrow || title || description || actions;
  const d = reveal === undefined ? 0 : Math.min(reveal * STAGGER, STAGGER_CAP);
  const motionProps =
    reveal === undefined
      ? {}
      : {
          initial: { opacity: 0, y: 8 },
          animate: { opacity: 1, y: 0 },
          transition: { y: { ...SPRING_SOFT, delay: d }, opacity: { duration: 0.24, ease: EASE_OUT, delay: d } },
        };
  const { onAnimationStart: _a, onDrag: _d, onDragStart: _ds, onDragEnd: _de, ...divProps } = props;
  const ringStyle: React.CSSProperties | undefined = strong
    ? { boxShadow: `0 0 0 3px ${toneSoftVar[tone]}, var(--shadow-card)`, ...style }
    : style;
  return (
    <motion.div
      {...motionProps}
      {...divProps}
      style={ringStyle}
      className={cn(
        "group/card relative isolate flex flex-col overflow-hidden rounded-[var(--radius-card)] border text-fg",
        night ? "theme-night inset-night" : "surface shadow-card",
        !night && (tinted ? toneBorder[tone] : "border-line"),
        interactive &&
          "cursor-pointer transition-[transform,box-shadow,border-color] duration-200 ease-out hover:shadow-lift motion-safe:hover:-translate-y-0.5 active:scale-[0.99] hover:border-line-strong focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand",
        className,
      )}
    >
      {night && <Starfield />}
      {strong && (
        <div
          aria-hidden
          className={cn("pointer-events-none absolute inset-x-0 top-0 -z-10 h-28", breathe && "animate-breathe")}
          style={{ background: `linear-gradient(180deg, ${toneSoftVar[tone]}, transparent)` }}
        />
      )}
      {lit && (
        <div
          aria-hidden
          className={cn("pointer-events-none absolute inset-x-0 top-0 h-[2px]", breathe && "animate-breathe")}
          style={{ background: toneVar[tone], opacity: strong ? 1 : 0.75 }}
        />
      )}
      {grain && <GrainLayer className="-z-10" />}
      {chips && <div className="flex flex-wrap items-center gap-1.5 px-5 pt-5">{chips}</div>}
      {hasHeader && (
        <div className={cn("flex items-start justify-between gap-3 px-5 pb-3", chips ? "pt-3" : "pt-5")}>
          <div className="min-w-0">
            {eyebrow && <div className="eyebrow mb-1.5 flex items-center gap-1.5 [&_svg]:size-3.5">{eyebrow}</div>}
            {title && <div className="text-base font-semibold tracking-[-0.01em] text-fg">{title}</div>}
            {description && <p className="mt-1 text-[13px] leading-normal text-muted">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 items-center gap-2">{actions}</div>}
        </div>
      )}
      {children !== undefined && children !== null && (
        <div className={cn("flex-1 px-5 pb-5", !hasHeader && !chips && "pt-5", bodyClassName)}>{children}</div>
      )}
      {footer && <div className="mt-auto px-5 pb-5">{footer}</div>}
    </motion.div>
  );
}
