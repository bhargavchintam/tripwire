import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from "../components/ui/table";
import { Receipts } from "../components/IncidentSheet";
import { DASH, fmtClock, fmtInt, fmtMs, fmtPct, fmtUsd, isNum } from "../lib/format";
import { cn } from "../lib/utils";
import type { EvidenceBundle } from "../lib/types";
import { useEvidence, useTripwire } from "../hooks/useTripwire";

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

function Empty({ children }: { children: string }) {
  return (
    <div className="flex h-[180px] items-center justify-center text-sm text-dim">
      {DASH} {children}
    </div>
  );
}

// ---- Confusion matrix: tp/fp/tn/fn exactly as the eval runner sent them -----

type Counts = { tp: number; fp: number; tn: number; fn: number };

/** Null unless all four counts are non-negative integers. */
// eslint-disable-next-line @typescript-eslint/no-explicit-any
function evalCounts(m: Record<string, any> | undefined): Counts | null {
  if (!m || !(["tp", "fp", "tn", "fn"] as const).every((k) => Number.isInteger(m[k]) && m[k] >= 0)) return null;
  return { tp: m.tp, fp: m.fp, tn: m.tn, fn: m.fn };
}

const TONE = {
  ok: "border-ok/40 bg-ok/10 text-ok",
  bad: "border-bad/50 bg-bad/15 text-bad",
  held: "border-held/50 bg-held/10 text-held",
  none: "border-line bg-panel-2 text-fg",
};

function Cell({ n, tag, title, tone }: { n: number; tag: string; title: string; tone: keyof typeof TONE }) {
  return (
    <div title={title} className={cn("flex flex-col items-center justify-center rounded-lg border py-3", TONE[tone])}>
      <span className="font-mono text-2xl font-bold tabular-nums">{fmtInt(n)}</span>
      <span className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">{tag}</span>
    </div>
  );
}

function MatrixGrid({ c }: { c: Counts }) {
  const head = "text-center text-[10px] font-semibold uppercase tracking-[0.12em] text-dim";
  const side = "text-right text-[10px] font-semibold uppercase tracking-[0.12em] text-dim";
  return (
    <div className="grid grid-cols-[auto_1fr_1fr] items-center gap-2">
      <span />
      <span className={head}>Pred. attack</span>
      <span className={head}>Pred. benign</span>
      <span className={side}>Actual attack</span>
      <Cell n={c.tp} tag="TP" title="true positive: attack case flagged" tone="ok" />
      <Cell n={c.fn} tag="FN" title="false negative: attack case missed" tone={c.fn > 0 ? "bad" : "none"} />
      <span className={side}>Actual benign</span>
      <Cell n={c.fp} tag="FP" title="false positive: benign case flagged" tone={c.fp > 0 ? "held" : "none"} />
      <Cell n={c.tn} tag="TN" title="true negative: benign case not flagged" tone="ok" />
    </div>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex flex-col gap-0.5">
      <dt className="text-[10px] font-semibold uppercase tracking-[0.12em] text-muted">{label}</dt>
      <dd className={cn("font-mono text-lg font-bold tabular-nums", value === DASH ? "text-dim" : "text-fg")}>{value}</dd>
    </div>
  );
}

function ConfusionMatrixCard({ e }: { e: EvidenceBundle | undefined }) {
  const { state } = useTripwire();
  const hb = state.evalHeartbeat;
  const c = evalCounts(hb?.metrics);
  return (
    <Card>
      <CardHeader>
        <CardTitle>Confusion matrix</CardTitle>
        {c && isNum(hb?.received_ms) && (
          <span className="font-mono text-xs text-dim">eval run · {fmtClock(hb.received_ms, false)}</span>
        )}
      </CardHeader>
      <CardContent>
        <div className="flex flex-col gap-4 sm:flex-row sm:items-center">
          <div className="min-w-0 flex-1">
            {c ? <MatrixGrid c={c} /> : <Empty>waiting for eval run (tp/fp/tn/fn)</Empty>}
          </div>
          <dl className="grid shrink-0 grid-cols-3 gap-3 sm:w-28 sm:grid-cols-1">
            <Stat label="Precision" value={fmtPct(e?.precision)} />
            <Stat label="Recall" value={fmtPct(e?.recall)} />
            <Stat label="Cases (n)" value={fmtInt(e?.n_cases)} />
          </dl>
        </div>
        <p className="mt-3 text-xs text-dim">
          Counts: last eval heartbeat on the live stream. Precision / recall / n: GET /evidence.
        </p>
      </CardContent>
    </Card>
  );
}

// ---- Detection query latency histogram: raw detector timings, this screen only ----

const BINS = 8;
const MIN_SAMPLES = 5;

/** Equal-width bins over the observed range. Counts only; no derived statistics. */
function histogram(samples: number[]) {
  const lo = Math.min(...samples);
  const hi = Math.max(...samples);
  const bins = hi > lo ? BINS : 1;
  const w = (hi - lo) / bins || 1;
  const digits = hi - lo < BINS ? 2 : hi - lo < BINS * 10 ? 1 : 0;
  const n = (v: number) => v.toFixed(digits);
  const out = Array.from({ length: bins }, (_, i) => {
    const a = lo + i * w;
    const b = bins === 1 ? hi : a + w;
    return { name: n(a), range: `${n(a)}–${n(b)} ms`, value: 0 };
  });
  for (const v of samples) out[Math.min(bins - 1, Math.floor((v - lo) / w))].value += 1;
  return out;
}

function LatencyHistogramCard() {
  const { state } = useTripwire();
  const samples = state.queryTimings;
  return (
    <Card>
      <CardHeader>
        <CardTitle>Detection query latency (ms)</CardTitle>
        {samples.length >= MIN_SAMPLES && <span className="font-mono text-xs text-dim">n = {samples.length}</span>}
      </CardHeader>
      <CardContent>
        {samples.length < MIN_SAMPLES ? (
          <Empty>{`collecting samples (${samples.length}/${MIN_SAMPLES})`}</Empty>
        ) : (
          <ResponsiveContainer width="100%" height={180}>
            <BarChart data={histogram(samples)} margin={{ top: 8, right: 8, left: 0, bottom: 0 }}>
              <CartesianGrid stroke="#253041" vertical={false} />
              <XAxis dataKey="name" stroke="#94a3b8" fontSize={11} tickLine={false} />
              <YAxis stroke="#94a3b8" fontSize={11} tickLine={false} width={36} allowDecimals={false} />
              <Tooltip
                cursor={{ fill: "rgb(148 163 184 / 0.08)" }}
                contentStyle={{ background: "#131a23", border: "1px solid #253041", borderRadius: 8, fontSize: 12 }}
                labelFormatter={(_, p) => p?.[0]?.payload?.range ?? ""}
                formatter={(v) => [v, "queries"]}
              />
              <Bar dataKey="value" fill="#38bdf8" radius={[4, 4, 0, 0]} isAnimationActive={false} />
            </BarChart>
          </ResponsiveContainer>
        )}
        <p className="mt-3 text-xs text-dim">
          Per-query timings from detector heartbeats (SSE metrics · query_timings_ms) received on this screen since
          the last snapshot; cleared on reset.
        </p>
      </CardContent>
    </Card>
  );
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

      <div className="grid gap-4 lg:grid-cols-2">
        <ConfusionMatrixCard e={e} />
        <LatencyHistogramCard />
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
