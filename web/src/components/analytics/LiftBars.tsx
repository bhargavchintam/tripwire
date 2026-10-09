import { useEffect, useId, useMemo, useRef, type MutableRefObject } from "react";
import { Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis, type BarShapeProps } from "recharts";
import { motion, useReducedMotion } from "motion/react";
import { toneVar, type Tone } from "../fx";

/**
 * Light "Paper & Signal" bar chart: soft vertical fills, hairline grid, mono ticks in tertiary ink,
 * white tooltip card. Bars rise from the baseline once on mount (staggered) and tween only when their
 * REAL value changes. No placeholder bars: the caller passes only measured points and renders its own
 * empty state.
 */
export type LiftDatum = { name: string; value: number; tone?: Tone; range?: string };

// Recharts writes tick props as SVG attributes, so use literal token values (index.css @theme).
const TICK = { fill: "#7A7E86", fontSize: 12, fontFamily: "Geist Mono Variable, Geist Mono, ui-monospace, monospace" };
const GRID = "#E7E5E0";
const AXIS = "#D9D6CF";
const SPRINGY = [0.22, 1, 0.36, 1] as const;

function barPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  const b = y + h;
  return `M${x},${b} L${x},${y + rr} Q${x},${y} ${x + rr},${y} L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} L${x + w},${b} Z`;
}

function LiftBar({
  x,
  y,
  width,
  height,
  index,
  fill,
  stroke,
  prev,
  reduce,
}: {
  x: number;
  y: number;
  width: number;
  height: number;
  index: number;
  fill: string;
  stroke: string;
  prev: MutableRefObject<Map<number, string>>;
  reduce: boolean;
}) {
  const top = height < 0 ? y + height : y;
  const h = Math.abs(height);
  const d = barPath(x, top, width, h, 5);
  const last = prev.current.get(index);
  const first = last === undefined;
  useEffect(() => {
    prev.current.set(index, d);
  }, [prev, index, d]);
  return (
    <motion.path
      initial={{ d: last ?? barPath(x, top + h, width, 0, 0) }}
      animate={{ d }}
      transition={
        reduce
          ? { duration: 0 }
          : { duration: first ? 0.7 : 0.45, ease: SPRINGY, delay: first ? Math.min(index * 0.045, 0.36) : 0 }
      }
      fill={fill}
      stroke={stroke}
      strokeOpacity={0.35}
      strokeWidth={1}
      className="transition-[filter] duration-200 hover:brightness-95"
    />
  );
}

export function LiftBars({
  data,
  tone = "info",
  color,
  height = 180,
  fmt,
  tick,
  seriesLabel = "value",
  allowDecimals = true,
  yWidth = 48,
  xLabel,
}: {
  data: LiftDatum[];
  /** Default bar tone (a datum's own `tone` wins). */
  tone?: Tone;
  /** CSS colour override for the default series (e.g. "var(--color-brand)"). */
  color?: string;
  height?: number;
  /** Tooltip value formatter. */
  fmt?: (v: number) => string;
  /** Y-axis tick formatter. */
  tick?: (v: number) => string;
  seriesLabel?: string;
  allowDecimals?: boolean;
  yWidth?: number;
  /** Small caption under the x axis (unit). */
  xLabel?: string;
}) {
  const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const reduce = useReducedMotion() ?? false;
  const prev = useRef(new Map<number, string>());
  const tones = useMemo(() => [...new Set([tone, ...data.map((d) => d.tone ?? tone)])], [data, tone]);
  const colorOf = (t: Tone) => (t === tone && color ? color : toneVar[t]);
  return (
    <ResponsiveContainer width="100%" height={height}>
      <BarChart
        data={data}
        margin={{ top: 8, right: 4, left: 0, bottom: xLabel ? 14 : 0 }}
        barCategoryGap="22%"
      >
        <defs>
          {tones.map((t) => (
            <linearGradient key={t} id={`${uid}-${t}`} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" style={{ stopColor: colorOf(t), stopOpacity: 0.78 }} />
              <stop offset="100%" style={{ stopColor: colorOf(t), stopOpacity: 0.32 }} />
            </linearGradient>
          ))}
        </defs>
        <CartesianGrid stroke={GRID} vertical={false} />
        <XAxis
          dataKey="name"
          tick={TICK}
          tickLine={false}
          axisLine={{ stroke: AXIS }}
          interval={0}
          tickMargin={8}
          label={
            xLabel
              ? { value: xLabel, position: "insideBottom", offset: -12, fill: "#7A7E86", fontSize: 12 }
              : undefined
          }
        />
        <YAxis
          tick={TICK}
          tickLine={false}
          axisLine={false}
          width={yWidth}
          allowDecimals={allowDecimals}
          tickFormatter={tick ? (v: number) => tick(v) : undefined}
        />
        <Tooltip
          cursor={{ fill: "rgb(21 22 26 / 0.04)", radius: 6 }}
          contentStyle={{
            background: "var(--color-panel)",
            border: "1px solid var(--color-line)",
            borderRadius: 12,
            boxShadow: "var(--shadow-lg)",
            fontFamily: "var(--font-sans)",
            fontSize: 12,
            padding: "8px 12px",
          }}
          labelStyle={{ color: "var(--color-fg)", fontWeight: 600, marginBottom: 2, fontFamily: "var(--font-mono)" }}
          itemStyle={{ color: "var(--color-muted)", padding: 0 }}
          labelFormatter={(label, p) => (p?.[0]?.payload as LiftDatum | undefined)?.range ?? String(label)}
          formatter={(v) => [fmt ? fmt(Number(v)) : String(v), seriesLabel]}
        />
        <Bar
          dataKey="value"
          maxBarSize={44}
          isAnimationActive={false}
          shape={(p: BarShapeProps) => {
            const t = (p.payload as LiftDatum | undefined)?.tone ?? tone;
            return (
              <LiftBar
                x={p.x}
                y={p.y}
                width={p.width}
                height={p.height}
                index={p.index}
                fill={`url(#${uid}-${t})`}
                stroke={colorOf(t)}
                prev={prev}
                reduce={reduce}
              />
            );
          }}
        />
      </BarChart>
    </ResponsiveContainer>
  );
}
