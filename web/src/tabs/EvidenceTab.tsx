import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { Receipts } from "../components/IncidentSheet";
import { DASH, fmtInt, fmtMs, fmtPct, fmtUsd, isNum } from "../lib/format";
import type { EvidenceBundle } from "../lib/types";
import { useEvidence } from "../hooks/useTripwire";

type Row = { key: keyof EvidenceBundle; label: string; fmt: (v: never) => string; source: string };

const ROWS: Row[] = [
  { key: "events_stored", label: "Events stored", fmt: fmtInt as Row["fmt"], source: "ClickHouse count() on tripwire.events (includes synthetic=1 background rows)" },
  { key: "query_p50_ms", label: "Detection query p50", fmt: fmtMs as Row["fmt"], source: "detector heartbeat query_timings_ms (nearest rank)" },
  { key: "query_p95_ms", label: "Detection query p95", fmt: fmtMs as Row["fmt"], source: "detector heartbeat query_timings_ms (nearest rank)" },
  { key: "time_to_detect_ms", label: "Time to detect", fmt: fmtMs as Row["fmt"], source: "median of block alerts: detected_at_ms − last_step_ts_ms" },
  { key: "time_to_contain_ms", label: "Time to contain", fmt: fmtMs as Row["fmt"], source: "median of detector + hold containment samples only (honeytoken trips reported separately as receipt kind honeytoken_contain)" },
  { key: "hold_decision_ms", label: "Hold decision latency", fmt: fmtMs as Row["fmt"], source: "median wall time of the whole hold decision (hold samples)" },
  { key: "precision", label: "Precision", fmt: fmtPct as Row["fmt"], source: "eval runner over labelled cases" },
  { key: "recall", label: "Recall", fmt: fmtPct as Row["fmt"], source: "eval runner over labelled cases" },
  { key: "n_cases", label: "Eval cases (n)", fmt: fmtInt as Row["fmt"], source: "fixtures/eval attack + benign" },
  { key: "cost_akashml", label: "Cost / 1,000 events · AkashML", fmt: fmtUsd as Row["fmt"], source: "Verdict tokens × AkashML price" },
  { key: "cost_openai", label: "Cost / 1,000 events · OpenAI", fmt: fmtUsd as Row["fmt"], source: "same tokens × OpenAI price" },
  { key: "priced_on", label: "Prices as of", fmt: ((v: string | null) => v ?? DASH) as Row["fmt"], source: "price table date" },
];

function ChartOrEmpty({ data, unit }: { data: { name: string; value: number }[]; unit: string }) {
  if (data.length === 0) return <div className="flex h-[180px] items-center justify-center text-sm text-dim">— not measured yet</div>;
  return (
    <ResponsiveContainer width="100%" height={180}>
      <BarChart data={data} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid stroke="#253041" vertical={false} />
        <XAxis dataKey="name" stroke="#94a3b8" fontSize={12} tickLine={false} />
        <YAxis stroke="#94a3b8" fontSize={11} tickLine={false} width={48} unit={unit} />
        <Tooltip
          cursor={{ fill: "rgb(148 163 184 / 0.08)" }}
          contentStyle={{ background: "#131a23", border: "1px solid #253041", borderRadius: 8, fontSize: 12 }}
        />
        <Bar dataKey="value" fill="#a78bfa" radius={[4, 4, 0, 0]} isAnimationActive={false} />
      </BarChart>
    </ResponsiveContainer>
  );
}

/** Only real (non-null) values become bars. */
function points(e: EvidenceBundle | undefined, pairs: [keyof EvidenceBundle, string][]) {
  return pairs.flatMap(([k, name]) => {
    const v = e?.[k];
    return isNum(v) ? [{ name, value: v }] : [];
  });
}

export function EvidenceTab() {
  const { data, isError, error } = useEvidence();
  const e = isError ? undefined : data;
  return (
    <div className="flex flex-col gap-4">
      {isError && (
        <Card className="border-held/50 p-4 text-sm text-held">GET /evidence failed: {(error as Error).message}</Card>
      )}
      <div className="grid gap-4 lg:grid-cols-3">
        <Card>
          <CardHeader>
            <CardTitle>Latency (ms)</CardTitle>
          </CardHeader>
          <CardContent>
            <ChartOrEmpty
              unit=""
              data={points(e, [
                ["query_p50_ms", "p50"],
                ["query_p95_ms", "p95"],
                ["hold_decision_ms", "hold"],
                ["time_to_detect_ms", "detect"],
                ["time_to_contain_ms", "contain"],
              ])}
            />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>Detection quality</CardTitle>
            {isNum(e?.n_cases) && <span className="font-mono text-xs text-dim">n = {e?.n_cases}</span>}
          </CardHeader>
          <CardContent>
            <ChartOrEmpty unit="" data={points(e, [["precision", "precision"], ["recall", "recall"]])} />
          </CardContent>
        </Card>
        <Card>
          <CardHeader>
            <CardTitle>$ per 1,000 events</CardTitle>
            {e?.priced_on && <span className="font-mono text-xs text-dim">{e.priced_on}</span>}
          </CardHeader>
          <CardContent>
            <ChartOrEmpty unit="" data={points(e, [["cost_akashml", "AkashML"], ["cost_openai", "OpenAI"]])} />
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader>
          <CardTitle>Evidence bundle · GET /evidence</CardTitle>
          {e?.mock ? <Badge variant="held">mock: true</Badge> : <Badge variant="muted">polled every 2 s</Badge>}
        </CardHeader>
        <CardContent className="px-0">
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Field</TableHead>
                <TableHead>Metric</TableHead>
                <TableHead className="text-right">Value</TableHead>
                <TableHead>Source</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              {ROWS.map((r) => (
                <TableRow key={r.key}>
                  <TableCell className="font-mono text-xs text-muted">{r.key}</TableCell>
                  <TableCell className="text-sm">{r.label}</TableCell>
                  <TableCell className="text-right font-mono text-sm font-bold tabular-nums">
                    {r.fmt((e?.[r.key] ?? null) as never)}
                  </TableCell>
                  <TableCell className="text-xs text-muted">{r.source}</TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </CardContent>
      </Card>

      <Card>
        <CardHeader>
          <CardTitle>Receipts</CardTitle>
        </CardHeader>
        <CardContent>
          <Receipts receipts={e?.receipts} />
        </CardContent>
      </Card>
    </div>
  );
}

export default EvidenceTab;
