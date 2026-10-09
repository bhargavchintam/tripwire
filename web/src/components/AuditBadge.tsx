import { Link2, Link2Off, LoaderCircle } from "lucide-react";
import { Badge } from "./ui/badge";
import { fmtClock, fmtInt } from "../lib/format";
import { useAudit } from "../hooks/useTripwire";

/** GET /audit/verify/{agent}: per-agent hash chain intact / broken. */
export function AuditBadge({ agentId }: { agentId: string | undefined }) {
  const q = useAudit(agentId);
  if (!agentId) return null;
  if (q.isLoading)
    return (
      <Badge variant="muted">
        <LoaderCircle className="animate-spin" /> verifying chain…
      </Badge>
    );
  if (q.isError || !q.data)
    return (
      <Badge variant="muted" title={(q.error as Error | null)?.message}>
        <Link2Off /> audit unavailable
      </Badge>
    );
  const a = q.data;
  if (a.intact)
    return (
      <Badge variant="ok" title={`hash chain for ${a.agent_id} verified`}>
        <Link2 /> Chain intact ✓ · {fmtInt(a.events)} events{a.mock ? " (mock)" : ""}
      </Badge>
    );
  return (
    <Badge variant="bad" title={`first broken link at ${a.first_break_ts_ms ?? "?"}`}>
      <Link2Off /> Chain broken at {fmtClock(a.first_break_ts_ms)}
    </Badge>
  );
}
