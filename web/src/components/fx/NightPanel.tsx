import type * as React from "react";
import { cn } from "../../lib/utils";
import { Starfield } from "./Starfield";

/**
 * The ONE dark "night sky" moment on a light page (Live tab fleet-flow / orbit). Deep ink card with a
 * faint twinkling star field + grain scoped INSIDE it. Everything inside uses the night palette
 * automatically (text-fg is light, text-ok is a brighter green, border-line is dark, toneVar adapts).
 *   <NightPanel className="min-h-[360px] p-6"><FleetOrbit … /></NightPanel>
 */
export function NightPanel({
  className,
  stars = true,
  starCount = 70,
  children,
  ...props
}: React.ComponentProps<"div"> & { stars?: boolean; starCount?: number }) {
  return (
    <div className={cn("theme-night inset-night", className)} {...props}>
      {stars && <Starfield count={starCount} />}
      {children}
    </div>
  );
}
