import type { ReactNode } from "react";
import { motion, type HTMLMotionProps } from "motion/react";

/** Shared motion spec: calm, physical. Import these instead of inventing new curves. */
export const SPRING = { type: "spring", stiffness: 300, damping: 30, mass: 1 } as const;
export const SPRING_SOFT = { type: "spring", stiffness: 260, damping: 32, mass: 1 } as const;
export const EASE_OUT = [0.22, 1, 0.36, 1] as const;
/** Stagger step between siblings (seconds) and its cap. */
export const STAGGER = 0.04;
export const STAGGER_CAP = 0.36;

type RevealProps = Omit<HTMLMotionProps<"div">, "initial" | "animate" | "whileInView" | "children"> & {
  children?: ReactNode;
  /** Stagger slot: delay = delay + index * 40 ms (capped at 360 ms). */
  index?: number;
  /** Extra delay in seconds. */
  delay?: number;
  /** Rise distance in px (default 8). */
  y?: number;
  /** "view" (default): plays once when scrolled into view. "mount": plays on mount. */
  trigger?: "view" | "mount";
};

/**
 * Rise + fade, once per view. Pure presentation: it never implies activity. Reduced motion: the
 * shell's <MotionConfig reducedMotion="user"> drops the transform, leaving a quick fade.
 * Put grid spans on Reveal itself: <Reveal index={1} className="col-span-12 lg:col-span-4">…</Reveal>
 */
export function Reveal({ children, index = 0, delay = 0, y = 8, trigger = "view", transition, ...rest }: RevealProps) {
  const from = { opacity: 0, y };
  const to = { opacity: 1, y: 0 };
  const d = delay + Math.min(index * STAGGER, STAGGER_CAP);
  const t = transition ?? {
    y: { ...SPRING_SOFT, delay: d },
    opacity: { duration: 0.24, ease: EASE_OUT, delay: d },
  };
  return trigger === "mount" ? (
    <motion.div initial={from} animate={to} transition={t} {...rest}>
      {children}
    </motion.div>
  ) : (
    <motion.div initial={from} whileInView={to} viewport={{ once: true, amount: 0.1 }} transition={t} {...rest}>
      {children}
    </motion.div>
  );
}
