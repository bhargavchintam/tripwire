import type * as React from "react";
import { Switch as SwitchPrimitive } from "radix-ui";
import { cn } from "../../lib/utils";

export function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        "peer inline-flex h-6 w-11 shrink-0 cursor-pointer items-center rounded-full border border-line transition-colors",
        "data-[state=checked]:bg-held data-[state=unchecked]:bg-panel-2",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-info/70 disabled:opacity-40",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-5 rounded-full bg-fg shadow transition-transform data-[state=checked]:translate-x-5 data-[state=unchecked]:translate-x-0.5" />
    </SwitchPrimitive.Root>
  );
}
