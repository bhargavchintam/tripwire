import type * as React from "react";
import { Tabs as TabsPrimitive } from "radix-ui";
import { cn } from "../../lib/utils";

export const Tabs = TabsPrimitive.Root;

/** Floating nav pill: translucent white, hairline, shadow-sm, backdrop blur. */
export function TabsList({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.List>) {
  return (
    <TabsPrimitive.List
      className={cn("glass inline-flex items-center gap-0.5 rounded-full border border-line p-1", className)}
      {...props}
    />
  );
}

/**
 * Tab pill. Active = indigo-tint pill + indigo text. The shell (App.tsx) renders a motion `layoutId`
 * pill behind the active trigger and passes `data-[state=active]:bg-transparent` so the pill glides.
 */
export function TabsTrigger({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Trigger>) {
  return (
    <TabsPrimitive.Trigger
      className={cn(
        "relative isolate inline-flex h-9 cursor-pointer items-center gap-2 rounded-full px-3.5 text-sm font-medium text-muted transition-colors duration-200",
        "hover:text-fg data-[state=active]:bg-brand-soft data-[state=active]:text-brand",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-1 focus-visible:ring-offset-panel",
        "[&_svg]:size-4 [&_svg]:shrink-0",
        className,
      )}
      {...props}
    />
  );
}

export function TabsContent({ className, ...props }: React.ComponentProps<typeof TabsPrimitive.Content>) {
  return (
    <TabsPrimitive.Content
      className={cn("mt-4 outline-none data-[state=active]:animate-[fade-in_0.24s_ease-out_both]", className)}
      {...props}
    />
  );
}
