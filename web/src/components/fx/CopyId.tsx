import { useEffect, useState, type MouseEvent, type ReactNode } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { cn } from "../../lib/utils";
import { Tooltip } from "../ui/tooltip";

/**
 * Click-to-copy id (incident ids, agent ids, run ids). Mono text + a copy glyph that appears on hover;
 * on click copies `value`, swaps to a check for 1.2 s and toasts "Copied". Stops propagation so it is
 * safe inside clickable rows/cards.
 *   <CopyId value={inc.id} />   <CopyId value={agent} label={<b>{agent}</b>} />
 */
export function CopyId({
  value,
  label,
  className,
  iconClassName,
}: {
  value: string;
  /** What to show (default: the value itself, mono). */
  label?: ReactNode;
  className?: string;
  iconClassName?: string;
}) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(t);
  }, [copied]);

  const onClick = async (e: MouseEvent) => {
    e.stopPropagation();
    e.preventDefault();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success("Copied", { description: value, duration: 1800 });
    } catch {
      toast.error("Couldn't copy to clipboard");
    }
  };

  return (
    <Tooltip content={copied ? "Copied" : "Copy id"}>
      <button
        type="button"
        onClick={onClick}
        onKeyDown={(e) => e.stopPropagation()}
        aria-label={`Copy ${value}`}
        className={cn(
          "group/copy -mx-1 inline-flex max-w-full cursor-copy items-center gap-1.5 rounded-md px-1 py-0.5 font-mono text-[12px] text-fg",
          "transition-colors duration-150 hover:bg-panel-3 focus-visible:bg-panel-3",
          className,
        )}
      >
        <span className="truncate">{label ?? value}</span>
        {copied ? (
          <Check className={cn("size-3.5 shrink-0 text-ok", iconClassName)} strokeWidth={2} />
        ) : (
          <Copy
            className={cn(
              "size-3.5 shrink-0 text-dim opacity-0 transition-opacity duration-150 group-hover/copy:opacity-100 group-focus-visible/copy:opacity-100",
              iconClassName,
            )}
            strokeWidth={1.75}
          />
        )}
      </button>
    </Tooltip>
  );
}
