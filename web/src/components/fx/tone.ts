// Tone = the one colour vocabulary of the UI. Hue encodes STATE, never decoration:
//   ok = green (normal / allowed / passed) · held = amber (held / heightened / pending)
//   bad = red (denied / quarantined / open incident) · model = violet (AkashML / model verdicts)
//   info = blue (ClickHouse / queries / neutral facts) · paper = warm editorial sheet (not a state)
//   neutral = no tint. The brand indigo is NOT a tone: it is only for actions, active nav, focus, links.
export type Tone = "ok" | "held" | "bad" | "model" | "info" | "paper" | "neutral";

/** CSS colour value for a tone (inline styles, `--ink`, SVG strokes). Adapts inside .inset-night. */
export const toneVar: Record<Tone, string> = {
  ok: "var(--color-ok)",
  held: "var(--color-held)",
  bad: "var(--color-bad)",
  model: "var(--color-model)",
  info: "var(--color-info)",
  paper: "var(--color-paper-2)",
  neutral: "var(--color-muted)",
};

/** Soft tint fill colour per tone (CSS value). */
export const toneSoftVar: Record<Tone, string> = {
  ok: "var(--color-ok-soft)",
  held: "var(--color-held-soft)",
  bad: "var(--color-bad-soft)",
  model: "var(--color-model-soft)",
  info: "var(--color-info-soft)",
  paper: "var(--color-paper)",
  neutral: "var(--color-neutral-soft)",
};

/** Text colour utility per tone. (`paper` emphasis renders in ink on the light canvas.) */
export const toneText: Record<Tone, string> = {
  ok: "text-ok",
  held: "text-held",
  bad: "text-bad",
  model: "text-model",
  info: "text-info",
  paper: "text-fg",
  neutral: "text-muted",
};

/** Legacy name: "glow" class per tone, now a whisper of tint washing down a white card. */
export const toneGlow: Record<Tone, string> = {
  ok: "glow-ok",
  held: "glow-held",
  bad: "glow-bad",
  model: "glow-model",
  info: "glow-info",
  paper: "glow-paper",
  neutral: "surface",
};

/** Tinted 1px border per tone. */
export const toneBorder: Record<Tone, string> = {
  ok: "border-ok-line",
  held: "border-held-line",
  bad: "border-bad-line",
  model: "border-model-line",
  info: "border-info-line",
  paper: "border-paper-2",
  neutral: "border-line",
};

/** Soft tint background utility per tone. */
export const toneSoft: Record<Tone, string> = {
  ok: "bg-ok-soft",
  held: "bg-held-soft",
  bad: "bg-bad-soft",
  model: "bg-model-soft",
  info: "bg-info-soft",
  paper: "bg-paper",
  neutral: "bg-neutral-soft",
};

/** Text + soft fill + tinted border in one class (badges, chips, callouts). */
export const toneTint: Record<Tone, string> = {
  ok: "tint-ok",
  held: "tint-held",
  bad: "tint-bad",
  model: "tint-model",
  info: "tint-info",
  paper: "border-paper-2 bg-paper text-ink",
  neutral: "tint-neutral",
};
