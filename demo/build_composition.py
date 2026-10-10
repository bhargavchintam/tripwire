#!/usr/bin/env python3
"""Build the HyperFrames composition for the Tripwire demo video (stdlib only).

Reads  demo/narration.json            (segments: id, act, clip, caption, text, screen)
       demo/out/timeline.json         (written by demo/tts.py: {"segments":[{id,start,duration,audio}],"total"})
Writes demo/video/index.html          (1920x1080, HyperFrames composition "tripwire")
Copies demo/out/audio/<id>.mp3 -> demo/video/audio/<id>.mp3
       docs/img/*.jpg (placeholders) -> demo/video/assets/placeholders/

If timeline.json is missing, durations are ESTIMATED from word count (2.6 words/s + 0.6 s per
[tag], gap_s from narration.json) and the composition is marked "estimated" (root attribute,
HTML comment and a visible badge) so it is never mistaken for the final cut.

Tracks
  0  operator screen clips  demo/video/clips/<segment.clip>  (muted). Missing clip -> the closest
     docs/img screenshot, full-frame with a slow zoom and a "placeholder - record <clip>" tag.
  1  narration audio        demo/video/audio/<id>.mp3 at the segment start.
  2  overlays               title card 0-3.2 s, act chip + caption lower-third per segment, end card.

Usage
  python3 demo/build_composition.py            # full composition
  python3 demo/build_composition.py --test     # 12 s test: first 2 segments (time-scaled to fit)
"""

from __future__ import annotations

import argparse
import html
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

DEMO = Path(__file__).resolve().parent
ROOT = DEMO.parent
VIDEO = DEMO / "video"
OUT = DEMO / "out"
IMG = ROOT / "docs" / "img"

W, H = 1920, 1080
TITLE_S = 3.2
END_CARD_S = 2.5
WORDS_PER_S = 2.6
TAG_S = 0.6
COMP_ID = "tripwire"
REPO_URL = "github.com/bhargavchintam/tripwire"
SPONSORS = "ClickHouse · AkashML · Semgrep · Guild · built with Claude Code"

# Screenshot -> keywords for "closest placeholder" matching (act x3, caption x2, screen x1).
SHOT_KEYWORDS = {
    "01-live-hold-denied-quarantined.jpg": ["live", "hold", "replay", "quarantin", "denied", "ticket", "orbit", "prevent", "trace", "honeytoken"],
    "02-fleet-heatmap-30m.jpg": ["fleet", "heatmap", "30m", "clickhouse", "events"],
    "03-proven-cure-guild-approval.jpg": ["cure", "guardrail", "approv", "incident", "sheet", "report", "verdict", "explain", "backtest"],
    "04-evidence.jpg": ["evidence", "proof", "precision", "confusion", "recall", "development cases"],
    "05-sponsors.jpg": ["sponsor", "semgrep", "stack", "guild agent"],
}
DEFAULT_SHOT = "01-live-hold-denied-quarantined.jpg"

# Visible region of each screenshot as fractions (x, y, w, h) of the image, 16:9.
# The docs/img screenshots predate removing the AkashML-vs-OpenAI cost comparison from the console,
# so these crops keep it out of frame: 01 drops the KPI footnote, 04 the cost card, 05 the Akash card.
SHOT_CROP = {
    "01-live-hold-denied-quarantined.jpg": (0.0, 0.0, 1.0, 0.9),
    "02-fleet-heatmap-30m.jpg": (0.0, 0.0, 1.0, 0.9),
    "03-proven-cure-guild-approval.jpg": (0.0, 0.05, 1.0, 0.9),
    "04-evidence.jpg": (0.0, 0.36, 0.5775, 0.5198),
    "05-sponsors.jpg": (0.0, 0.34, 0.5775, 0.5198),
}


# ---- helpers -------------------------------------------------------------------------------------


def esc(s: str) -> str:
    return html.escape(str(s), quote=True)


def spoken_words(text: str) -> int:
    return len(re.sub(r"\[[^\]]+\]", " ", text).split())


def n_tags(text: str) -> int:
    return len(re.findall(r"\[[^\]]+\]", text))


def f3(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def jpeg_size(path: Path) -> tuple[int, int]:
    """(width, height) from the JPEG SOF marker."""
    data = path.read_bytes()
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            i += 2
            continue
        seg_len = int.from_bytes(data[i + 2 : i + 4], "big")
        if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
            h = int.from_bytes(data[i + 5 : i + 7], "big")
            w = int.from_bytes(data[i + 7 : i + 9], "big")
            return w, h
        i += 2 + seg_len
    raise ValueError(f"no SOF marker in {path}")


def media_duration(path: Path) -> float | None:
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
            capture_output=True, text=True, check=True, timeout=30,
        ).stdout.strip()
        return float(out)
    except Exception:
        return None


def closest_shot(seg: dict, shots: list[str]) -> str | None:
    if not shots:
        return None
    fields = [(seg.get("act", ""), 3), (seg.get("caption", ""), 2), (seg.get("screen", ""), 1)]
    best, best_score = None, 0
    for name in shots:
        kws = SHOT_KEYWORDS.get(name.lower(), [Path(name).stem.lower()])
        score = sum(w * text.lower().count(k) for text, w in fields for k in kws)
        if score > best_score:
            best, best_score = name, score
    if best is None:
        best = next((s for s in shots if s.lower() == DEFAULT_SHOT), shots[0])
    return best


# ---- timing --------------------------------------------------------------------------------------


def load_timing(plan: dict) -> tuple[list[dict], float, bool]:
    """Return (segments with start/duration/audio, total narration seconds, estimated?)."""
    segs = plan["segments"]
    tl_path = OUT / "timeline.json"
    if tl_path.exists():
        tl = json.loads(tl_path.read_text())
        by_id = {s["id"]: s for s in tl["segments"]}
        missing = [s["id"] for s in segs if s["id"] not in by_id]
        if missing:
            sys.exit(f"timeline.json is missing segments {missing}: re-run demo/tts.py")
        out = []
        for s in segs:
            t = by_id[s["id"]]
            out.append({**s, "start": float(t["start"]), "duration": float(t["duration"]), "audio": t.get("audio")})
        total = float(tl.get("total") or max(x["start"] + x["duration"] for x in out))
        return out, total, False
    gap = float(plan.get("gap_s", 0.35))
    out, t = [], 0.0
    for s in segs:
        d = round(spoken_words(s["text"]) / WORDS_PER_S + TAG_S * n_tags(s["text"]), 3)
        out.append({**s, "start": round(t, 3), "duration": d, "audio": None})
        t += d + gap
    return out, round(t - gap, 3), True


def make_test(segs: list[dict], seconds: float) -> tuple[list[dict], float]:
    """First 2 segments, time-scaled so that narration + end card == `seconds`."""
    segs = segs[:2]
    content = seconds - END_CARD_S
    end = segs[-1]["start"] + segs[-1]["duration"]
    k = min(1.0, content / end) if end > 0 else 1.0
    out = [{**s, "start": round(s["start"] * k, 3), "duration": round(s["duration"] * k, 3)} for s in segs]
    return out, round(content if k < 1.0 else end, 3)


# ---- HTML ----------------------------------------------------------------------------------------

CSS = """
:root {
  --porcelain: #F6F5F2; --panel: #FFFFFF; --ink: #15161A; --muted: #5B5D66; --dim: #8A8C94;
  --line: rgba(21, 22, 26, 0.10); --indigo: #3D3AE8; --indigo-soft: rgba(61, 58, 232, 0.10);
  --sans: "Geist", "Geist Sans", -apple-system, BlinkMacSystemFont, "SF Pro Display", "Inter", "Helvetica Neue", Helvetica, Arial, sans-serif;
  --mono: "Geist Mono", ui-monospace, "SF Mono", SFMono-Regular, Menlo, Monaco, Consolas, monospace;
  --serif: "Instrument Serif", "New York", "Iowan Old Style", "Times New Roman", Georgia, serif;
}
* { margin: 0; padding: 0; box-sizing: border-box; }
html, body { width: 1920px; height: 1080px; overflow: hidden; background: var(--porcelain); }
body { font-family: var(--sans); color: var(--ink); -webkit-font-smoothing: antialiased; }
#stage { position: relative; width: 1920px; height: 1080px; overflow: hidden; background: var(--porcelain); }
.clip { position: absolute; inset: 0; width: 1920px; height: 1080px; }

/* track 0 */
.screen { z-index: 1; object-fit: contain; background: var(--porcelain); }
.ph { z-index: 1; overflow: hidden; background: var(--porcelain); }
.ph-zoom { position: absolute; inset: 0; transform-origin: 50% 50%; }
.ph-zoom img { position: absolute; display: block; max-width: none; }
.ph-tag {
  position: absolute; right: 40px; top: 40px; padding: 10px 16px; border-radius: 10px;
  font: 500 20px/1 var(--mono); color: #8A4B00; background: #FFF4E0; border: 1px solid #F2C98A;
  letter-spacing: 0.01em; box-shadow: 0 6px 18px rgba(21, 22, 26, 0.08);
}

/* track 2: per-segment overlay layer */
.ov { z-index: 10; pointer-events: none; }
.chip {
  position: absolute; left: 48px; top: 44px; display: inline-flex; align-items: center; gap: 12px;
  padding: 12px 20px 12px 16px; border-radius: 999px; background: rgba(255, 255, 255, 0.94);
  border: 1px solid var(--line); box-shadow: 0 8px 24px rgba(21, 22, 26, 0.10), 0 1px 2px rgba(21, 22, 26, 0.06);
  font: 600 22px/1 var(--mono); letter-spacing: 0.08em; text-transform: uppercase; color: var(--ink);
}
.chip .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--indigo); box-shadow: 0 0 0 5px var(--indigo-soft); }
.cap-wrap { position: absolute; left: 0; right: 0; bottom: 64px; display: flex; justify-content: center; }
.cap {
  max-width: 1560px; display: flex; align-items: stretch; border-radius: 18px; overflow: hidden;
  background: rgba(255, 255, 255, 0.96); border: 1px solid var(--line);
  box-shadow: 0 18px 48px rgba(21, 22, 26, 0.16), 0 2px 6px rgba(21, 22, 26, 0.06);
}
.cap .bar { width: 8px; background: var(--indigo); flex: none; }
.cap .txt { padding: 22px 34px 24px 28px; font: 600 40px/1.25 var(--sans); letter-spacing: -0.015em; color: var(--ink); white-space: nowrap; }

/* title + end cards */
.card { z-index: 20; display: flex; flex-direction: column; align-items: center; justify-content: center; text-align: center; }
.title-card { background: rgba(246, 245, 242, 0.95); }
.end-card { background: var(--porcelain); }
.eyebrow { font: 600 22px/1 var(--mono); letter-spacing: 0.22em; text-transform: uppercase; color: var(--indigo); margin-bottom: 36px; }
.brand { display: flex; align-items: center; gap: 30px; }
.logo { width: 112px; height: 112px; border-radius: 28px; background: var(--indigo); display: grid; place-items: center;
  box-shadow: 0 18px 40px rgba(61, 58, 232, 0.28); }
.logo svg { width: 64px; height: 64px; }
.wordmark { font: 700 168px/1 var(--sans); letter-spacing: -0.045em; color: var(--ink); }
.tagline { margin-top: 34px; font: 500 52px/1.2 var(--sans); letter-spacing: -0.02em; color: var(--muted); }
.tagline em { font-family: var(--serif); font-style: italic; font-weight: 400; color: var(--ink); font-size: 60px; }
.end-card .wordmark { font-size: 120px; }
.end-card .logo { width: 88px; height: 88px; border-radius: 22px; }
.end-card .logo svg { width: 50px; height: 50px; }
.url { margin-top: 44px; padding: 20px 34px; border-radius: 16px; background: var(--panel); border: 1px solid var(--line);
  font: 600 46px/1 var(--mono); letter-spacing: -0.01em; color: var(--ink); box-shadow: 0 10px 30px rgba(21, 22, 26, 0.08); }
.sponsors { margin-top: 40px; font: 500 30px/1.3 var(--sans); color: var(--muted); letter-spacing: -0.005em; }
.est { position: absolute; z-index: 30; right: 40px; bottom: 24px; font: 600 16px/1 var(--mono); letter-spacing: 0.08em;
  text-transform: uppercase; color: #8A4B00; background: #FFF4E0; border: 1px solid #F2C98A; padding: 8px 12px; border-radius: 8px; }
"""

LOGO_SVG = (
    '<svg viewBox="0 0 24 24" fill="none" stroke="#FFFFFF" stroke-width="2" stroke-linecap="round" '
    'stroke-linejoin="round" aria-hidden="true"><path d="M3 12h4l2-5 4 10 2-5h6"/></svg>'
)


def build(segs: list[dict], total_narr: float, estimated: bool, test: bool, shots: list[str]) -> tuple[str, list[str]]:
    total = round(total_narr + END_CARD_S, 3)
    warnings: list[str] = []
    track0, track1, track2, js = [], [], [], []

    used_shots: set[str] = set()
    for i, s in enumerate(segs):
        sid = re.sub(r"[^a-zA-Z0-9_-]", "_", s["id"])
        start, dur = float(s["start"]), float(s["duration"])
        # Visual covers the gap up to the next segment (no blank frames between clips).
        vis_end = float(segs[i + 1]["start"]) if i + 1 < len(segs) else total_narr
        vis_dur = round(max(dur, vis_end - start), 3)
        clip = s.get("clip") or f"{sid}.mp4"
        clip_path = VIDEO / "clips" / clip
        if clip_path.exists():
            media_start = float(s.get("clip_start", 0) or 0)
            cd = media_duration(clip_path)
            if cd is not None and cd - media_start + 0.05 < vis_dur:
                warnings.append(f"{clip}: {cd - media_start:.2f} s of footage for a {vis_dur:.2f} s slot (last frame holds / blank)")
            ms = f' data-media-start="{f3(media_start)}"' if media_start else ""
            track0.append(
                f'      <video id="v-{sid}" class="clip screen" data-start="{f3(start)}" data-duration="{f3(vis_dur)}" '
                f'data-track-index="0"{ms} src="clips/{esc(clip)}" muted playsinline></video>'
            )
        else:
            shot = closest_shot(s, shots)
            if shot is None:
                warnings.append(f"{clip}: missing and no docs/img screenshot to stand in")
                continue
            used_shots.add(shot)
            iw, ih = jpeg_size(VIDEO / "assets" / "placeholders" / shot)
            cx, cy, cw, ch = SHOT_CROP.get(shot.lower(), (0.0, 0.0, 1.0, min(1.0, (iw * 9 / 16) / ih)))
            # Scale so the crop rect covers 1920x1080, centred.
            k = max(W / (cw * iw), H / (ch * ih))
            img_w, img_h = iw * k, ih * k
            left = -(cx * iw * k) + (W - cw * iw * k) / 2
            top = -(cy * ih * k) + (H - ch * ih * k) / 2
            track0.append(
                f'      <div id="v-{sid}" class="clip ph" data-start="{f3(start)}" data-duration="{f3(vis_dur)}" data-track-index="0">\n'
                f'        <div class="ph-zoom" id="z-{sid}"><img src="assets/placeholders/{esc(shot)}" alt="" '
                f'style="width:{img_w:.1f}px;height:{img_h:.1f}px;left:{left:.1f}px;top:{top:.1f}px"></div>\n'
                f'        <div class="ph-tag">placeholder — record {esc(clip)}</div>\n'
                f"      </div>"
            )
            js.append(f'tl.fromTo("#z-{sid}", {{ scale: 1 }}, {{ scale: 1.06, duration: {f3(vis_dur)}, ease: "none" }}, {f3(start)});')

        # track 1: narration
        if s.get("audio"):
            src = OUT / s["audio"]
            dst = VIDEO / "audio" / f"{s['id']}.mp3"
            if src.exists():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                track1.append(
                    f'      <audio id="a-{sid}" data-start="{f3(start)}" data-duration="{f3(dur)}" data-track-index="1" '
                    f'data-volume="1" src="audio/{esc(dst.name)}"></audio>'
                )
            else:
                warnings.append(f"{src}: missing narration audio")

        # track 2: act chip + caption (after the title card on the first segment)
        ov_start = max(start, TITLE_S if i == 0 else start)
        ov_end = vis_end
        ov_dur = round(ov_end - ov_start, 3)
        if ov_dur > 0.8:
            track2.append(
                f'      <div id="o-{sid}" class="clip ov" data-start="{f3(ov_start)}" data-duration="{f3(ov_dur)}" data-track-index="2">\n'
                f'        <div class="chip" id="c-{sid}"><span class="dot"></span>{esc(s.get("act", ""))}</div>\n'
                f'        <div class="cap-wrap" id="l-{sid}"><div class="cap"><span class="bar"></span><span class="txt">{esc(s.get("caption", ""))}</span></div></div>\n'
                f"      </div>"
            )
            out_at = round(ov_end - 0.35, 3)
            js.append(f'tl.fromTo("#c-{sid}", {{ opacity: 0, x: -28 }}, {{ opacity: 1, x: 0, duration: 0.5, ease: "power3.out" }}, {f3(ov_start + 0.1)});')
            js.append(f'tl.fromTo("#l-{sid}", {{ opacity: 0, y: 28 }}, {{ opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }}, {f3(ov_start + 0.3)});')
            js.append(f'tl.to("#c-{sid}, #l-{sid}", {{ opacity: 0, duration: 0.3, ease: "power1.in" }}, {f3(out_at)});')

    # Title card (0 - 3.2 s) and end card (last 2.5 s)
    title_dur = min(TITLE_S, total_narr)
    track2.insert(0, (
        f'      <div id="title-card" class="clip card title-card" data-start="0" data-duration="{f3(title_dur)}" data-track-index="2">\n'
        f'        <div class="eyebrow" id="t-eyebrow">Prevent · Trip · Trace · Cure</div>\n'
        f'        <div class="brand" id="t-brand"><div class="logo">{LOGO_SVG}</div><div class="wordmark">Tripwire</div></div>\n'
        f'        <div class="tagline" id="t-tag">the <em>immune system</em> for AI-agent fleets</div>\n'
        f"      </div>"
    ))
    track2.append(
        f'      <div id="end-card" class="clip card end-card" data-start="{f3(total_narr)}" data-duration="{f3(END_CARD_S)}" data-track-index="2">\n'
        f'        <div class="brand" id="e-brand"><div class="logo">{LOGO_SVG}</div><div class="wordmark">Tripwire</div></div>\n'
        f'        <div class="url" id="e-url">{esc(REPO_URL)}</div>\n'
        f'        <div class="sponsors" id="e-sp">{esc(SPONSORS)}</div>\n'
        f"      </div>"
    )
    js[:0] = [
        'tl.fromTo("#t-eyebrow", { opacity: 0, y: 16 }, { opacity: 1, y: 0, duration: 0.5, ease: "power3.out" }, 0.15);',
        'tl.fromTo("#t-brand", { opacity: 0, y: 24, scale: 0.98 }, { opacity: 1, y: 0, scale: 1, duration: 0.7, ease: "power3.out" }, 0.3);',
        'tl.fromTo("#t-tag", { opacity: 0, y: 20 }, { opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }, 0.65);',
        f'tl.to("#title-card", {{ opacity: 0, duration: 0.5, ease: "power2.in" }}, {f3(max(0.0, title_dur - 0.5))});',
    ]
    js += [
        f'tl.fromTo("#end-card", {{ opacity: 0 }}, {{ opacity: 1, duration: 0.45, ease: "power2.out" }}, {f3(total_narr)});',
        f'tl.fromTo("#e-brand", {{ opacity: 0, y: 20 }}, {{ opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }}, {f3(total_narr + 0.2)});',
        f'tl.fromTo("#e-url", {{ opacity: 0, y: 20 }}, {{ opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }}, {f3(total_narr + 0.45)});',
        f'tl.fromTo("#e-sp", {{ opacity: 0, y: 16 }}, {{ opacity: 1, y: 0, duration: 0.6, ease: "power3.out" }}, {f3(total_narr + 0.7)});',
    ]

    timing = "estimated" if estimated else "measured"
    variant = "test" if test else "full"
    badge = []
    if estimated or test:
        label = " · ".join(x for x in ["test render" if test else "", "estimated timing — no TTS timeline" if estimated else ""] if x)
        badge = [
            f'      <div id="est-layer" class="clip" style="z-index:30" data-start="0" data-duration="{f3(total)}" data-track-index="3">'
            f'<div class="est">{esc(label)}</div></div>'
        ]
    note = (
        f"<!-- GENERATED by demo/build_composition.py — do not edit by hand. timing={timing} variant={variant} "
        f"segments={len(segs)} narration={total_narr:.3f}s total={total:.3f}s -->"
    )
    doc = "\n".join([
        "<!doctype html>",
        note,
        '<html lang="en" data-resolution="landscape">',
        "  <head>",
        '    <meta charset="UTF-8" />',
        '    <meta name="viewport" content="width=1920, height=1080" />',
        f'    <meta name="tripwire-timing" content="{timing}" />',
        "    <title>Tripwire demo</title>",
        '    <script src="assets/vendor/gsap.min.js"></script>',
        "    <style>" + CSS + "    </style>",
        "  </head>",
        "  <body>",
        f'    <div id="stage" data-composition-id="{COMP_ID}" data-start="0" data-duration="{f3(total)}" '
        f'data-width="{W}" data-height="{H}" data-timing="{timing}" data-variant="{variant}">',
        "      <!-- track 0: operator screen clips -->",
        *track0,
        "      <!-- track 1: narration -->",
        *track1,
        "      <!-- track 2: overlays -->",
        *track2,
        *badge,
        "    </div>",
        "    <script>",
        "      window.__timelines = window.__timelines || {};",
        "      const tl = gsap.timeline({ paused: true });",
        *("      " + line for line in js),
        f'      window.__timelines["{COMP_ID}"] = tl;',
        "    </script>",
        "  </body>",
        "</html>",
        "",
    ])
    return doc, warnings


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--test", action="store_true", help="12 s test variant: first 2 segments only, time-scaled")
    ap.add_argument("--test-seconds", type=float, default=12.0)
    ap.add_argument("--out", type=Path, default=VIDEO / "index.html")
    args = ap.parse_args()

    plan = json.loads((DEMO / "narration.json").read_text())
    segs, total_narr, estimated = load_timing(plan)
    if args.test:
        segs, total_narr = make_test(segs, args.test_seconds)

    # Placeholder screenshots are copied into the project (the renderer serves the project dir only).
    shots_dst = VIDEO / "assets" / "placeholders"
    shots_dst.mkdir(parents=True, exist_ok=True)
    shots = []
    for p in sorted(IMG.glob("*.jpg")):
        shutil.copy2(p, shots_dst / p.name)
        shots.append(p.name)
    (VIDEO / "clips").mkdir(parents=True, exist_ok=True)

    doc, warnings = build(segs, total_narr, estimated, args.test, shots)
    args.out.write_text(doc)

    total = total_narr + END_CARD_S
    print(f"wrote {args.out.relative_to(ROOT) if args.out.is_relative_to(ROOT) else args.out}")
    print(f"timing: {'ESTIMATED (no demo/out/timeline.json)' if estimated else 'measured (demo/out/timeline.json)'}"
          f"{' · TEST variant' if args.test else ''}")
    for s in segs:
        have = (VIDEO / "clips" / (s.get("clip") or "")).exists()
        print(f"  {s['start']:>7.2f}s  {s['duration']:>6.2f}s  {s['id']:<16} clip={'ok' if have else 'PLACEHOLDER'}"
              f"  audio={'ok' if s.get('audio') and (OUT / s['audio']).exists() else '-'}")
    print(f"narration {total_narr:.2f} s + end card {END_CARD_S} s = {total:.2f} s")
    for w in warnings:
        print(f"WARNING: {w}")
    limit = float(plan.get("hard_limit_s", 179))
    if not args.test and total > limit:
        print(f"WARNING: {total:.2f} s exceeds hard_limit_s {limit}")


if __name__ == "__main__":
    main()
