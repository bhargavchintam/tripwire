import { useMemo, useState } from "react";
import { Flame, Radio, Receipt } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "../components/ui/card";
import { Badge } from "../components/ui/badge";
import { DASH, fmtInt, fmtMs } from "../lib/format";
import type { FleetHeatmap } from "../lib/types";
import { useFleetTop, useHeatmap } from "../hooks/useTripwire";

const HOURS = 72;

// Sequential single-hue (blue) ramp for magnitude on the dark surface: near-zero recedes, hot = bright.
// Status colours (red/amber) stay reserved for state. Steps from the dataviz reference palette.
const RAMP = ["#104281", "#1c5cab", "#2a78d6", "#5598e7", "#86b6ef", "#cde2fb"];
const BINS = [1, 11, 26, 46, 66, 86]; // lower bound (inclusive) of each ramp step

function cellColor(score: number, events: number): string {
  if (events <= 0) return "transparent";
  if (score <= 0) return "rgb(148 163 184 / 0.10)"; // activity, zero risk
  let idx = 0;
  for (let i = 0; i < BINS.length; i++) if (score >= BINS[i]) idx = i;
  return RAMP[idx];
}

const isLive = (a: string) => a === "deploy-bot" || a === "support-bot" || a.startsWith("guild:");

function hourLabel(ms: number, withDay = false): string {
  const d = new Date(ms);
  const hh = String(d.getHours()).padStart(2, "0");
  return withDay ? `${d.toLocaleDateString("en-US", { weekday: "short" })} ${hh}:00` : `${hh}h`;
}

interface Hover {
  agent: string;
  hour: number;
  score: number;
  events: number;
  x: number;
  y: number;
}

function Heatmap({ data }: { data: FleetHeatmap }) {
  const [hover, setHover] = useState<Hover | null>(null);
  const { rows, lookup } = useMemo(() => {
    const lookup = new Map<string, [number, number]>();
    for (const [a, h, score, events] of data.cells) lookup.set(`${a}:${h}`, [score, events]);
    // Server already orders live first; keep that, but pin live agents defensively.
    const idx = data.agents.map((a, i) => ({ a, i }));
    const rows = [...idx.filter((r) => isLive(r.a)), ...idx.filter((r) => !isLive(r.a))];
    return { rows, lookup };
  }, [data]);
  const cols = data.hours.length;
  const grid = { gridTemplateColumns: `164px repeat(${cols}, minmax(0, 1fr))` };
  const firstSynthetic = rows.findIndex((r) => !isLive(r.a));

  return (
    <div className="relative" onMouseLeave={() => setHover(null)}>
      {/* hour axis */}
      <div className="grid gap-px pb-1" style={grid}>
        <span />
        {data.hours.map((h, i) => (
          <span key={h} className="overflow-visible whitespace-nowrap font-mono text-[9px] text-dim">
            {i % 12 === 0 ? hourLabel(h, true) : ""}
          </span>
        ))}
      </div>
      <div className="flex flex-col gap-px">
        {rows.map(({ a, i }, r) => (
          <div key={a}>
            {r === firstSynthetic && firstSynthetic > 0 && <div className="my-1 border-t border-dashed border-line" />}
            <div className="grid items-center gap-px" style={grid}>
              <span className="flex min-w-0 items-center gap-1 pr-2">
                {isLive(a) && (
                  <Badge variant="ok" className="px-1 text-[9px]">
                    <Radio /> LIVE
                  </Badge>
                )}
                <span className={`truncate font-mono text-[11px] ${isLive(a) ? "font-bold text-fg" : "text-muted"}`} title={a}>
                  {a}
                </span>
              </span>
              {data.hours.map((h, hi) => {
                const [score, events] = lookup.get(`${i}:${hi}`) ?? [0, 0];
                return (
                  <span
                    key={h}
                    onMouseEnter={(e) => {
                      const box = (e.currentTarget.offsetParent as HTMLElement | null)?.getBoundingClientRect();
                      const c = e.currentTarget.getBoundingClientRect();
                      setHover({
                        agent: a,
                        hour: h,
                        score,
                        events,
                        x: c.left - (box?.left ?? 0) + c.width / 2,
                        y: c.top - (box?.top ?? 0),
                      });
                    }}
                    className={`h-3.5 rounded-[2px] ${events > 0 ? "" : "bg-panel-2/60"} ${
                      hover && hover.agent === a && hover.hour === h ? "outline outline-1 outline-fg" : ""
                    }`}
                    style={events > 0 ? { backgroundColor: cellColor(score, events) } : undefined}
                    aria-label={`${a} ${hourLabel(h, true)} score ${score} events ${events}`}
                  />
                );
              })}
            </div>
          </div>
        ))}
      </div>
      {hover && (
        <div
          className={`pointer-events-none absolute z-20 -translate-x-1/2 rounded-md border border-line bg-panel-2 px-2.5 py-1.5 text-xs shadow-lg ${
            hover.y < 64 ? "" : "-translate-y-full"
          }`}
          style={{ left: hover.x, top: hover.y < 64 ? hover.y + 20 : hover.y - 6 }}
        >
          <div className="font-mono font-bold">{hover.agent}</div>
          <div className="font-mono text-muted">{hourLabel(hover.hour, true)}</div>
          <div className="font-mono">
            score <span className="font-bold text-fg">{hover.score}</span> · {fmtInt(hover.events)} events
          </div>
        </div>
      )}
      {/* legend */}
      <div className="mt-3 flex flex-wrap items-center gap-2 text-[11px] text-muted">
        <span>risk score</span>
        <span className="inline-block h-3 w-4 rounded-[2px] border border-line bg-panel-2/60" /> no events
        <span className="inline-block h-3 w-4 rounded-[2px]" style={{ backgroundColor: "rgb(148 163 184 / 0.10)" }} /> 0
        {RAMP.map((c, i) => (
          <span key={c} className="flex items-center gap-1">
            <span className="inline-block h-3 w-4 rounded-[2px]" style={{ backgroundColor: c }} />
            {BINS[i]}
            {i === RAMP.length - 1 ? "–100" : ""}
          </span>
        ))}
      </div>
    </div>
  );
}

function TopRisky() {
  const { data, isError, error, dataUpdatedAt } = useFleetTop(60, 10);
  return (
    <Card>
      <CardHeader>
        <CardTitle className="flex items-center gap-1.5">
          <Flame className="size-4" /> Top risky agents · last 60 min
        </CardTitle>
        <span className="font-mono text-xs text-dim">
          refresh 5 s{dataUpdatedAt ? ` · ${new Date(dataUpdatedAt).toLocaleTimeString("en-US", { hour12: false })}` : ""}
        </span>
      </CardHeader>
      <CardContent className="px-0">
        {isError ? (
          <div className="px-4 text-sm text-held">GET /fleet/top failed: {(error as Error).message}</div>
        ) : !data ? (
          <div className="px-4 text-sm text-dim">Loading…</div>
        ) : data.length === 0 ? (
          <div className="px-4 text-sm text-dim">No activity in the window.</div>
        ) : (
          <div className="flex flex-col">
            <div className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_64px_64px_64px] gap-2 border-b border-line px-4 pb-2 text-[11px] font-semibold uppercase tracking-wider text-muted">
              <span>Agent</span>
              <span>Score</span>
              <span className="text-right">Events</span>
              <span className="text-right">Denied</span>
              <span className="text-right">Ext. posts</span>
            </div>
            {data.map((r) => (
              <div
                key={r.agent_id}
                className="grid grid-cols-[minmax(0,1.3fr)_minmax(0,1fr)_64px_64px_64px] items-center gap-2 border-b border-line/50 px-4 py-1.5 text-sm"
              >
                <span className="flex min-w-0 items-center gap-1 font-mono text-xs font-semibold">
                  {isLive(r.agent_id) && <Radio className="size-3 shrink-0 text-ok" aria-label="live agent" />}
                  <span className="truncate">{r.agent_id}</span>
                </span>
                <span className="flex items-center gap-2">
                  <span className="h-2 flex-1 overflow-hidden rounded-full bg-panel-2">
                    <span
                      className="block h-full rounded-full bg-[#5598e7]"
                      style={{ width: `${Math.max(0, Math.min(100, r.score))}%` }}
                    />
                  </span>
                  <span className="w-8 text-right font-mono text-xs tabular-nums">{r.score}</span>
                </span>
                <span className="text-right font-mono text-xs tabular-nums">{fmtInt(r.events)}</span>
                <span className={`text-right font-mono text-xs tabular-nums ${r.denied ? "text-bad" : ""}`}>
                  {fmtInt(r.denied)}
                </span>
                <span className="text-right font-mono text-xs tabular-nums">{fmtInt(r.external_posts)}</span>
              </div>
            ))}
          </div>
        )}
      </CardContent>
    </Card>
  );
}

export function FleetTab() {
  const { data, isError, error, isLoading } = useHeatmap(HOURS);
  return (
    <div className="flex flex-col gap-4">
      <Card>
        <CardHeader>
          <CardTitle>Fleet risk heatmap · {HOURS} h</CardTitle>
          {data?.mock ? <Badge variant="held">mock</Badge> : null}
        </CardHeader>
        <CardContent className="overflow-x-auto">
          {isError ? (
            <div className="text-sm text-held">GET /fleet/heatmap failed: {(error as Error).message}</div>
          ) : isLoading || !data ? (
            <div className="text-sm text-dim">Querying ClickHouse…</div>
          ) : data.agents.length === 0 ? (
            <div className="text-sm text-dim">No events in the window.</div>
          ) : (
            <div className="min-w-[760px]">
              <Heatmap data={data} />
            </div>
          )}
        </CardContent>
        {data && (
          <div className="flex flex-wrap items-center gap-x-2 gap-y-1 border-t border-line px-4 py-2 font-mono text-xs text-muted">
            <Receipt className="size-3.5" />
            <span>
              {data.agents.length} agents × {HOURS} h from <span className="text-fg">{fmtInt(data.rows_read)}</span> rows in{" "}
              <span className="text-fg">{fmtMs(data.query_ms)}</span>
            </span>
            {data.formula ? (
              <span className="w-full truncate text-dim" title={data.formula}>
                score = {data.formula}
              </span>
            ) : (
              <span className="text-dim">{DASH}</span>
            )}
          </div>
        )}
      </Card>
      <TopRisky />
    </div>
  );
}

export default FleetTab;
