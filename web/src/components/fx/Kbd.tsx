import type * as React from "react";
import { cn } from "../../lib/utils";

/**
 * Keyboard hint chip: <Kbd>R</Kbd>, <Kbd>⌘K</Kbd>. Inherits the surrounding text colour, so it reads
 * right on white, on a tint, and inside a solid indigo button.
 */
export function Kbd({ className, ...props }: React.ComponentProps<"kbd">) {
  return <kbd className={cn("kbd ml-1 opacity-90", className)} {...props} />;
}
