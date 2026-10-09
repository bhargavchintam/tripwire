import { lazy, Suspense, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Activity, BookCheck, Grid3x3, Handshake, ScrollText, Siren } from "lucide-react";
import { toast } from "sonner";
import { Header, toggleHold } from "./components/Header";
import { IncidentSheet } from "./components/IncidentSheet";
import { restoreAgent } from "./components/AgentCard";
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
  return <Suspense fallback={<div className="p-6 text-sm text-dim">Loading…</div>}>{children}</Suspense>;
}

async function replay() {
  try {
    await api.replay("secret_theft");
    toast("Replaying recorded attack: secret_theft");
  } catch (e) {
    toast.error(`Replay failed: ${(e as Error).message}`);
  }
}

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

/** Presenter shortcuts: R replay · X restore most recently quarantined · H hold · P presenter · 0 reset. */
function useShortcuts(ctx: TripwireCtx, togglePresenter: () => void) {
  const ref = useRef(ctx);
  ref.current = ctx;
  const presRef = useRef(togglePresenter);
  presRef.current = togglePresenter;
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.metaKey || e.ctrlKey || e.altKey || e.repeat || isTyping(e.target)) return;
      const { state, setHold } = ref.current;
      const k = e.key.toLowerCase();
      if (k === "r") replay();
      else if (k === "0") reset();
      else if (k === "h") toggleHold(state.holdEnabled, setHold);
      else if (k === "p") presRef.current();
      else if (k === "x") {
        const target = [...state.quarantineOrder].reverse().find((a) => state.modes[a] === "quarantined");
        if (target) restoreAgent(target);
        else toast("No quarantined agent to restore");
      } else return;
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
  const open = useCallback((id: string) => setOpenId(id), []);
  useShortcuts(ctx, presenter.toggle);
  const openCount = Object.values(ctx.state.incidents).filter((i) => !i.closed_ms).length;

  return (
    <TripwireContext.Provider value={ctx}>
     <PresenterContext.Provider value={presenter}>
      <div className="min-h-full">
        <Header />
        <main className="mx-auto max-w-[1600px] px-5 py-4">
          <Tabs value={tab} onValueChange={setTab}>
            <TabsList>
              <TabsTrigger value="live">
                <Activity /> Live
              </TabsTrigger>
              <TabsTrigger value="incidents">
                <Siren /> Incidents
                {openCount > 0 && (
                  <span className="rounded bg-bad px-1.5 font-mono text-[11px] font-bold text-white">{openCount}</span>
                )}
              </TabsTrigger>
              <TabsTrigger value="fleet">
                <Grid3x3 /> Fleet
              </TabsTrigger>
              <TabsTrigger value="evidence">
                <BookCheck /> Evidence
              </TabsTrigger>
              <TabsTrigger value="policy">
                <ScrollText /> Policy
              </TabsTrigger>
              <TabsTrigger value="sponsors">
                <Handshake /> Sponsors
              </TabsTrigger>
            </TabsList>
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
      </div>
     </PresenterContext.Provider>
    </TripwireContext.Provider>
  );
}
