import type * as React from "react";
import { Switch as SwitchPrimitive } from "radix-ui";
import { cn } from "../../lib/utils";

/** Light toggle. Checked = held amber (Hold mode semantics). For ON/OFF modes prefer <Segmented>. */
export function Switch({ className, ...props }: React.ComponentProps<typeof SwitchPrimitive.Root>) {
  return (
    <SwitchPrimitive.Root
      className={cn(
        "peer relative inline-flex h-6 w-10 shrink-0 cursor-pointer items-center rounded-full border transition-[background-color,border-color] duration-200",
        "data-[state=checked]:border-held data-[state=checked]:bg-held",
        "data-[state=unchecked]:border-line-strong data-[state=unchecked]:bg-panel-3",
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-panel disabled:opacity-45",
        className,
      )}
      {...props}
    >
      <SwitchPrimitive.Thumb className="pointer-events-none block size-[18px] rounded-full bg-white shadow-[0_1px_2px_rgb(21_22_26/0.2),0_1px_1px_rgb(21_22_26/0.06)] transition-transform duration-200 ease-[var(--ease-out-soft)] data-[state=checked]:translate-x-[18px] data-[state=unchecked]:translate-x-[2px]" />
    </SwitchPrimitive.Root>
  );
}
