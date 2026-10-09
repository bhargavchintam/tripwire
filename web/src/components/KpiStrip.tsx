import { useId, type CSSProperties, type ReactNode } from "react";
import { Database, DollarSign, Gauge, Hand, Lock, Timer } from "lucide-react";
import { Tooltip } from "./ui/tooltip";
import { Count, GlowCard, InkMarker, toneText, type Tone } from "./fx";
import { DASH, fmtInt, isNum } from "../lib/format";
import { cn } from "../lib/utils";
import { useEvidence, useTripwire } from "../hooks/useTripwire";
import { usePresenter } from "../hooks/usePresenter";

const SPARK_POINTS = 60;

/** Raw detector query timings from the live stream (no axes), in brand indigo. Needs >= 2 samples. */
function Sparkline({ values }: { values: number[] }) {
  const gid = useId().replace(/:/g, "");
  if (values.length < 2) return null;
  const lo = Math.min(...values);
  const hi = Math.max(...values);
  const pts = values.map(
    (v, i) => [(i / (values.length - 1)) * 100, hi > lo ? 21 - ((v - lo) / (hi - lo)) * 18 : 12] as const,
  );
  const line = pts.map(([x, y]) => `${x.toFixed(2)},${y.toFixed(2)}`).join(" ");
  const [lx, ly] = pts[pts.length - 1];
  return (
    <svg
      viewBox="0 0 100 24"
      preserveAspectRatio="none"
      className="h-6 w-full overflow-visible"
      role="img"
      aria-label={`last ${values.length} detection query timings`}
    >
      <defs>
        <linearGradient id={`spark-${gid}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0" style={{ stopColor: "var(--color-brand)", stopOpacity: 0.16 }} />
          <stop offset="1" style={{ stopColor: "var(--color-brand)", stopOpacity: 0 }} />
        </linearGradient>
      </defs>
      <polygon points={`0,24 ${line} 100,24`} fill={`url(#spark-${gid})`} />
      <polyline
        points={line}
        fill="none"
        className="stroke-brand"
        strokeWidth={1.5}
        strokeLinejoin="round"
        strokeLinecap="round"
        vectorEffect="non-scaling-stroke"
      />
      <circle cx={lx} cy={ly} r={2.4} className="fill-brand" vectorEffect="non-scaling-stroke" />
    </svg>
  );
}

/** Real duration shown in ms, or in seconds once it reaches 10 s (same rule as fmtMs). Formatting only. */
function dur(
  ms: number | null | undefined,
  msDigits?: number,
): { value: number | undefined; digits: number; unit: string; text: string } {
  if (!isNum(ms)) return { value: undefined, digits: 0, unit: "ms", text: DASH };
  if (ms >= 10_000) return { value: ms / 1000, digits: 1, unit: "s", text: (ms / 1000).toFixed(1) };
  const digits = msDigits ?? (ms < 10 ? 1 : 0);
  return { value: ms, digits, unit: "ms", text: ms.toFixed(digits) };
}

/**
 * Display-number size that always fits its tile: min(max, (tile width - unit) / glyphs). `text` is
 * the formatted value (only its LENGTH is used); every tile shares the same max, so short values line up.
 */
function fitStyle(text: string, unit = ""): CSSProperties {
  let em = 0;
  for (const ch of text) em += /[.,]/.test(ch) ? 0.32 : ch === "—" ? 0.8 : 0.64;
  em += 0.3; // NumberFlow mask padding
  const unitW = unit ? ` - ${unit.length * 8}px - 0.5rem` : "";
  return { fontSize: `min(2.5rem, calc((100cqw${unitW}) / ${Math.max(em, 1.6).toFixed(2)}))` };
}

function Unit({ children }: { children: ReactNode }) {
  return <span className="ml-1.5 font-sans text-[13px] font-medium tracking-normal text-muted">{children}</span>;
}

function Tile({
  icon,
  label,
  note,
  source,
  tone,
  measured,
  index,
  className,
  caption,
  children,
}: {
  icon: ReactNode;
  label: string;
  note?: ReactNode;
  source: ReactNode;
  /** Icon hue once the value is measured (an unmeasured tile stays neutral). */
  tone: Tone;
  measured: boolean;
  index: number;
  className?: string;
  /** One quiet line (or the sparkline) under the number. */
  caption?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Tooltip content={<span>Source: {source}</span>}>
      <GlowCard
        tone="neutral"
        glow="none"
        reveal={index + 3}
        tabIndex={0}
        className={cn(
          "min-w-0 cursor-default transition-[translate,box-shadow,border-color] duration-200 ease-out hover:border-line-strong hover:shadow-lift motion-safe:hover:-translate-y-px",
          className,
        )}
        bodyClassName="flex min-w-0 flex-col gap-3 p-5"
      >
        <div className="flex min-w-0 items-center justify-between gap-2">
          <span className="eyebrow inline-flex min-w-0 items-center gap-1.5 [&_svg]:size-3.5 [&_svg]:shrink-0">
            <span className={cn("inline-flex", measured ? toneText[tone] : "text-dim")}>{icon}</span>
            <span className="min-w-0 truncate">{label}</span>
          </span>
          {note && <span className="shrink-0 font-mono text-[11px] text-dim">{note}</span>}
        </div>
        <div className="@container min-w-0">
          <div className="num-display flex items-baseline whitespace-nowrap text-fg">{children}</div>
        </div>
        <div className="mt-auto flex min-h-6 min-w-0 items-end text-[12px] leading-snug text-muted">{caption}</div>
      </GlowCard>
    </Tooltip>
  );
}

/** Six numbers, all straight from GET /evidence. Null renders "—". The sparkline is the raw stream. */
export function KpiStrip() {
  const { data: ev, isError, isPending } = useEvidence();
  const { state } = useTripwire();
  const presenter = usePresenter().on;
  const e = isError ? undefined : ev;
  const hold = dur(e?.hold_decision_ms);
  const contain = dur(e?.time_to_contain_ms);
  const detect = dur(e?.time_to_detect_ms);
  const p50 = dur(e?.query_p50_ms, 1);
  const p95 = dur(e?.query_p95_ms, 1);
  const stored = e?.events_stored;
  const timings = state.queryTimings.slice(-SPARK_POINTS);
  const loading = isPending && !e;
  const exact = (v: number | null | undefined) => (isNum(v) ? `${fmtInt(v)} ms` : DASH);

  // The four same-width duration tiles share ONE size (fitted to the longest of their real values),
  // so the row reads as a set; the wider tiles fit their own value.
  const durations = [hold, detect, contain, p50].filter((d) => isNum(d.value));
  const longest = durations.reduce<{ text: string; unit: string }>(
    (a, d) => (d.text.length + d.unit.length > a.text.length + a.unit.length ? { text: d.text, unit: d.unit } : a),
    { text: "000", unit: "ms" },
  );
  const value = (node: ReactNode, text: string, unit?: string, shared = false) =>
    loading ? (
      <span aria-hidden className="skeleton inline-block h-9 w-24 rounded-lg" />
    ) : (
      <span
        className="inline-flex items-baseline"
        style={shared ? fitStyle(longest.text, longest.unit) : fitStyle(text, unit)}
      >
        {node}
        {unit && <Unit>{unit}</Unit>}
      </span>
    );

  return (
    <div
      className={cn(
        "grid grid-cols-2 gap-4 md:grid-cols-3",
        // Presenter: 3 x 2 so every number reads from the back of the room; otherwise one row of six.
        !presenter && "min-[73.75rem]:grid-cols-[repeat(4,minmax(0,1.06fr))_minmax(0,1.12fr)_minmax(0,1fr)]",
      )}
    >
      <Tile
        index={0}
        icon={<Hand />}
        label="Hold decision"
        source={<>/evidence hold_decision_ms ({exact(e?.hold_decision_ms)})</>}
        tone="held"
        measured={isNum(hold.value)}
        caption={
          state.holdEnabled === null ? (
            "Median wait for a verdict"
          ) : (
            <span>
              Median wait · hold{" "}
              <span className={state.holdEnabled ? "font-medium text-held" : "font-medium text-fg"}>
                {state.holdEnabled ? "ON" : "OFF"}
              </span>
            </span>
          )
        }
      >
        {value(
          <InkMarker tone="held" trigger={hold.value} active={isNum(hold.value)} strength={40}>
            <Count value={hold.value} digits={hold.digits} />
          </InkMarker>,
          hold.text,
          isNum(hold.value) ? hold.unit : undefined,
          true,
        )}
      </Tile>

      <Tile
        index={1}
        icon={<Timer />}
        label="Time to detect"
        source={<>/evidence time_to_detect_ms ({exact(e?.time_to_detect_ms)})</>}
        tone="info"
        measured={isNum(detect.value)}
        caption="Median · last step → detection"
      >
        {value(
          <Count value={detect.value} digits={detect.digits} />,
          detect.text,
          isNum(detect.value) ? detect.unit : undefined,
          true,
        )}
      </Tile>

      <Tile
        index={2}
        icon={<Lock />}
        label="Time to contain"
        source={<>/evidence time_to_contain_ms ({exact(e?.time_to_contain_ms)})</>}
        tone="ok"
        measured={isNum(contain.value)}
        caption="Median · until quarantined"
      >
        {value(
          <InkMarker tone="ok" trigger={contain.value} active={isNum(contain.value)} strength={36}>
            <Count value={contain.value} digits={contain.digits} />
          </InkMarker>,
          contain.text,
          isNum(contain.value) ? contain.unit : undefined,
          true,
        )}
      </Tile>

      <Tile
        index={3}
        icon={<Gauge />}
        label="Detection p50"
        source={
          <>
            /evidence query_p50_ms / query_p95_ms; line: last {SPARK_POINTS} detector query timings on the live stream
          </>
        }
        tone="info"
        measured={isNum(p50.value) || isNum(p95.value)}
        caption={
          <div className="flex w-full min-w-0 flex-col gap-1.5">
            <span className="whitespace-nowrap">
              p95{" "}
              <span className="font-mono text-fg">
                <Count value={p95.value} digits={p95.digits} />
                {isNum(p95.value) ? ` ${p95.unit}` : ""}
              </span>
            </span>
            {timings.length >= 2 ? (
              <Sparkline values={timings} />
            ) : (
              <span className="text-dim">no detector timings on the stream yet</span>
            )}
          </div>
        }
      >
        {value(<Count value={p50.value} digits={p50.digits} />, p50.text, isNum(p50.value) ? p50.unit : undefined, true)}
      </Tile>

      <Tile
        index={4}
        icon={<Database />}
        label="Events stored"
        source="/evidence events_stored (ClickHouse count, includes synthetic background rows)"
        tone="info"
        measured={isNum(stored)}
        caption="ClickHouse count() · incl. synthetic"
      >
        {/* Sweeps once when the count first lands (it grows every poll; re-sweeping would be noise). */}
        {value(
          <InkMarker tone="info" active={isNum(stored)} strength={30}>
            <Count value={stored} />
          </InkMarker>,
          isNum(stored) ? fmtInt(stored) : DASH,
        )}
      </Tile>

      <Tile
        index={5}
        icon={<DollarSign />}
        label="$ / 1k events"
        source={`/evidence cost_akashml vs cost_openai${e?.priced_on ? `, priced on ${e.priced_on}` : ""}`}
        tone="model"
        measured={isNum(e?.cost_akashml)}
        caption={
          <span className="min-w-0 truncate">
            <span className="text-model">AkashML</span> vs{" "}
            <span className="font-mono text-fg">
              <Count value={e?.cost_openai} digits={4} prefix="$" />
            </span>{" "}
            OpenAI
          </span>
        }
      >
        {value(
          <span className="text-model">
            <Count value={e?.cost_akashml} digits={4} prefix="$" />
          </span>,
          isNum(e?.cost_akashml) ? `$${e.cost_akashml.toFixed(4)}` : DASH,
        )}
      </Tile>
    </div>
  );
}
