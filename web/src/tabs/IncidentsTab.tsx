import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { DecisionSourceBadge, VerdictBadge } from "../components/badges";
import { OutbreakCard } from "../components/IncidentSheet";
import { sortedIncidents } from "../components/IncidentFeed";
import { DASH, fmtClock } from "../lib/format";
import { useTripwire } from "../hooks/useTripwire";

export function IncidentsTab({ onOpen }: { onOpen: (id: string) => void }) {
  const { state } = useTripwire();
  const list = sortedIncidents(state.incidents);
  return (
    <div className="flex flex-col gap-4">
      <OutbreakCard ob={state.outbreak} />
      <Card>
        <CardHeader>
          <CardTitle>Incidents</CardTitle>
          <span className="font-mono text-xs text-dim">click a row for timeline, report and receipts</span>
        </CardHeader>
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Opened</TableHead>
                <TableHead>Id</TableHead>
                <TableHead>Agent</TableHead>
                <TableHead>Rule</TableHead>
                <TableHead>Verdict</TableHead>
                <TableHead>Decided by</TableHead>
                <TableHead>Steps</TableHead>
                <TableHead>State</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {list.length === 0 && (
                <TableRow>
                  <TableCell colSpan={8} className="py-6 text-center text-dim">
                    No incidents yet.
                  </TableCell>
                </TableRow>
              )}
              {list.map((inc) => (
                <TableRow key={inc.id} className="cursor-pointer" onClick={() => onOpen(inc.id)}>
                  <TableCell className="font-mono text-xs text-muted">{fmtClock(inc.opened_ms, false)}</TableCell>
                  <TableCell className="font-mono text-xs">{inc.id}</TableCell>
                  <TableCell className="font-mono text-xs font-semibold">{inc.agent_id}</TableCell>
                  <TableCell className="font-mono text-xs">{inc.rule}</TableCell>
                  <TableCell>
                    <VerdictBadge verdict={inc.verdict?.verdict} confidence={inc.verdict?.confidence} />
                  </TableCell>
                  <TableCell>
                    <DecisionSourceBadge source={inc.verdict?.decision_source} />
                  </TableCell>
                  <TableCell className="font-mono text-xs">{inc.steps?.length ?? DASH}</TableCell>
                  <TableCell>
                    {inc.closed_ms ? <Badge variant="muted">closed</Badge> : <Badge variant="bad">open</Badge>}
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
  );
}
