# Tripwire UI v3: "Paper & Signal" (premium light)

Builders: this file plus `src/components/fx/*` and `src/components/ui/*` are the whole system.
Read the honesty rules first. Judges watch on a projector (1280x720, often presenter mode).

Feel: warm porcelain paper, white cards, ink type, ONE indigo accent, state colours only for state,
soft layered shadows, calm physical motion. One dark "night sky" card on the Live tab. No glows, no
neon, no gradients on text, no blobs, no emoji, no rainbow.

---

## 1. Honesty rules (judged hackathon, non-negotiable)

- Every number, agent, event and capability comes from the existing hooks/state/API (`useTripwire`,
  `useEvidence`, `usePolicy`, `useHeatmap`, `useFleetTop`, `useIncident`, `useAudit`, `api.*`).
  Never invent, hard-code, round up or sample a value to fill a design.
- Unmeasured = DASH `—` (`DASH` from `lib/format`, rendered `text-dim`). `<Count>` does it. Never `0`.
- Animations fire on REAL state changes only (mount, tab switch, a new SSE `tool_event`, a value that
  changed, a mode that changed). No timers faking activity. Allowed loops: LIVE dot ring (stream is
  live), `animate-quarantine` / `animate-held` (really quarantined / held), `breathe` on a state that
  is really ongoing, spinners / `.skeleton` / `animate-scan` while a request is really in flight.
- Decoration never looks like data: stars + grain live only inside the night panel (and a 2% paper
  grain on the canvas); never inside charts; no fake sparklines/bars/planets.
- Keep every feature, button, label meaning, tooltip "Source: …", shortcut (R, X, H, 0, P, ⌘K/Ctrl-K),
  presenter mode, MOCK banner, Guild buttons, incident sheet, proven-cure flow.
- Never click Replay/Reset/Restore/Prove/Approve/Guild buttons or press R/0/H/X while testing.

---

## 2. Tokens (`src/index.css` `@theme static`)

All old names still exist with new values, so `text-muted`, `bg-panel-2`, `border-line` keep working.

| Token | Value | Use |
|---|---|---|
| `bg` / `bg-2` | `#F6F5F2` / `#F1EFEA` | porcelain page canvas |
| `panel` | `#FFFFFF` | card surface |
| `panel-2` | `#FAF9F7` | raised / inset wells, table header strip, code |
| `panel-3` | `#F1F0EC` | hover wells, neutral fills, skeleton base |
| `line` / `line-strong` | `#E7E5E0` / `#D9D6CF` | hairline / hover + visible divider |
| `edge` / `edge-strong` | = line / line-strong | legacy glass borders |
| `fg` | `#15161A` | ink text |
| `muted` | `#565A61` | secondary text (7:1) |
| `dim` | `#7A7E86` | tertiary text, eyebrows, DASH (4:1; spec #8B8E95 darkened for projector) |
| `brand` / `brand-hover` | `#3D3AE8` / `#2F2CC9` | THE accent: primary button, active nav, focus, links |
| `brand-soft` / `brand-line` | `#EEEEFE` / `#CFCEFA` | active tab pill, brand tint |
| `paper` / `paper-2` / `ink` | `#FBF8F1` / `#EFE8D8` / `#15161A` | warm editorial sheet (incident report), ink on it |
| `night` / `night-2` | `#0B0D12` / `#12151C` | the one dark panel |

State: text colour `X`, soft fill `X-soft`, tinted 1px border `X-line` (`text-ok bg-ok-soft border-ok-line`).

| Tone | Means | text | soft | line |
|---|---|---|---|---|
| `ok` | normal, allowed, passed, restored | `#0E8A5F` | `#E9F7F0` | `#BFE5D3` |
| `held` | held, heightened, pending, hold ON | `#A86400` | `#FFF4DF` | `#F1D9A8` |
| `bad` | denied, quarantined, open incident | `#C8281E` | `#FDECEA` | `#F5C4BF` |
| `model` | AkashML / model verdict / LLM | `#6A4FD0` | `#F2EFFD` | `#D8CFF7` |
| `info` | ClickHouse / queries / neutral fact | `#0B67C2` | `#E9F2FC` | `#C3DBF4` |
| `neutral` | no state | `#565A61` | `#F1F0EC` | `#E2DFD8` |

Opacity forms still work and look right on white (`bg-ok/10`, `border-bad/40`), but prefer the
`-soft`/`-line` tokens or `.tint-*`. Solid state fills take WHITE text (`bg-bad text-white`,
`bg-held text-white`), never dark text.

Type: `font-sans` Geist (UI), `font-mono` Geist Mono (ONLY ids, SQL, code_ref, numbers-with-units in
tables, eyebrows, kbd), `font-serif` Instrument Serif (page titles only, weight 400, one italic word).
Max 2 families per view (sans + one of mono/serif in a card). Scale (px): **12 / 13 / 14 / 16 / 20 /
28 / 40 / 56**. Body 14px/1.5 (body default). Captions 12. Display numbers 40–56px Geist 600
`tracking-[-0.03em]` (`.num-display`). Every number is `tabular-nums` (default on mono, tables, Count).

Radius: cards `rounded-[var(--radius-card)]` (16px), controls `rounded-[var(--radius-control)]`
(10px: buttons, inputs, kbd wells), pills `rounded-full` (chips, badges, nav, status), inner wells
`rounded-xl` (12px).

Shadows (Tailwind scale overridden): `shadow-sm` (resting), `shadow-md` (hover), `shadow-lg`
(sheets/palette/toasts). `shadow-card` = sm + 1px inner white top highlight (cards), `shadow-lift` =
hover card, `shadow-pop` = lg. Never coloured glows.

Layout: 8px grid. Shell `mx-auto max-w-[1440px] px-6` (24px gutters). Card padding 20px (`p-5`),
large hero cards 24px (`p-6`). Grid gaps `gap-4` (16) inside rows, `gap-6` (24) between sections.

---

## 3. Utility classes (`@layer components`; any Tailwind utility on the same element wins)

| Class | What |
|---|---|
| `surface` | white panel bg (Card/GlowCard) |
| `surface-raised` | `panel-2` well + hairline + 12px radius (proof frames, code, stat wells) |
| `surface-card` | full card look in one class (white, hairline, 16px, shadow-card) |
| `tint-ok/held/bad/model/info/neutral/brand` | state text + soft fill + tinted border-color (add `border`) |
| `ring-tint-ok/held/bad/model/info` | 3px soft state ring + card shadow (the ONE card that matters) |
| `glow-*` (legacy) | now a whisper of state tint washing down a white card; pair with `border-X-line` |
| `glass` | translucent white + blur + shadow-sm (floating nav). Add `border border-line`. |
| `lift` / `press` | hover: -1px + shadow-lift; active: scale .98 (reduced-motion: off) |
| `hairline` | 1px `line` divider |
| `eyebrow` / `eyebrow-lg` | Geist Mono 12px / 13px, uppercase, 0.08em, `dim` |
| `display` | Instrument Serif, tight; `<em>` inside = the italic word |
| `num-display` | Geist 600, -0.03em, tabular, leading 1 (size it: `text-[40px]`) |
| `theme-night` | token scope: everything inside uses the dark palette (text-fg light, etc.) |
| `inset-night` | the night-sky card: deep ink gradient, faint CSS stars, grain, 16px, shadow-lg |
| `skeleton` | shimmer placeholder (only while really loading) |
| `kbd` | key-hint chip, adapts to current text colour |
| `grain` / `grain-layer` | paper grain (page, 2%) / inside a positioned card (light noise inside night) |
| `ink` + `ink-mark`, `rule-draw` | used by `<InkMarker>` / `<RuleDraw>` |
| `animate-held`, `animate-quarantine` | soft state ring pulses (state-driven only) |
| `focus-ring` | 2px brand ring with white offset (default `:focus-visible` is already brand) |

Animations: `animate-rise-in` (420ms), `fade-in` (240ms), `pop-in` (180ms), `rule-draw` (600ms),
`ink-sweep`, `breathe`, `pulse-ring`, `scan`, `shimmer`, `sheet-in/out`, `overlay-in/out`.
Stagger CSS ones with `[animation-delay:40ms]`.

`text-paper` is aliased to ink on the light canvas (legacy safety net). Don't use it in new code.
Don't use `bg-white/[x]` hovers (invisible on white): use `hover:bg-panel-3` / `hover:bg-panel-2`.

---

## 4. Components

### fx (`import { … } from "../components/fx"`)

```tsx
<SectionHeader eyebrow="Live · tool-call stream" pre="Live" em="fleet" description="…" actions={<Button…/>} />
```
Page header: 13px mono eyebrow, 40px serif h1 (`size` sm 28 / md 40 / lg 48), 15px muted line, actions
bottom-aligned right, self-drawing hairline. `eyebrowTone` adds a state dot.

```tsx
<GlowCard tone="bad" glow="strong" interactive reveal={2} eyebrow={<><Bot/> agent</>} title="deploy-bot"
  description="Quarantined 13:36:28" actions={<Badge variant="bad">Quarantined</Badge>} footer={…}>…</GlowCard>
```
THE card. White, hairline, 16px, shadow-card. `tone` + `glow`: `none` plain; `soft` (default) tinted
hairline + 2px top accent bar; `strong` + tint wash + 3px tint ring (one per view). `breathe` only while
ongoing. `interactive` = hover lift + press (add `role="button" tabIndex={0} onKeyDown`). `night` = the
dark night-sky card (stars scoped inside, night palette). `reveal={i}` = staggered spring entrance.
Slots: `chips`, `eyebrow`, `title` (16px 600), `description` (13px muted), `actions`, `footer`,
`bodyClassName`. Padding 20px.

```tsx
<NightPanel className="min-h-[360px] p-6"><FleetOrbit … /></NightPanel>
```
The ONE dark moment (Live fleet-flow/orbit). Deep ink, faint twinkling `<Starfield/>` + grain INSIDE.
Inside it `text-fg`, `text-muted`, `border-line`, `toneVar.*`, `tint-*` automatically use night values.

```tsx
<Count value={ev?.hold_decision_ms} suffix=" ms" />   <Count value={n} className="num-display text-[40px]" />
```
NumberFlow count-up (520ms ease-out roll) only when the real value changes; DASH when unmeasured.

```tsx
<CopyId value={inc.id} />   <CopyId value={agent} label={<span className="font-sans font-semibold">{agent}</span>} />
```
Click-to-copy id: mono, copy glyph on hover, check + toast "Copied". Stops propagation (safe in rows).

```tsx
<Kbd>R</Kbd>   <Skeleton className="h-4 w-24" />   <TabSkeleton />
```
`Kbd` (also re-exported from `ui/button`). `Skeleton` shimmer only while really loading.

```tsx
<InkMarker tone="held" trigger={v} active={isNum(v)}>…</InkMarker>
```
Subtle highlighter stroke (≤20% tint, lower 1/3 of the glyphs); re-sweeps when `trigger` changes. Use on
at most the 3 hero numbers (hold decision ms, backtest ms, events stored).

```tsx
<Chip>deploy-bot</Chip>  <Chip tone="model" dot mono size="md">Llama-3.3-70B</Chip>
```
Tones `glass` (white + hairline + shadow-sm), `paper`, `neutral`, `ok/held/bad/model/info` (tints).
Sizes sm 12px / md 13px. Max ~3 chips per card.

`<Eyebrow dot tone="info" size="lg">`, `<DisplayTitle pre em post size emTone>` (italic word stays ink;
tint it only when the word IS that state), `<Reveal index>` (spring y 8px + 240ms fade, 40ms stagger,
once in view), `<RuleDraw tone key>` (re-key on real change to redraw; timelines), `<Starfield>`
(now fills its positioned parent; `fixed` opt-in, don't). Motion constants: `SPRING`
(300/30), `SPRING_SOFT` (260/32), `EASE_OUT`, `STAGGER` (0.04), `STAGGER_CAP`.
Tone helpers (`fx/tone.ts`): `toneVar`, `toneSoftVar`, `toneText`, `toneBorder` (→ `border-X-line`),
`toneSoft` (→ `bg-X-soft`), `toneTint` (→ `tint-X`), `toneGlow` (legacy).

### ui primitives (same exports/variants as before, new look)

- **Button** (10px radius, 14px 500, hover -1px, press .98): `default` = solid indigo (primary, ONE per
  view), `outline`/`secondary` = white + hairline + shadow-sm, `ghost`, `danger` = solid red + white
  text (attack CTA), state tints `held` `ok` `bad` `model`, `paper`, `link`. Sizes `sm` 32 / `default`
  36 / `lg` 44 / `icon` 36. `<ButtonArrow />`, `<Kbd>`.
- **Badge** (22px pill, 12px 500): `default` (white), `ok held bad model info` tints, `muted` neutral,
  `paper`, `brand`. Always with an icon or a word, never colour alone.
- **Card** / CardHeader / CardTitle (14px 600 ink, icon 16px dim) / CardDescription / CardContent /
  CardFooter: white, hairline, 16px, shadow-card, 20px padding.
- **Tabs**: floating glass pill list; trigger = pill, active = indigo tint (shell renders a shared
  `layoutId="tab-pill"` spring pill). TabsContent fades in (240ms).
- **Sheet**: inset white panel (20px radius, shadow-lg) slides 24px + fades; backdrop ink 22% + 6px
  blur; close button white control with tooltip; focuses the panel on open (no tooltip pop).
- **Table**: sentence-case 12px `dim` headers on a `panel-2` strip, hairline rows, whisper zebra,
  `hover:bg-panel-3/70`, tabular nums, first/last cells padded 20px to align with card padding.
- **Tooltip**: dark ink bubble, 12px, arrow, `side`/`align`/`delay` props; night token scope inside.
- **Segmented** (`ui/segmented.tsx`, new): radio group with a sliding spring thumb; option `tone`
  colours the thumb (Hold ON = held). `value={null}` = unknown (no thumb).
- **Switch**: light toggle (checked = held). Prefer Segmented for ON/OFF modes.
- **Toasts**: sonner restyled in CSS: white, hairline, shadow-lg, state-coloured icon + 3px state edge.

---

## 5. Motion spec (premium = calm + physical)

- Springs: stiffness 260–320, damping 28–32 (`SPRING`, `SPRING_SOFT`). Tweens: 180–240ms ease-out
  (`EASE_OUT` / `--ease-out-soft`).
- Entrances: ONCE per view, staggered 30–50ms (`reveal={i}` / `<Reveal index>`), y 8px. Never loop.
- Numbers: NumberFlow count-up only when the value changes.
- Lines: hairlines / timelines draw in (`RuleDraw`, 600ms). Re-key to redraw on a real change.
- Nav: shared `layoutId` pill (tabs, Segmented thumb).
- Hover: 1–2px lift + shadow sm→md. Press: scale .98. Focus: 2px indigo ring.
- Loading: `.skeleton` shimmer sized like the content (never a blank area).
- Sheet / palette: slide + fade, backdrop blur. Toasts: white, shadow-lg, state icon.
- `prefers-reduced-motion`: lifts, staggers, drift, shimmer and loops off; state changes land instantly
  (handled globally + `<MotionConfig reducedMotion="user">`).

## 6. Interaction patterns

- Tooltip on every icon button and badge (`<Tooltip content=…>`); include the shortcut as `<span className="kbd">H</span>`.
- Copy-to-clipboard on incident ids / agent ids: `<CopyId>` (toast "Copied").
- Keyboard hints as kbd chips: `<Kbd>R</Kbd>` inside buttons, palette rows, header controls.
- Event rows: `hover:bg-panel-3/70`; agent filter chips toggle with `tint-brand` when active.
- Agent cards: reveal Restore on hover/focus (`opacity-0 group-hover/card:opacity-100 group-focus-within/card:opacity-100`),
  but ALWAYS visible when the agent is quarantined (it's the call to action) and in presenter mode.
- Hold mode: Segmented ON/OFF in the header (H toggles). ⌘K palette with grouped results.

---

## 7. Layout

### Shell (`App.tsx` + `Header.tsx`, done)
- `MockBanner` (solid held, white text) is a direct child of the full-height shell: `sticky top-0`,
  always visible; it sets `--banner-h` so the nav sticks below it.
- App bar (white/70, bottom hairline): logo mark + "Tripwire" (Geist 600 20px) + mono eyebrow "The
  immune system for AI-agent fleets" | events/s, LIVE pill, Hold segmented, Presenter, ⌘K.
  Presenter: compact padding, no eyebrow, no ⌘K button (⌘K still works).
- Floating sticky nav: centred glass pill, active tab = indigo-tint spring pill, open-incident count
  badge (solid red, white). Tab content starts 16px below the nav.
- Lazy tabs show `<TabSkeleton />` while their chunk loads.

### Bento grid
- `grid grid-cols-12 gap-4`; sections `flex flex-col gap-6`. Intentional spans; no orphan card (if a
  row has a leftover, widen the last card or fold it into its neighbour).
- KPI tiles: label 12px eyebrow, value `.num-display` 28–40px (56 for the ONE hero number), unit 13px
  muted after the number, `whitespace-nowrap`. Make long values (e.g. "p50 / p95") shrink:
  `text-[clamp(1.25rem,2.2vw,1.75rem)]` and `min-w-0` on the tile so nothing clips at 1280x720.
- Presenter (html 125%): the first agent card / attack chain must be visible at 1280x720. Keep hero
  rows ≤ ~260px tall, drop descriptions, prefer fewer, larger cards.

### Per tab
- **Live**: SectionHeader (Replay = `danger` lg + `<Kbd>R</Kbd>`, Reset = `outline` + `<Kbd>0</Kbd>`).
  The fleet-flow / orbit is the ONE `<GlowCard night>` / `<NightPanel>`; KPIs white tiles; agent cards
  toned by mode (`glow="strong"` + `animate-quarantine` only when quarantined); attack chain timeline with
  a drawn spine; event table + incident feed.
- **Incidents**: outbreak card (held when an outbreak exists) then the table card; rows open the sheet;
  ids via `<CopyId>`.
- **Fleet**: heatmap card full width (info tone, sequential blue ramp on white), top-risky list.
- **Evidence**: 3 hero GlowCards (latency / quality / cost), charts on white (grid `line`, axis `dim`,
  series in tone vars), evidence table, receipts in `surface-raised` wells.
- **Policy**: copilot card (model tone), sections as a 2-col bento, backtest card.
- **Sponsors**: 3-col bento; proof in `surface-raised` wells. Semgrep receipt = 223 files · 13 findings
  · 0 open true positives (#1 fixed), per `semgrep/FINDINGS.md`.

### Charts (Recharts)
Grid `var(--color-line)`, ticks `var(--color-dim)` 12px Geist Mono, cursor `rgb(21 22 26 / 0.04)`,
series `toneVar[…]`, tooltip = white card + hairline + shadow-lg.

## 8. Do / don't
- Do: hairlines, generous whitespace, one accent per card, serif only for page titles, mono only for
  ids/SQL/code/units, icon + text with every state colour, 16/18px lucide icons `strokeWidth={1.75}`.
- Don't: gradients on text, rainbow, emoji, decorative blobs/glows, dark heavy shadows
  (`rgb(0 0 0/0.9)`), `bg-white/[x]` hovers, white-on-white glass, all-caps paragraphs, pill soup,
  more than 2 families per view, a second dark card, new npm deps, `localStorage` for data.
- Don't edit `hooks/useTripwire.ts`, `hooks/usePresenter.ts`, `lib/api.ts`, `lib/types.ts`. Don't run
  `npm run build` (dist is live). Typecheck: `npx tsc --noEmit`.
