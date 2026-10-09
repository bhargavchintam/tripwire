import { ChevronRight, FileCode } from "lucide-react";
import { cn } from "../../lib/utils";

/**
 * Collapsible SQL receipt: a quiet disclosure row that opens a raised mono well with the exact SQL
 * the checkpoint returned. Renders nothing when there is no SQL.
 */
export function SqlReceipt({
  sql,
  label = "SQL receipt",
  className,
  maxH = "max-h-64",
}: {
  sql: string | null | undefined;
  label?: string;
  className?: string;
  maxH?: string;
}) {
  if (!sql) return null;
  return (
    <details className={cn("group/sql", className)}>
      <summary
        className={cn(
          "inline-flex cursor-pointer select-none items-center gap-1.5 rounded-md py-1 pr-2 pl-1 text-[13px] font-medium text-muted",
          "transition-colors duration-150 hover:bg-panel-3 hover:text-fg [&::-webkit-details-marker]:hidden",
        )}
      >
        <ChevronRight
          className="size-3.5 transition-transform duration-200 ease-out group-open/sql:rotate-90"
          strokeWidth={1.75}
        />
        <FileCode className="size-3.5 text-info" strokeWidth={1.75} />
        {label}
      </summary>
      <pre
        className={cn(
          "surface-raised mt-2 overflow-auto px-3.5 py-3 font-mono text-[12px] leading-5 whitespace-pre-wrap break-words text-fg/85 animate-fade-in",
          maxH,
        )}
      >
        {sql}
      </pre>
    </details>
  );
}
