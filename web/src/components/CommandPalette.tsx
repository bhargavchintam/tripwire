// ⌘K / Ctrl+K command palette. Commands are built in App.tsx and call the same functions as the
// buttons and single-key shortcuts, so a palette run and a shortcut press behave identically.
import { useEffect, useId, useMemo, useRef, useState, type KeyboardEvent } from "react";
import { Dialog } from "radix-ui";
import { Search, type LucideIcon } from "lucide-react";
import { Badge } from "./ui/badge";
import { cn } from "../lib/utils";

export interface PaletteCommand {
  id: string;
  group: string;
  label: string;
  icon: LucideIcon;
  /** Existing single-key shortcut, shown as a hint. */
  shortcut?: string;
  /** Short live state next to the label (e.g. hold ON/OFF); omitted when unknown. */
  detail?: string;
  /** Extra words the filter matches on. */
  keywords?: string;
  run: () => void;
}

export const PALETTE_KEY_HINT =
  typeof navigator !== "undefined" && /Mac|iPhone|iPad/.test(navigator.userAgent) ? "⌘K" : "Ctrl K";

function matches(c: PaletteCommand, query: string): boolean {
  const hay = `${c.group} ${c.label} ${c.keywords ?? ""}`.toLowerCase();
  return query
    .toLowerCase()
    .split(/\s+/)
    .filter(Boolean)
    .every((t) => hay.includes(t));
}

function PaletteBody({ commands, onClose }: { commands: PaletteCommand[]; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const baseId = useId();
  const filtered = useMemo(() => commands.filter((c) => matches(c, query)), [commands, query]);
  const idx = Math.min(active, filtered.length - 1);
  const optionId = (i: number) => `${baseId}-opt-${i}`;

  // Groups keep the order commands were given in; options index into the flat filtered list.
  const groups = useMemo(() => {
    const m = new Map<string, PaletteCommand[]>();
    for (const c of filtered) m.set(c.group, [...(m.get(c.group) ?? []), c]);
    return [...m];
  }, [filtered]);

  useEffect(() => {
    listRef.current?.querySelector('[data-active="true"]')?.scrollIntoView({ block: "nearest" });
  }, [idx]);

  const run = (c: PaletteCommand | undefined) => {
    if (!c) return;
    onClose();
    c.run();
  };

  const onKeyDown = (e: KeyboardEvent<HTMLInputElement>) => {
    const n = filtered.length;
    if (e.key === "ArrowDown" && n) setActive((idx + 1) % n);
    else if (e.key === "ArrowUp" && n) setActive((idx - 1 + n) % n);
    else if (e.key === "Home" && n) setActive(0);
    else if (e.key === "End" && n) setActive(n - 1);
    else if (e.key === "Enter" && !e.nativeEvent.isComposing) run(filtered[idx]);
    else return;
    e.preventDefault();
  };

  return (
    <>
      <div className="flex items-center gap-2 border-b border-line px-4">
        <Search className="size-4 shrink-0 text-dim" />
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Type a command…"
          role="combobox"
          aria-expanded="true"
          aria-controls={`${baseId}-list`}
          aria-activedescendant={idx >= 0 ? optionId(idx) : undefined}
          aria-autocomplete="list"
          spellCheck={false}
          className="h-12 w-full bg-transparent text-sm text-fg outline-none placeholder:text-dim"
        />
      </div>
      <div
        ref={listRef}
        id={`${baseId}-list`}
        role="listbox"
        aria-label="Commands"
        className="max-h-[min(60vh,420px)] overflow-y-auto p-1.5"
      >
        {filtered.length === 0 && (
          <div className="px-3 py-6 text-center text-sm text-dim">No command matches “{query}”</div>
        )}
        {groups.map(([group, items]) => (
          <div key={group} role="group" aria-label={group}>
            <div className="px-2.5 pb-1 pt-2 text-[11px] font-semibold uppercase tracking-wider text-dim">{group}</div>
            {items.map((c) => {
              const i = filtered.indexOf(c);
              const on = i === idx;
              const Icon = c.icon;
              return (
                <div
                  key={c.id}
                  id={optionId(i)}
                  role="option"
                  aria-selected={on}
                  data-active={on}
                  // Keep focus in the input so Up/Down/Enter keep working after a hover or click.
                  onMouseDown={(e) => e.preventDefault()}
                  onMouseMove={() => i !== idx && setActive(i)}
                  onClick={() => run(c)}
                  className={cn(
                    "flex cursor-pointer items-center gap-2.5 rounded-md px-2.5 py-2 text-sm text-muted",
                    on && "bg-panel-2 text-fg shadow-[inset_0_0_0_1px_var(--color-line)]",
                  )}
                >
                  <Icon className="size-4 shrink-0" />
                  <span className="truncate">{c.label}</span>
                  {c.detail && (
                    <Badge variant="muted" className="font-mono">
                      {c.detail}
                    </Badge>
                  )}
                  {c.shortcut && (
                    <kbd className="ml-auto rounded bg-black/25 px-1.5 font-mono text-xs text-muted">{c.shortcut}</kbd>
                  )}
                </div>
              );
            })}
          </div>
        ))}
      </div>
      <div className="flex items-center gap-3 border-t border-line px-4 py-2 font-mono text-[11px] text-dim">
        <span>↑↓ navigate · ↵ run · esc close</span>
        <span className="ml-auto">{PALETTE_KEY_HINT} toggles</span>
      </div>
    </>
  );
}

export function CommandPalette({
  open,
  onOpenChange,
  commands,
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  commands: PaletteCommand[];
}) {
  return (
    <Dialog.Root open={open} onOpenChange={onOpenChange}>
      <Dialog.Portal>
        <Dialog.Overlay className="fixed inset-0 z-40 bg-black/60 backdrop-blur-[1px]" />
        <Dialog.Content
          aria-describedby={undefined}
          className="fixed left-1/2 top-[12vh] z-50 flex w-[calc(100%-2rem)] max-w-xl -translate-x-1/2 flex-col overflow-hidden rounded-xl border border-line bg-panel shadow-2xl outline-none"
        >
          <Dialog.Title className="sr-only">Command palette</Dialog.Title>
          {/* Mounted per open, so the filter and selection start fresh each time. */}
          <PaletteBody commands={commands} onClose={() => onOpenChange(false)} />
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
