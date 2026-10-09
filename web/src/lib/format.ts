// Formatting only. The UI never computes metrics: null/undefined always renders "—".
export const DASH = "—";

export function isNum(v: unknown): v is number {
  return typeof v === "number" && Number.isFinite(v);
}

export function fmtMs(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  if (v >= 10_000) return `${(v / 1000).toFixed(1)} s`;
  return `${v < 10 ? v.toFixed(1) : Math.round(v)} ms`;
}

export function fmtInt(v: number | null | undefined): string {
  return isNum(v) ? Math.round(v).toLocaleString("en-US") : DASH;
}

export function fmtPct(v: number | null | undefined, digits = 0): string {
  return isNum(v) ? `${(v * 100).toFixed(digits)}%` : DASH;
}

export function fmtUsd(v: number | null | undefined): string {
  if (!isNum(v)) return DASH;
  return v < 0.01 ? `$${v.toFixed(4)}` : `$${v.toFixed(3)}`;
}

export function toMs(ts: unknown): number {
  if (isNum(ts)) return ts;
  if (typeof ts === "string") {
    const n = Number(ts);
    if (Number.isFinite(n)) return n;
    const d = Date.parse(ts.endsWith("Z") || ts.includes("+") ? ts : `${ts}Z`);
    if (Number.isFinite(d)) return d;
  }
  return Date.now();
}

export function fmtClock(ts: number | null | undefined, withMs = true): string {
  if (!isNum(ts)) return DASH;
  const d = new Date(ts);
  const hms = d.toLocaleTimeString("en-US", { hour12: false });
  return withMs ? `${hms}.${String(d.getMilliseconds()).padStart(3, "0")}` : hms;
}

export function modeLabel(mode: string | undefined): string {
  if (mode === "quarantined") return "QUARANTINED";
  if (mode === "heightened") return "HEIGHTENED";
  return "ACTIVE";
}
