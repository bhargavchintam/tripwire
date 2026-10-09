// ⌘K / Ctrl+K command palette. Commands are built in App.tsx and call the same functions as the
// buttons and single-key shortcuts, so a palette run and a shortcut press behave identically.
// Look: white panel, shadow-lg, search field on top, grouped results with icon tiles + kbd hints,
// the active row is an indigo tint that slides between rows (shared layoutId spring).
import { Fragment, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent, type ReactNode } from "react";
import { Dialog } from "radix-ui";
import { AnimatePresence, motion } from "motion/react";
import { CornerDownLeft, Search, type LucideIcon } from "lucide-react";
import { Badge, type BadgeVariant } from "./ui/badge";
import { Kbd } from "./ui/button";
import { EASE_OUT, SPRING } from "./fx";
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

function terms(query: string): string[] {
  return query.toLowerCase().split(/\s+/).filter(Boolean);
}

function matches(c: PaletteCommand, ts: string[]): boolean {
  const hay = `${c.group} ${c.label} ${c.keywords ?? ""}`.toLowerCase();
  return ts.every((t) => hay.includes(t));
}

/** Label with the typed terms emphasised (ink + 600 on an indigo whisper). */
function Highlight({ text, ts }: { text: string; ts: string[] }): ReactNode {
  if (!ts.length) return text;
  const lower = text.toLowerCase();
  const marks = new Array<boolean>(text.length).fill(false);
  for (const t of ts) {
    let from = 0;
    for (let i = lower.indexOf(t, from); i !== -1; i = lower.indexOf(t, from)) {
      for (let k = i; k < i + t.length; k++) marks[k] = true;
      from = i + t.length;
    }
  }
  const out: ReactNode[] = [];
  let i = 0;
  while (i < text.length) {
    const on = marks[i];
    let j = i;
    while (j < text.length && marks[j] === on) j++;
    const chunk = text.slice(i, j);
    out.push(
      on ? (
        <mark key={i} className="rounded-[3px] bg-brand-soft font-semibold text-fg">
          {chunk}
        </mark>
      ) : (
        <Fragment key={i}>{chunk}</Fragment>
      ),
    );
    i = j;
  }
  return out;
}

/** Tone of a command's live-state badge. Hold ON = held; the current tab = brand (active nav). */
function detailVariant(c: PaletteCommand): BadgeVariant {
  if (c.id === "hold" && c.detail === "ON") return "held";
  if (c.detail === "current") return "brand";
  return "muted";
}

/** Key hint pill (no leading margin, so it lines up in rows and the footer). */
function Key({ children, className }: { children: string; className?: string }) {
  return <Kbd className={cn("ml-0 opacity-100", className)}>{children}</Kbd>;
}

function PaletteBody({ commands, onClose }: { commands: PaletteCommand[]; onClose: () => void }) {
  const [query, setQuery] = useState("");
  const [active, setActive] = useState(0);
  const listRef = useRef<HTMLDivElement>(null);
  const baseId = useId();
  const ts = useMemo(() => terms(query), [query]);
  const filtered = useMemo(() => commands.filter((c) => matches(c, ts)), [commands, ts]);
  const idx = Math.min(active, filtered.length - 1);
  const optionId = (i: number) => `${baseId}-opt-${i}`;
  const groupId = (g: string) => `${baseId}-g-${g.replace(/[^a-zA-Z0-9_-]/g, "_")}`;

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
      {/* Search field */}
      <div className="flex items-center gap-3 border-b border-line px-5">
        <Search className="size-[18px] shrink-0 text-dim" strokeWidth={1.75} />
        <input
          autoFocus
          value={query}
          onChange={(e) => {
            setQuery(e.target.value);
            setActive(0);
          }}
          onKeyDown={onKeyDown}
          placeholder="Search commands…"
          role="combobox"
          aria-label="Search commands"
          aria-expanded="true"
          aria-controls={`${baseId}-list`}
          aria-activedescendant={idx >= 0 ? optionId(idx) : undefined}
          aria-autocomplete="list"
          spellCheck={false}
          autoComplete="off"
          // Inline: the app-wide unlayered :focus-visible ring would otherwise box the field; the open
          // palette + caret already show focus.
          style={{ outline: "none" }}
          className="h-14 min-w-0 flex-1 bg-transparent text-[16px] text-fg placeholder:text-dim"
        />
        <Key className="text-dim">esc</Key>
      </div>

      {/* Results */}
      <motion.div
        layoutScroll
        ref={listRef}
        id={`${baseId}-list`}
        role="listbox"
        aria-label="Commands"
        className="relative max-h-[min(56vh,440px)] overflow-y-auto overscroll-contain px-2 pb-2"
      >
        {filtered.length === 0 && (
          <div className="flex animate-pop-in flex-col items-center gap-2 px-3 py-10 text-center">
            <span className="grid size-10 place-items-center rounded-[10px] border border-line bg-panel-2 text-dim">
              <Search className="size-[18px]" strokeWidth={1.75} />
            </span>
            <p className="text-[14px] text-fg">No command matches “{query}”</p>
            <p className="text-[13px] text-dim">Try a tab name, “replay”, “hold” or “presenter”.</p>
          </div>
        )}
        {groups.map(([group, items]) => (
          <div key={group} role="group" aria-labelledby={groupId(group)}>
            <div
              id={groupId(group)}
              className="sticky top-0 z-10 bg-panel/95 px-3 pb-1.5 pt-3 text-[12px] font-medium text-dim backdrop-blur-sm"
            >
              {group}
            </div>
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
                  style={{ animationDelay: `${Math.min(i, 10) * 18}ms` }}
                  className={cn(
                    "relative isolate flex h-11 animate-pop-in cursor-pointer select-none items-center gap-3 rounded-[10px] px-2.5 text-[14px] text-fg",
                    "transition-colors duration-150",
                  )}
                >
                  {on && (
                    <motion.span
                      layoutId={`${baseId}-active`}
                      aria-hidden
                      className="absolute inset-0 -z-10 rounded-[10px] bg-brand-soft ring-1 ring-inset ring-brand-line/60"
                      transition={SPRING}
                    />
                  )}
                  <span
                    className={cn(
                      "grid size-7 shrink-0 place-items-center rounded-lg border transition-colors duration-150 [&_svg]:size-4",
                      on ? "border-brand-line bg-panel text-brand" : "border-line bg-panel-2 text-muted",
                    )}
                  >
                    <Icon strokeWidth={1.75} />
                  </span>
                  <span className={cn("min-w-0 truncate", !on && "text-fg/90")}>
                    <Highlight text={c.label} ts={ts} />
                  </span>
                  {c.detail && (
                    <Badge variant={detailVariant(c)} className={cn("shrink-0", c.id === "restore" && "font-mono")}>
                      {c.detail}
                    </Badge>
                  )}
                  <span className="ml-auto flex shrink-0 items-center gap-2 pl-2">
                    {c.shortcut && <Key className={on ? "text-brand" : "text-muted"}>{c.shortcut}</Key>}
                    <CornerDownLeft
                      aria-hidden
                      strokeWidth={1.75}
                      className={cn(
                        "size-4 text-brand transition-[opacity,transform] duration-150",
                        on ? "translate-x-0 opacity-100" : "-translate-x-1 opacity-0",
                      )}
                    />
                  </span>
                </div>
              );
            })}
          </div>
        ))}
      </motion.div>

      {/* Footer: keyboard hints */}
      <div className="flex flex-wrap items-center gap-x-5 gap-y-1.5 border-t border-line bg-panel-2 px-5 py-2.5 text-[12px] text-muted">
        <span className="inline-flex items-center gap-1.5">
          <Key>↑</Key>
          <Key>↓</Key>
          <span className="ml-0.5">navigate</span>
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Key>↵</Key> run
        </span>
        <span className="inline-flex items-center gap-1.5">
          <Key>esc</Key> close
        </span>
        <span className="ml-auto inline-flex items-center gap-1.5 text-dim">
          <span className="tabular-nums">
            {filtered.length} of {commands.length}
          </span>
          <span aria-hidden>·</span>
          <Key>{PALETTE_KEY_HINT}</Key> toggles
        </span>
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
      <AnimatePresence>
        {open && (
          <Dialog.Portal forceMount>
            <Dialog.Overlay asChild forceMount>
              <motion.div
                className="fixed inset-0 z-40 bg-[rgb(21_22_26/0.22)] backdrop-blur-[6px]"
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0 }}
                transition={{ duration: 0.2, ease: EASE_OUT }}
              />
            </Dialog.Overlay>
            <Dialog.Content asChild forceMount aria-describedby={undefined}>
              <motion.div
                className={cn(
                  "fixed left-1/2 top-[14vh] z-50 flex w-[calc(100%-2rem)] max-w-[640px] flex-col overflow-hidden",
                  "rounded-[var(--radius-card)] border border-line bg-panel text-fg shadow-pop outline-none",
                )}
                style={{ x: "-50%" }}
                initial={{ opacity: 0, y: -10, scale: 0.98 }}
                animate={{ opacity: 1, y: 0, scale: 1 }}
                exit={{ opacity: 0, y: -6, scale: 0.985, transition: { duration: 0.14, ease: EASE_OUT } }}
                transition={{ ...SPRING, opacity: { duration: 0.18, ease: EASE_OUT } }}
              >
                <Dialog.Title className="sr-only">Command palette</Dialog.Title>
                {/* Mounted per open, so the filter and selection start fresh each time. */}
                <PaletteBody commands={commands} onClose={() => onOpenChange(false)} />
              </motion.div>
            </Dialog.Content>
          </Dialog.Portal>
        )}
      </AnimatePresence>
    </Dialog.Root>
  );
}
