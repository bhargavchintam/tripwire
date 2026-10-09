import type { ReactNode } from "react";
import { Biohazard } from "lucide-react";
import { Tooltip } from "../ui/tooltip";
import { CopyId, GlowCard } from "../fx";
import { OutbreakView } from "./OutbreakGraph";
import { fmtMs } from "../../lib/format";
import type { AgentMode, Outbreak } from "../../lib/types";

const plural = (n: number, one: string, many: string) => `${n} ${n === 1 ? one : many}`;

/**
 * Outbreak card (Incidents tab, Live tab): held tone while a real outbreak trace exists, a quiet
 * one-line card otherwise. Body = OutbreakView (patient zero -> exposed agents, blocked destinations).
 */
export function OutbreakCard({
  ob,
  modes,
  sourceNote,
  className,
}: {
  ob: (Outbreak & { incident_id?: string }) | null | undefined;
  modes?: Record<string, AgentMode>;
  sourceNote?: ReactNode;
  className?: string;
}) {
  if (!ob)
    return (
      <GlowCard tone="neutral" glow="none" className={className} bodyClassName="py-4">
        <div className="flex items-center gap-3">
          <span className="grid size-9 shrink-0 place-items-center rounded-full border border-line bg-panel-2 text-dim">
            <Biohazard className="size-4" strokeWidth={1.75} aria-hidden />
          </span>
          <div className="min-w-0">
            <div className="eyebrow">Outbreak trace</div>
            <div className="mt-0.5 text-sm text-muted">No outbreak trace yet.</div>
          </div>
        </div>
      </GlowCard>
    );
  const n = ob.exposed_agents.length;
  const m = ob.blocked_destinations.length;
  return (
    <GlowCard
      tone="held"
      glow="soft"
      className={className}
      eyebrow={
        <>
          <Biohazard className="text-held" strokeWidth={1.75} /> Outbreak trace
        </>
      }
      title="Outbreak traced"
      description={`${plural(n, "exposed agent", "exposed agents")} · ${plural(m, "destination", "destinations")} blocked fleet-wide`}
      actions={
        <div className="flex items-center gap-3">
          {ob.incident_id ? <CopyId value={ob.incident_id} className="text-xs text-muted" /> : null}
          <Tooltip content="Trace query time, as measured">
            <span className="whitespace-nowrap font-mono text-xs tabular-nums text-dim">traced in {fmtMs(ob.query_ms)}</span>
          </Tooltip>
        </div>
      }
      bodyClassName="pt-2"
    >
      <OutbreakView key={`${ob.source_id}|${ob.incident_id ?? ""}`} ob={ob} modes={modes} sourceNote={sourceNote} />
    </GlowCard>
  );
}
