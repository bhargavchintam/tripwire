import { lazy, Suspense, useCallback, useEffect, useRef, useState, type CSSProperties, type ReactNode } from "react";
import {
  Activity,
  BookCheck,
  Grid3x3,
  Hand,
  Handshake,
  Play,
  Presentation,
  RotateCcw,
  ScrollText,
  Siren,
  Undo2,
} from "lucide-react";
import { toast } from "sonner";
import { MotionConfig, motion } from "motion/react";
import "./fonts";
import { Header, MockBanner, toggleHold } from "./components/Header";
import { Grain, TabSkeleton } from "./components/fx";
import { Tooltip } from "./components/ui/tooltip";
import { IncidentSheet } from "./components/IncidentSheet";
import { restoreAgent } from "./components/AgentCard";
import { CommandPalette, PALETTE_KEY_HINT, type PaletteCommand } from "./components/CommandPalette";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "./components/ui/tabs";
import { LiveTab } from "./tabs/LiveTab";
import { IncidentsTab } from "./tabs/IncidentsTab";
import { api } from "./lib/api";
import { TripwireContext, useTripwireStream, type TripwireCtx } from "./hooks/useTripwire";
import { PresenterContext, usePresenterState } from "./hooks/usePresenter";

// Heavy / secondary tabs load on first open so the main chunk stays small (Recharts lives in Evidence).
const EvidenceTab = lazy(() => import("./tabs/EvidenceTab"));
const FleetTab = lazy(() => import("./tabs/FleetTab"));
const PolicyTab = lazy(() => import("./tabs/PolicyTab"));
const SponsorsTab = lazy(() => import("./tabs/SponsorsTab"));

function Lazy({ children }: { children: ReactNode }) {
  return (
    <Suspense fallback={<TabSkeleton label="Loading tab" />}>
      {children}
    </Suspense>
  );
}

const TABS = [
  { value: "live", label: "Live", icon: Activity },
  { value: "incidents", label: "Incidents", icon: Siren },
  { value: "fleet", label: "Fleet", icon: Grid3x3 },
  { value: "evidence", label: "Evidence", icon: BookCheck },
  { value: "policy", label: "Policy", icon: ScrollText },
  { value: "sponsors", label: "Sponsors", icon: Handshake },
] as const;

/** POST /demo/replay for a recorded fixture (fixtures/<scenario>.json on the checkpoint). */
async function replayScenario(scenario: string, kind = "attack") {
  try {
    await api.replay(scenario);
    toast(`Replaying recorded ${kind}: ${scenario}`);
  } catch (e) {
    toast.error(`Replay failed: ${(e as Error).message}`);
  }
}

// Zero-arg on purpose: the Replay button passes its click event straight through.
const replay = () => replayScenario("secret_theft");

async function reset() {
  try {
    await api.reset();
    toast.success("Demo reset: fleet green");
  } catch (e) {
    toast.error(`Reset failed: ${(e as Error).message}`);
  }
}

function isTyping(t: EventTarget | null): boolean {
  const el = t as HTMLElement | null;
  if (!el) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

function lastQuarantined(state: TripwireCtx["state"]): string | undefined {
  return [...state.quarantineOrder].reverse().find((a) => state.modes[a] === "quarantined");
}

function restoreLastQuarantined(state: TripwireCtx["state"]) {
  const target = lastQuarantined(state);
  if (target) restoreAgent(target);
  else toast("No quarantined agent to restore");
}

/** Presenter shortcuts: R replay · X restore most recently quarantined · H hold · P presenter · 0 reset · ⌘K/Ctrl+K palette. */
function useShortcuts(
  ctx: TripwireCtx,
  togglePresenter: () => void,
  paletteOpen: boolean,
  setPaletteOpen: (open: boolean) => void,
) {
  const ref = useRef(ctx);
  ref.current = ctx;
  const presRef = useRef(togglePresenter);
  presRef.current = togglePresenter;
  const paletteRef = useRef({ open: paletteOpen, set: setPaletteOpen });
  paletteRef.current = { open: paletteOpen, set: setPaletteOpen };
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const palette = paletteRef.current;
      // ⌘K / Ctrl+K: opens from anywhere except a text field (Ctrl+K is kill-line in macOS inputs);
      // pressed again from the palette's own input, it closes.
      if ((e.metaKey || e.ctrlKey) && !e.altKey && !e.shiftKey && e.key.toLowerCase() === "k") {
        if (!palette.open && isTyping(e.target)) return;
        e.preventDefault();
        if (!e.repeat) palette.set(!palette.open);
        return;
      }
      if (palette.open || e.metaKey || e.ctrlKey || e.altKey || e.repeat || isTyping(e.target)) return;
      const { state, setHold } = ref.current;
      const k = e.key.toLowerCase();
      if (k === "r") replay();
      else if (k === "0") reset();
      else if (k === "h") toggleHold(state.holdEnabled, setHold);
      else if (k === "p") presRef.current();
      else if (k === "x") restoreLastQuarantined(state);
      else return;
      e.preventDefault();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);
}

export default function App() {
  const ctx = useTripwireStream();
  const presenter = usePresenterState();
  const [tab, setTab] = useState("live");
  const [openId, setOpenId] = useState<string | null>(null);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const open = useCallback((id: string) => setOpenId(id), []);
  useShortcuts(ctx, presenter.toggle, paletteOpen, setPaletteOpen);
  const openCount = Object.values(ctx.state.incidents).filter((i) => !i.closed_ms).length;

  const { state, setHold } = ctx;
  const onOff = (v: boolean | null) => (v === null ? undefined : v ? "ON" : "OFF");
  const commands: PaletteCommand[] = [
    {
      id: "replay:secret_theft",
      group: "Demo",
      label: "Replay secret_theft",
      icon: Play,
      shortcut: "R",
      keywords: "attack scenario",
      run: replay,
    },
    {
      id: "replay:honeytoken_exfil",
      group: "Demo",
      label: "Replay honeytoken_exfil",
      icon: Play,
      keywords: "attack scenario decoy",
      run: () => replayScenario("honeytoken_exfil"),
    },
    {
      id: "replay:normal_ops",
      group: "Demo",
      label: "Replay normal_ops",
      icon: Play,
      keywords: "benign scenario false positive",
      run: () => replayScenario("normal_ops", "benign run"),
    },
    {
      id: "restore",
      group: "Demo",
      label: "Restore last quarantined agent",
      icon: Undo2,
      shortcut: "X",
      detail: lastQuarantined(state),
      run: () => restoreLastQuarantined(state),
    },
    {
      id: "reset",
      group: "Demo",
      label: "Reset demo",
      icon: RotateCcw,
      shortcut: "0",
      keywords: "clear green",
      run: reset,
    },
    {
      id: "hold",
      group: "Modes",
      label: "Toggle hold mode",
      icon: Hand,
      shortcut: "H",
      detail: onOff(state.holdEnabled),
      keywords: "prevent block",
      run: () => toggleHold(state.holdEnabled, setHold),
    },
    {
      id: "presenter",
      group: "Modes",
      label: "Toggle presenter mode",
      icon: Presentation,
      shortcut: "P",
      detail: onOff(presenter.on),
      run: presenter.toggle,
    },
    ...TABS.map((t) => ({
      id: `tab:${t.value}`,
      group: "Go to",
      label: `Go to ${t.label}`,
      icon: t.icon,
      detail: tab === t.value ? "current" : undefined,
      keywords: "tab",
      run: () => setTab(t.value),
    })),
  ];

  return (
    <MotionConfig reducedMotion="user">
    <TripwireContext.Provider value={ctx}>
     <PresenterContext.Provider value={presenter}>
      {/* Barely-visible paper grain on the porcelain canvas (behind content, never data). */}
      <Grain />
      <div
        className="relative z-10 min-h-full"
        style={{ "--banner-h": state.isMock ? "2.25rem" : "0px" } as CSSProperties}
      >
        {/* Direct child of the full-height shell so it stays sticky while scrolling. */}
        <MockBanner />
        <Header onOpenCommands={() => setPaletteOpen(true)} commandsHint={PALETTE_KEY_HINT} />
        <main className="mx-auto max-w-[1440px] px-6 pb-24">
          <Tabs value={tab} onValueChange={setTab}>
            {/* Floating, centred pill nav: sticky below the (optional) MOCK banner. */}
            <nav
              aria-label="Sections"
              className="pointer-events-none sticky z-30 -mx-6 flex justify-center px-6 pt-4 pb-2"
              style={{ top: "var(--banner-h, 0px)" }}
            >
              <TabsList className="pointer-events-auto max-w-full overflow-x-auto">
                {TABS.map(({ value, label, icon: Icon }) => (
                  <TabsTrigger
                    key={value}
                    value={value}
                    className="data-[state=active]:bg-transparent"
                  >
                    {tab === value && (
                      <motion.span
                        layoutId="tab-pill"
                        layoutDependency={tab}
                        aria-hidden
                        className="absolute inset-0 -z-10 rounded-full bg-brand-soft ring-1 ring-brand-line/70"
                        transition={{ type: "spring", stiffness: 320, damping: 30 }}
                      />
                    )}
                    <Icon strokeWidth={1.75} /> {label}
                    {value === "incidents" && openCount > 0 && (
                      <Tooltip content={`${openCount} open incident${openCount === 1 ? "" : "s"}`}>
                        <span
                          key={openCount}
                          className="inline-flex h-[18px] min-w-[18px] animate-pop-in items-center justify-center rounded-full bg-bad px-1 font-mono text-[11px] font-semibold leading-none text-white tabular-nums"
                        >
                          {openCount}
                        </span>
                      </Tooltip>
                    )}
                  </TabsTrigger>
                ))}
              </TabsList>
            </nav>
            <TabsContent value="live">
              <LiveTab onOpenIncident={open} onReplay={replay} onReset={reset} />
            </TabsContent>
            <TabsContent value="incidents">
              <IncidentsTab onOpen={open} />
            </TabsContent>
            <TabsContent value="fleet">
              <Lazy>
                <FleetTab />
              </Lazy>
            </TabsContent>
            <TabsContent value="evidence">
              <Lazy>
                <EvidenceTab />
              </Lazy>
            </TabsContent>
            <TabsContent value="policy">
              <Lazy>
                <PolicyTab />
              </Lazy>
            </TabsContent>
            <TabsContent value="sponsors">
              <Lazy>
                <SponsorsTab />
              </Lazy>
            </TabsContent>
          </Tabs>
        </main>
        <IncidentSheet id={openId} onClose={() => setOpenId(null)} />
        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} commands={commands} />
      </div>
     </PresenterContext.Provider>
    </TripwireContext.Provider>
    </MotionConfig>
  );
}
