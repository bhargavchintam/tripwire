import { useEffect, useState, type ReactNode } from "react";
import { useReducedMotion } from "motion/react";
import { Gauge, Play, RotateCcw } from "lucide-react";
import { Button, ButtonArrow, Kbd } from "../components/ui/button";
import { Eyebrow, RuleDraw, SectionHeader } from "../components/fx";
import { FleetOrbit } from "../components/FleetOrbit";
import { KpiStrip } from "../components/KpiStrip";
import { AgentCard, agentDomId } from "../components/AgentCard";
import { AttackChain } from "../components/AttackChain";
import { EventTable } from "../components/EventTable";
import { IncidentFeed, sortedIncidents } from "../components/IncidentFeed";
import { OutbreakPanel } from "../components/OutbreakPanel";
import { LIVE_AGENTS } from "../lib/types";
import { cn } from "../lib/utils";
import { isLiveAgent, useTripwire } from "../hooks/useTripwire";
import { usePresenter } from "../hooks/usePresenter";

export function agentIds(modes: Record<string, unknown>, stats: Record<string, unknown>): string[] {
  const extra = [...new Set([...Object.keys(modes), ...Object.keys(stats)])]
    .filter((a) => a.startsWith("guild:") && isLiveAgent(a))
    .sort();
  return [...LIVE_AGENTS, ...extra];
}

const FOCUS_MS = 1800;

const SHORTCUTS: [string, string][] = [
  ["R", "replay"],
  ["X", "restore last quarantined"],
  ["H", "hold"],
  ["P", "presenter"],
  ["0", "reset"],
];

/** A quiet section label with a self-drawing hairline. */
function SectionLabel({ children, icon }: { children: ReactNode; icon?: ReactNode }) {
  return (
    <div className="flex items-center gap-4">
      <Eyebrow icon={icon} className="shrink-0">
        {children}
      </Eyebrow>
      <RuleDraw delay={0.2} className="flex-1" />
    </div>
  );
}

export function LiveTab({
  onOpenIncident,
  onReplay,
  onReset,
}: {
  onOpenIncident: (id: string) => void;
  onReplay: () => void;
  onReset: () => void;
}) {
  const { state } = useTripwire();
  const presenter = usePresenter().on;
  const reduced = useReducedMotion();
  const latest = sortedIncidents(state.incidents)[0];
  const ids = agentIds(state.modes, state.stats);

  // Clicking a planet in the orbit rings that agent's card briefly (scrolls it into view if needed).
  const [focus, setFocus] = useState<{ id: string; n: number } | null>(null);
  useEffect(() => {
    if (!focus) return;
    const t = setTimeout(() => setFocus(null), FOCUS_MS);
    return () => clearTimeout(t);
  }, [focus]);
  const showAgent = (id: string) => {
    const el = document.getElementById(agentDomId(id));
    el?.scrollIntoView({ behavior: reduced ? "auto" : "smooth", block: "nearest" });
    setFocus((f) => ({ id, n: (f?.n ?? 0) + 1 }));
  };
  // Hovering / focusing an agent card rings its planet in the orbit.
  const [hoverId, setHoverId] = useState<string | null>(null);

  const stacked = ids.length <= 3;

  return (
    <div className="flex flex-col gap-6">
      <SectionHeader
        eyebrow="Live · tool-call stream"
        eyebrowTone={state.connection === "live" ? "ok" : "held"}
        pre="Live"
        em="fleet"
        size={presenter ? "sm" : "md"}
        rule={!presenter}
        description={
          presenter ? undefined : "Every tool call passes the checkpoint: allowed, held for a verdict, or denied."
        }
        actions={
          <div className="flex flex-col items-end gap-2.5">
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button variant="outline" size="lg" onClick={onReset}>
                <RotateCcw /> Reset demo <Kbd>0</Kbd>
              </Button>
              <Button variant="danger" size="lg" onClick={onReplay}>
                <Play /> Replay attack <Kbd>R</Kbd>
                <ButtonArrow />
              </Button>
            </div>
            {!presenter && (
              <div
                aria-label="Keyboard shortcuts"
                className="flex flex-wrap items-center justify-end gap-x-3.5 gap-y-1 text-[12px] text-dim"
              >
                {SHORTCUTS.map(([k, label]) => (
                  <span key={k} className="inline-flex items-center gap-1.5">
                    <Kbd className="ml-0">{k}</Kbd>
                    {label}
                  </span>
                ))}
              </div>
            )}
          </div>
        }
      />

      {/* Row 1: the night-sky orbit + the agents, side by side so the agents sit above the fold. */}
      <section aria-label="Fleet" className="grid grid-cols-1 gap-4 lg:grid-cols-12">
        <div className="min-w-0 lg:col-span-7">
          <FleetOrbit
            ids={ids}
            onSelect={showAgent}
            activeId={focus?.id ?? hoverId}
            className="h-[360px] lg:h-full"
          />
        </div>
        <div
          aria-label={`Agents · ${ids.length}`}
          role="list"
          className={cn(
            "grid min-w-0 gap-3 lg:col-span-5",
            stacked ? "grid-cols-1 lg:auto-rows-fr" : "grid-cols-1 sm:grid-cols-2 lg:auto-rows-fr",
          )}
        >
          {ids.map((id, i) => (
            <div role="listitem" key={id} className="min-w-0">
              <AgentCard agentId={id} index={i + 1} highlighted={focus?.id === id} onHover={setHoverId} />
            </div>
          ))}
        </div>
      </section>

      {/* Row 2: the measured numbers. */}
      <section aria-label="Measured" className="flex flex-col gap-3">
        <SectionLabel icon={<Gauge />}>Measured · GET /evidence</SectionLabel>
        <KpiStrip />
      </section>

      {/* Row 3: the latest incident's chain. */}
      <AttackChain incident={latest} mode={latest ? state.modes[latest.agent_id] : undefined} />

      {/* Presenter mode keeps the outbreak flow only once there is one (demo act 3). */}
      <OutbreakPanel hideWhenEmpty={presenter} />

      {!presenter && (
        <div className="grid grid-cols-12 gap-4">
          <div className="col-span-12 min-w-0 xl:col-span-8">
            <EventTable />
          </div>
          <div className="col-span-12 min-w-0 xl:col-span-4">
            <IncidentFeed onOpen={onOpenIncident} />
          </div>
        </div>
      )}
    </div>
  );
}
