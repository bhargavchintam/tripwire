import { Command, Hand, LoaderCircle, Presentation, TriangleAlert, WifiOff } from "lucide-react";
import { toast } from "sonner";
import { Segmented } from "./ui/segmented";
import { Tooltip } from "./ui/tooltip";
import { Count, Kbd } from "./fx";
import { api } from "../lib/api";
import { isNum } from "../lib/format";
import { cn } from "../lib/utils";
import { useTripwire } from "../hooks/useTripwire";
import { usePresenter } from "../hooks/usePresenter";

/** PUT /config hold_enabled = next (the server's answer wins). Same toasts as before. */
export async function setHoldTo(next: boolean, setHold: (v: boolean) => void) {
  try {
    const res = await api.setHold(next);
    const value = typeof res.hold_enabled === "boolean" ? res.hold_enabled : next;
    setHold(value);
    toast(value ? "Hold mode ON — risky actions wait for a verdict" : "Hold mode OFF — detect after the fact");
  } catch (e) {
    toast.error(`Hold toggle failed: ${(e as Error).message}`);
  }
}

export async function toggleHold(current: boolean | null, setHold: (v: boolean) => void) {
  return setHoldTo(!current, setHold);
}

/** Shared shape of every header control (white, hairline, shadow-sm, lifts on hover). */
const CONTROL =
  "inline-flex h-9 shrink-0 items-center gap-2 whitespace-nowrap rounded-full border border-line bg-panel px-3 text-[13px] font-medium text-muted shadow-sm " +
  "transition-[color,border-color,box-shadow,transform,background-color] duration-200 ease-out hover:border-line-strong hover:text-fg hover:shadow-md motion-safe:active:scale-[0.98] " +
  "cursor-pointer focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg [&_svg]:size-4 [&_svg]:shrink-0";

/** Connection state of the live SSE stream (real state from useTripwire; the ring only pulses while live). */
export function LivePill() {
  const { state } = useTripwire();
  if (state.connection === "live")
    return (
      <Tooltip content="Streaming tool-call events from the checkpoint (SSE)">
        <span className="tint-ok inline-flex h-9 shrink-0 items-center gap-2 rounded-full border px-3 shadow-sm">
          <span className="relative flex size-2">
            <span className="absolute inline-flex size-full animate-pulse-ring rounded-full bg-ok" />
            <span className="relative inline-flex size-2 rounded-full bg-ok" />
          </span>
          <span className="font-mono text-[12px] font-medium tracking-[0.08em]">LIVE</span>
        </span>
      </Tooltip>
    );
  const connecting = state.connection === "connecting";
  return (
    <Tooltip content={connecting ? "Opening the event stream…" : "Stream dropped, retrying"}>
      <span className="tint-held inline-flex h-9 shrink-0 items-center gap-2 rounded-full border px-3 shadow-sm">
        {connecting ? <LoaderCircle className="size-3.5 animate-spin" /> : <WifiOff className="size-3.5" />}
        <span className="font-mono text-[12px] font-medium tracking-[0.08em]">
          {connecting ? "CONNECTING" : "RECONNECTING"}
        </span>
      </span>
    </Tooltip>
  );
}

/** Hold mode as an ON/OFF segmented control (real state from GET/PUT config; unknown = no thumb). */
export function HoldPill() {
  const { state, setHold } = useTripwire();
  const on = state.holdEnabled === true;
  const value = state.holdEnabled === null ? null : on ? "on" : "off";
  return (
    <Tooltip content="When ON, high-risk actions (http_post, assume_role, disable_logging) wait for a verdict before they run. Shortcut: H">
      <div
        className={cn(
          "inline-flex h-9 shrink-0 items-center gap-2 rounded-full border bg-panel pl-3 pr-[3px] text-[13px] font-medium shadow-sm transition-colors duration-200",
          on ? "border-held-line" : "border-line",
        )}
      >
        <Hand className={cn("size-4 transition-colors duration-200", on ? "text-held" : "text-dim")} strokeWidth={1.75} />
        <span className={on ? "text-fg" : "text-muted"}>Hold</span>
        <Segmented
          label="Hold mode"
          value={value}
          onChange={(v) => setHoldTo(v === "on", setHold)}
          options={[
            { value: "off", label: "OFF" },
            { value: "on", label: "ON", tone: "held" },
          ]}
          className="border-transparent"
        />
      </div>
    </Tooltip>
  );
}

/** Presenter-mode toggle (icon-only while presenting to keep the header tight). */
export function PresenterPill() {
  const presenter = usePresenter();
  return (
    <Tooltip content="Presenter mode: bigger type, hides the event table and secondary panels. Shortcut: P">
      <button
        onClick={presenter.toggle}
        aria-pressed={presenter.on}
        aria-label="Presenter mode"
        className={cn(CONTROL, presenter.on && "tint-brand border hover:border-brand-line hover:text-brand")}
      >
        <Presentation strokeWidth={1.75} />
        {!presenter.on && <span className="hidden min-[1100px]:inline">Presenter</span>}
        <Kbd className="ml-0">P</Kbd>
      </button>
    </Tooltip>
  );
}

/** Opens the command palette. */
export function CommandsPill({ onClick, hint }: { onClick: () => void; hint: string }) {
  return (
    <Tooltip content="Command palette: replay, restore, reset, modes, tabs">
      <button onClick={onClick} aria-label="Commands" className={CONTROL}>
        <Command strokeWidth={1.75} />
        <span className="sr-only min-[1360px]:not-sr-only">Commands</span>
        <Kbd className="ml-0">{hint}</Kbd>
      </button>
    </Tooltip>
  );
}

/** Tripwire mark: a wire between two posts with a trip in it. Brand indigo tile, white glyph. */
export function LogoMark({ className }: { className?: string }) {
  return (
    <span
      aria-hidden
      className={cn(
        "inline-flex size-8 shrink-0 items-center justify-center rounded-[9px] bg-brand text-white shadow-[inset_0_1px_0_rgb(255_255_255/0.22),0_1px_2px_rgb(21_22_26/0.14),0_4px_10px_-4px_rgb(61_58_232/0.5)]",
        className,
      )}
    >
      <svg viewBox="0 0 24 24" className="size-[18px]" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round">
        <path d="M3.5 6v12M20.5 6v12" />
        <path d="M3.5 12h4.5l1.6-3.2 2.8 6.4 1.6-3.2h6.5" />
      </svg>
    </span>
  );
}

/**
 * MOCK DATA warning. Rendered by App as a direct child of the full-height shell so it stays sticky
 * (visible) while scrolling. Honesty-critical: keep it loud.
 */
export function MockBanner() {
  const { state } = useTripwire();
  if (!state.isMock) return null;
  return (
    <div
      role="alert"
      className="sticky top-0 z-[60] flex h-9 items-center justify-center gap-2 bg-held px-4 text-[13px] font-semibold tracking-[-0.005em] text-white shadow-sm"
    >
      <TriangleAlert className="size-4" strokeWidth={2} />
      MOCK DATA — synthetic events from tripwire/mock_server.py, not the live checkpoint
      <TriangleAlert className="size-4" strokeWidth={2} />
    </div>
  );
}

/**
 * App bar: logo mark + "Tripwire" wordmark + mono eyebrow on the left; live events/s, connection,
 * Hold mode, Presenter and ⌘K on the right. The floating tab nav (sticky) lives in App.
 */
export function Header({ onOpenCommands, commandsHint }: { onOpenCommands?: () => void; commandsHint?: string } = {}) {
  const { state } = useTripwire();
  const presenter = usePresenter();
  // Mock sends events_per_s at the top level; real heartbeats nest it under Heartbeat.metrics.
  const eps = state.metrics?.events_per_s ?? state.metrics?.metrics?.events_per_s;
  return (
    <header className="relative border-b border-line bg-panel/70">
      <div
        className={cn(
          "mx-auto flex max-w-[1440px] flex-wrap items-center justify-between gap-x-6 gap-y-3 px-6",
          presenter.on ? "py-2.5" : "py-3.5",
        )}
      >
        <div className="flex min-w-0 items-center gap-3 animate-fade-in">
          <LogoMark />
          <span className="text-[20px] font-semibold leading-none tracking-[-0.03em] text-fg">Tripwire</span>
          {!presenter.on && (
            <>
              <span aria-hidden className="hidden h-4 w-px bg-line-strong md:block" />
              <span className="eyebrow hidden md:inline">The immune system for AI-agent fleets</span>
            </>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2 animate-fade-in">
          {isNum(eps) && (
            <Tooltip content="Tool-call events per second, from the checkpoint heartbeat">
              <span className="mr-1 hidden items-baseline gap-1.5 text-[13px] text-muted sm:inline-flex">
                <Count value={eps} digits={1} minDigits={1} className="font-mono text-[13px] font-medium text-fg" />
                events/s
              </span>
            </Tooltip>
          )}
          <LivePill />
          <HoldPill />
          <PresenterPill />
          {!presenter.on && onOpenCommands && <CommandsPill onClick={onOpenCommands} hint={commandsHint ?? "⌘K"} />}
        </div>
      </div>
    </header>
  );
}
