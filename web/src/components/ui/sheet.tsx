import type * as React from "react";
import { Dialog as SheetPrimitive } from "radix-ui";
import { X } from "lucide-react";
import { cn } from "../../lib/utils";
import { Tooltip } from "./tooltip";

export const Sheet = SheetPrimitive.Root;
export const SheetClose = SheetPrimitive.Close;

/** Right-side white panel (shadow-lg) that slides + fades in over a soft blurred backdrop. */
export function SheetContent({
  className,
  children,
  onOpenAutoFocus,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Content>) {
  return (
    <SheetPrimitive.Portal>
      <SheetPrimitive.Overlay className="fixed inset-0 z-40 bg-[rgb(21_22_26/0.22)] backdrop-blur-[6px] data-[state=closed]:animate-overlay-out data-[state=open]:animate-overlay-in" />
      <SheetPrimitive.Content
        className={cn(
          "fixed inset-y-2 right-2 z-50 flex w-[calc(100%-1rem)] max-w-2xl flex-col overflow-hidden rounded-[20px] border border-line bg-panel text-fg shadow-pop outline-none",
          "data-[state=closed]:animate-sheet-out data-[state=open]:animate-sheet-in",
          className,
        )}
        onOpenAutoFocus={(e) => {
          onOpenAutoFocus?.(e);
          if (e.defaultPrevented) return;
          // Focus the panel itself (not its first button) so no tooltip pops open on arrival; Esc still closes.
          e.preventDefault();
          (e.target as HTMLElement | null)?.focus?.({ preventScroll: true });
        }}
        {...props}
      >
        {children}
        <Tooltip content={<>Close <span className="kbd ml-1">Esc</span></>} side="left">
          <SheetPrimitive.Close
            className="absolute right-4 top-4 z-10 inline-flex size-9 cursor-pointer items-center justify-center rounded-[var(--radius-control)] border border-line bg-panel text-muted shadow-sm transition-[color,border-color,box-shadow,transform] duration-200 hover:border-line-strong hover:text-fg hover:shadow-md active:scale-[0.96] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand"
            aria-label="Close"
          >
            <X className="size-4" strokeWidth={1.75} />
          </SheetPrimitive.Close>
        </Tooltip>
      </SheetPrimitive.Content>
    </SheetPrimitive.Portal>
  );
}

export function SheetHeader({ className, ...props }: React.ComponentProps<"div">) {
  return <div className={cn("relative border-b border-line px-6 py-5 pr-16", className)} {...props} />;
}
export function SheetTitle({ className, ...props }: React.ComponentProps<typeof SheetPrimitive.Title>) {
  return <SheetPrimitive.Title className={cn("text-xl font-semibold tracking-[-0.015em]", className)} {...props} />;
}
export function SheetDescription({
  className,
  ...props
}: React.ComponentProps<typeof SheetPrimitive.Description>) {
  return <SheetPrimitive.Description className={cn("mt-1 text-sm text-muted", className)} {...props} />;
}
