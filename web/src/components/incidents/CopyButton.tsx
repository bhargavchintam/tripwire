import { useEffect, useState, type MouseEvent } from "react";
import { Check, Copy } from "lucide-react";
import { toast } from "sonner";
import { Tooltip } from "../ui/tooltip";
import { cn } from "../../lib/utils";

/**
 * Small icon button that copies a block of text (SQL, a report) and toasts "Copied".
 * For ids use fx/CopyId instead (it shows the id itself).
 */
export function CopyButton({
  value,
  label = "Copy",
  className,
}: {
  value: string;
  /** Tooltip + aria label, e.g. "Copy SQL". */
  label?: string;
  className?: string;
}) {
  const [copied, setCopied] = useState(false);
  useEffect(() => {
    if (!copied) return;
    const t = window.setTimeout(() => setCopied(false), 1200);
    return () => window.clearTimeout(t);
  }, [copied]);

  const onClick = async (e: MouseEvent) => {
    e.stopPropagation();
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      toast.success("Copied", { duration: 1600 });
    } catch {
      toast.error("Couldn't copy to clipboard");
    }
  };

  return (
    <Tooltip content={copied ? "Copied" : label}>
      <button
        type="button"
        onClick={onClick}
        aria-label={label}
        className={cn(
          "inline-flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-lg text-dim",
          "transition-[color,background-color,transform] duration-150 hover:bg-panel-3 hover:text-fg active:scale-95",
          "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand",
          className,
        )}
      >
        {copied ? (
          <Check className="size-3.5 text-ok" strokeWidth={2} />
        ) : (
          <Copy className="size-3.5" strokeWidth={1.75} />
        )}
      </button>
    </Tooltip>
  );
}
