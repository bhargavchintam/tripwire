import type * as React from "react";
import { Tooltip as TooltipPrimitive } from "radix-ui";
import { cn } from "../../lib/utils";

export const TooltipProvider = TooltipPrimitive.Provider;

/**
 * Dark ink bubble (12px), pops in. Put one on every icon button and badge.
 * Content renders in the night token scope, so text-muted / text-ok / .kbd inside read correctly.
 *   <Tooltip content="Copy id"><button …/></Tooltip>
 *   <Tooltip content={<>Replay attack <span className="kbd">R</span></>} side="bottom">…</Tooltip>
 */
export function Tooltip({
  content,
  children,
  className,
  side = "top",
  align = "center",
  delay = 200,
}: {
  content: React.ReactNode;
  children: React.ReactNode;
  className?: string;
  side?: "top" | "right" | "bottom" | "left";
  align?: "start" | "center" | "end";
  /** Open delay in ms (default 200). */
  delay?: number;
}) {
  return (
    <TooltipPrimitive.Root delayDuration={delay}>
      <TooltipPrimitive.Trigger asChild>{children}</TooltipPrimitive.Trigger>
      <TooltipPrimitive.Portal>
        <TooltipPrimitive.Content
          side={side}
          align={align}
          sideOffset={8}
          collisionPadding={12}
          className={cn(
            "theme-night z-[80] max-w-xs animate-pop-in rounded-lg bg-[#15161a] px-2.5 py-1.5 text-[12px] font-normal leading-[1.45] text-fg normal-case tracking-normal shadow-lg",
            className,
          )}
        >
          {content}
          <TooltipPrimitive.Arrow width={10} height={5} className="fill-[#15161a]" />
        </TooltipPrimitive.Content>
      </TooltipPrimitive.Portal>
    </TooltipPrimitive.Root>
  );
}
