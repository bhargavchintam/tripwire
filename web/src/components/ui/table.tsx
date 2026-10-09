import type * as React from "react";
import { cn } from "../../lib/utils";

/**
 * Light table: quiet sentence-case headers on a raised strip, hairline row dividers, whisper zebra,
 * hover highlight. Numbers are tabular by default. Row borders live on the cells (border-separate).
 */
export function Table({ className, ...props }: React.ComponentProps<"table">) {
  return (
    <div className="w-full overflow-x-auto">
      <table
        className={cn("w-full caption-bottom border-separate border-spacing-0 text-sm tabular-nums", className)}
        {...props}
      />
    </div>
  );
}
export function TableHeader({ className, ...props }: React.ComponentProps<"thead">) {
  return <thead className={cn("[&_tr]:bg-transparent [&_tr]:hover:bg-transparent", className)} {...props} />;
}
export function TableBody({ className, ...props }: React.ComponentProps<"tbody">) {
  return <tbody className={className} {...props} />;
}
export function TableRow({ className, ...props }: React.ComponentProps<"tr">) {
  return (
    <tr
      className={cn(
        "transition-colors duration-150 even:bg-[rgb(21_22_26/0.014)] hover:bg-panel-3/70 [&>td]:border-b [&>td]:border-line",
        className,
      )}
      {...props}
    />
  );
}
export function TableHead({ className, ...props }: React.ComponentProps<"th">) {
  return (
    <th
      className={cn(
        "h-10 border-b border-line bg-panel-2 px-3 text-left align-middle text-[12px] font-medium whitespace-nowrap text-dim first:pl-5 last:pr-5",
        className,
      )}
      {...props}
    />
  );
}
export function TableCell({ className, ...props }: React.ComponentProps<"td">) {
  return <td className={cn("px-3 py-2.5 align-middle first:pl-5 last:pr-5", className)} {...props} />;
}
