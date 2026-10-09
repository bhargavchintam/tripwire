import { Link2, Link2Off, LoaderCircle } from "lucide-react";
import { StatusPill } from "./badges";
import { fmtClock, fmtInt } from "../lib/format";
import { useAudit } from "../hooks/useTripwire";

/** GET /audit/verify/{agent}: per-agent hash chain intact / broken. */
export function AuditBadge({ agentId }: { agentId: string | undefined }) {
  const q = useAudit(agentId);
  if (!agentId) return null;
  if (q.isLoading)
    return (
      <StatusPill
        variant="muted"
        icon={<LoaderCircle className="animate-spin" />}
        tip={<>Verifying the audit hash chain for <span className="font-mono">{agentId}</span></>}
      >
        Verifying chain…
      </StatusPill>
    );
  if (q.isError || !q.data)
    return (
      <StatusPill
        variant="muted"
        icon={<Link2Off />}
        tip={(q.error as Error | null)?.message ?? "The audit check did not answer"}
      >
        Audit unavailable
      </StatusPill>
    );
  const a = q.data;
  if (a.intact)
    return (
      <StatusPill
        variant="ok"
        icon={<Link2 />}
        tip={
          <>
            Hash chain for <span className="font-mono">{a.agent_id}</span> verified
            <span className="text-muted"> · /audit/verify</span>
          </>
        }
      >
        Chain intact · <span className="font-mono tabular-nums">{fmtInt(a.events)}</span> events
        {a.mock ? " (mock)" : ""}
      </StatusPill>
    );
  return (
    <StatusPill
      variant="bad"
      icon={<Link2Off />}
      tip={
        <>
          First broken link at <span className="font-mono">{a.first_break_ts_ms ?? "?"}</span>
        </>
      }
    >
      Chain broken at <span className="font-mono tabular-nums">{fmtClock(a.first_break_ts_ms)}</span>
    </StatusPill>
  );
}
