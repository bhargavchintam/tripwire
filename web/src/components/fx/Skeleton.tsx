import type * as React from "react";
import { cn } from "../../lib/utils";

/**
 * Shimmer placeholder. Show it ONLY while a request / lazy chunk is really in flight, and size it like
 * the content it stands in for. Never use it as decoration or to fake activity.
 *   <Skeleton className="h-4 w-24" />   <Skeleton className="h-40 rounded-[var(--radius-card)]" />
 */
export function Skeleton({ className, ...props }: React.ComponentProps<"div">) {
  return <div aria-hidden className={cn("skeleton h-4 w-full", className)} {...props} />;
}

/** Loading state for a whole tab: title block + a bento of card skeletons. */
export function TabSkeleton({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" aria-label={label} className="flex flex-col gap-6 pt-2">
      <div className="flex flex-col gap-3">
        <Skeleton className="h-3.5 w-40" />
        <Skeleton className="h-10 w-80 max-w-full" />
        <Skeleton className="h-4 w-[28rem] max-w-full" />
      </div>
      <div className="grid grid-cols-12 gap-4">
        <Skeleton className="col-span-12 h-36 rounded-[var(--radius-card)] md:col-span-4" />
        <Skeleton className="col-span-12 h-36 rounded-[var(--radius-card)] md:col-span-4" />
        <Skeleton className="col-span-12 h-36 rounded-[var(--radius-card)] md:col-span-4" />
        <Skeleton className="col-span-12 h-64 rounded-[var(--radius-card)] lg:col-span-8" />
        <Skeleton className="col-span-12 h-64 rounded-[var(--radius-card)] lg:col-span-4" />
      </div>
    </div>
  );
}
