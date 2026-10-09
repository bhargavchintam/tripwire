import type { ReactNode } from "react";
import { motion } from "motion/react";
import { EASE_OUT, SPRING_SOFT, STAGGER, STAGGER_CAP } from "../fx";
import { cn } from "../../lib/utils";

/**
 * Header row of a section inside the incident sheet: mono eyebrow with a 16px icon on the left,
 * optional meta / status on the right.
 */
export function SectionHead({
  icon,
  label,
  meta,
  className,
}: {
  icon?: ReactNode;
  label: ReactNode;
  meta?: ReactNode;
  className?: string;
}) {
  return (
    <div className={cn("mb-4 flex min-h-7 flex-wrap items-center justify-between gap-x-3 gap-y-2", className)}>
      <h3 className="eyebrow flex items-center gap-2 text-muted [&_svg]:size-4 [&_svg]:text-dim">
        {icon}
        {label}
      </h3>
      {meta ? <div className="flex flex-wrap items-center gap-2">{meta}</div> : null}
    </div>
  );
}

/**
 * One section of the incident sheet: 24px side padding, hairline divider above (not on the first),
 * `data-section` so the sheet's section nav can find and track it. `reveal` = stagger index of a
 * once-only rise-in (on mount for the first sections, `revealOn="view"` for the ones further down).
 */
export function SheetSection({
  id,
  icon,
  label,
  meta,
  reveal,
  revealOn = "mount",
  children,
  className,
}: {
  id: string;
  icon?: ReactNode;
  label?: ReactNode;
  meta?: ReactNode;
  reveal?: number;
  revealOn?: "mount" | "view";
  children: ReactNode;
  className?: string;
}) {
  const d = reveal === undefined ? 0 : Math.min(reveal * STAGGER, STAGGER_CAP);
  const t = { y: { ...SPRING_SOFT, delay: d }, opacity: { duration: 0.24, ease: EASE_OUT, delay: d } };
  const motionProps =
    reveal === undefined
      ? {}
      : revealOn === "view"
        ? { initial: { opacity: 0, y: 8 }, whileInView: { opacity: 1, y: 0 }, viewport: { once: true, amount: 0.05 }, transition: t }
        : { initial: { opacity: 0, y: 8 }, animate: { opacity: 1, y: 0 }, transition: t };
  return (
    <motion.section
      {...motionProps}
      data-section={id}
      aria-label={typeof label === "string" ? label : undefined}
      className={cn("border-t border-line px-6 py-6 first:border-t-0", className)}
    >
      {label ? <SectionHead icon={icon} label={label} meta={meta} /> : null}
      {children}
    </motion.section>
  );
}
