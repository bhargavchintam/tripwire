import { motion } from "motion/react";
import { ArrowRight, Ban, Biohazard, FileWarning } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "./ui/card";
import { Badge } from "./ui/badge";
import { ModeBadge } from "./badges";
import { DASH, fmtMs } from "../lib/format";
import { useTripwire } from "../hooks/useTripwire";

const pop = (i: number) => ({
  initial: { opacity: 0, y: 8 },
  animate: { opacity: 1, y: 0 },
  transition: { delay: 0.15 * i, duration: 0.3 },
});

/** Live outbreak flow: patient zero -> exposed agents (heightened) -> blocked destinations. Driven by SSE outbreak. */
export function OutbreakPanel({ hideWhenEmpty = false }: { hideWhenEmpty?: boolean }) {
  const { state } = useTripwire();
  const ob = state.outbreak;
  if (!ob && hideWhenEmpty) return null;
  const arrow = <ArrowRight className="hidden size-6 shrink-0 self-center text-held md:block" />;
  return (
    <Card className={ob ? "border-held/50" : undefined}>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Biohazard className="size-4" /> Outbreak trace
        </CardTitle>
        {ob ? (
          <span className="font-mono text-xs text-dim">
            traced in {fmtMs(ob.query_ms)}
            {ob.incident_id ? ` · ${ob.incident_id}` : ""}
          </span>
        ) : (
          <span className="text-xs text-dim">no outbreak traced</span>
        )}
      </CardHeader>
      {ob && (
        <CardContent className="flex flex-col gap-3 md:flex-row md:items-stretch">
          <motion.div {...pop(0)} className="flex-1 rounded-lg border-2 border-bad/60 bg-bad/10 p-3">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">Patient zero</div>
            <div className="flex items-center gap-2 font-mono text-sm font-bold text-bad">
              <FileWarning className="size-4 shrink-0" />
              <span className="truncate" title={ob.source_id}>
                {ob.source_id}
              </span>
            </div>
            <div className="mt-1 text-xs text-muted">untrusted input read before the attack</div>
          </motion.div>
          {arrow}
          <motion.div {...pop(1)} className="flex-1 rounded-lg border-2 border-held/60 bg-held/10 p-3">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
              Exposed agents · {ob.exposed_agents.length}
            </div>
            <div className="flex flex-col gap-1.5">
              {ob.exposed_agents.length === 0 && <span className="text-sm text-dim">{DASH}</span>}
              {ob.exposed_agents.map((a) => (
                <div key={a} className="flex items-center justify-between gap-2">
                  <span className="truncate font-mono text-sm font-semibold">{a}</span>
                  <ModeBadge mode={state.modes[a] ?? "heightened"} />
                </div>
              ))}
            </div>
          </motion.div>
          {arrow}
          <motion.div {...pop(2)} className="flex-1 rounded-lg border-2 border-line bg-panel-2 p-3">
            <div className="mb-1 text-[11px] font-semibold uppercase tracking-wider text-muted">
              Blocked fleet-wide · denylist
            </div>
            <div className="flex flex-wrap gap-1.5">
              {ob.blocked_destinations.length === 0 && <span className="text-sm text-dim">{DASH}</span>}
              {ob.blocked_destinations.map((h) => (
                <Badge key={h} variant="bad" className="px-2 py-1 font-mono text-xs">
                  <Ban /> {h}
                </Badge>
              ))}
            </div>
          </motion.div>
        </CardContent>
      )}
    </Card>
  );
}
