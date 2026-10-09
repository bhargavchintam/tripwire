import { Play, RotateCcw } from "lucide-react";
import { Button } from "../components/ui/button";
import { KpiStrip } from "../components/KpiStrip";
import { AgentCard } from "../components/AgentCard";
import { AttackChain } from "../components/AttackChain";
import { EventTable } from "../components/EventTable";
import { IncidentFeed, sortedIncidents } from "../components/IncidentFeed";
import { LIVE_AGENTS } from "../lib/types";
import { useTripwire } from "../hooks/useTripwire";

export function agentIds(modes: Record<string, unknown>, stats: Record<string, unknown>): string[] {
  const extra = [...new Set([...Object.keys(modes), ...Object.keys(stats)])].filter((a) => a.startsWith("guild:")).sort();
  return [...LIVE_AGENTS, ...extra];
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
  const latest = sortedIncidents(state.incidents)[0];
  const ids = agentIds(state.modes, state.stats);
  return (
    <div className="flex flex-col gap-4">
      <KpiStrip />

      <div className="flex flex-wrap items-center gap-2">
        <Button variant="danger" size="lg" onClick={onReplay}>
          <Play /> Replay attack <kbd className="ml-1 rounded bg-black/25 px-1.5 font-mono text-xs">R</kbd>
        </Button>
        <Button variant="outline" size="lg" onClick={onReset}>
          <RotateCcw /> Reset demo <kbd className="ml-1 rounded bg-black/25 px-1.5 font-mono text-xs">0</kbd>
        </Button>
        <span className="ml-auto hidden font-mono text-xs text-dim lg:inline">
          shortcuts: R replay · X restore last quarantined · H hold · 0 reset
        </span>
      </div>

      <div className={`grid gap-4 ${ids.length > 2 ? "lg:grid-cols-3" : "md:grid-cols-2"}`}>
        {ids.map((id) => (
          <AgentCard key={id} agentId={id} />
        ))}
      </div>

      <AttackChain incident={latest} mode={latest ? state.modes[latest.agent_id] : undefined} />

      <div className="grid gap-4 xl:grid-cols-[minmax(0,1.7fr)_minmax(0,1fr)]">
        <EventTable />
        <IncidentFeed onOpen={onOpenIncident} />
      </div>
    </div>
  );
}
