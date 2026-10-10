"""Generate the demo narration with the ElevenLabs text-to-speech API (Eleven v4, falling back to v3).

    ELEVENLABS_API_KEY=... uv run python demo/tts.py            # all segments
    uv run python demo/tts.py --only s05_trace                  # regenerate one segment
    uv run python demo/tts.py --dry-run                         # print the plan, call nothing
    uv run python demo/tts.py --from-files                      # audio already in demo/out/audio (ElevenLabs MCP)

Reads demo/narration.json; writes demo/out/audio/<segment>.mp3, demo/out/timeline.json and
demo/out/narration.mp3 (all segments joined with the configured gap). The key is read from the
environment or the repo's gitignored .env (ELEVENLABS_API_KEY) and is never printed. Fails if the
measured narration + the 2.5 s end card is not strictly under narration.json's hard_limit_s (179 s),
so the video stays under 3:00.
Needs ffmpeg/ffprobe on PATH (brew install ffmpeg).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEMO = ROOT / "demo"
OUT = DEMO / "out"
API = "https://api.elevenlabs.io/v1"
END_CARD_S = 2.5  # the HyperFrames end card after the last line


def api_key() -> str:
    key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    env = ROOT / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("ELEVENLABS_API_KEY="):
                key = line.split("=", 1)[1].strip().strip("\"'")
    if not key:
        sys.exit("ELEVENLABS_API_KEY is not set (export it, or add it to the gitignored .env)")
    return key


def request(method: str, path: str, key: str, body: dict | None = None, accept: str = "application/json") -> bytes:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{API}{path}", data=data, method=method)
    req.add_header("xi-api-key", key)
    req.add_header("accept", accept)
    if data is not None:
        req.add_header("content-type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return r.read()
    except urllib.error.HTTPError as e:  # surface the API's message, never the key
        detail = e.read().decode(errors="replace")[:300]
        raise RuntimeError(f"HTTP {e.code} on {method} {path.split('?')[0]}: {detail}") from None


def resolve_voice(key: str, cfg: dict) -> tuple[str, str]:
    voices = json.loads(request("GET", "/voices", key))["voices"]
    by_name = {v["name"].split(" - ")[0].strip().lower(): v for v in voices}
    for name in [cfg["voice_name"], *cfg.get("fallback_voice_names", [])]:
        v = by_name.get(name.lower())
        if v:
            return v["voice_id"], v["name"]
    sys.exit(f"none of the voices {[cfg['voice_name'], *cfg.get('fallback_voice_names', [])]} exist on this account")


def synthesize(key: str, voice_id: str, model: str, text: str, cfg: dict) -> bytes:
    body = {"text": text, "model_id": model, "voice_settings": cfg["voice_settings"]}
    return request("POST", f"/text-to-speech/{voice_id}?output_format={cfg['output_format']}", key, body, accept="audio/mpeg")


def duration_s(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "default=nw=1:nk=1", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip()
    return float(out)


def spoken_words(text: str) -> int:
    return len(re.sub(r"\[[^\]]+\]", " ", text).split())


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", help="regenerate just this segment id")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--from-files", action="store_true",
                    help="skip the API: build the timeline + narration track from demo/out/audio/*.mp3 "
                         "(e.g. generated with the ElevenLabs MCP)")
    args = ap.parse_args()

    plan = json.loads((DEMO / "narration.json").read_text())
    cfg, segs, gap = plan["tts"], plan["segments"], float(plan["gap_s"])
    if args.dry_run:
        for s in segs:
            print(f"{s['id']:<18} {spoken_words(s['text']):>3} words  {s['text'][:90]}")
        print(f"total spoken words: {sum(spoken_words(s['text']) for s in segs)}")
        return
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not found (brew install ffmpeg)")

    (OUT / "audio").mkdir(parents=True, exist_ok=True)
    model, voice_name = cfg["model_id"], cfg["voice_name"]
    if not args.from_files:
        key = api_key()
        voice_id, voice_name = (cfg["voice_id"], cfg["voice_name"]) if cfg.get("voice_id") else resolve_voice(key, cfg)
    for s in [] if args.from_files else segs:
        if args.only and s["id"] != args.only:
            continue
        try:
            audio = synthesize(key, voice_id, model, s["text"], cfg)
        except RuntimeError as exc:
            if model != cfg["fallback_model_id"] and ("model" in str(exc).lower() or "HTTP 4" in str(exc)):
                print(f"{model} refused ({exc}); falling back to {cfg['fallback_model_id']}")
                model = cfg["fallback_model_id"]
                audio = synthesize(key, voice_id, model, s["text"], cfg)
            else:
                raise
        (OUT / "audio" / f"{s['id']}.mp3").write_bytes(audio)
        print(f"{s['id']}: {len(audio):,} bytes")

    timeline, t = [], 0.0
    for s in segs:
        p = OUT / "audio" / f"{s['id']}.mp3"
        if not p.exists():
            sys.exit(f"missing {p} — run without --only first")
        d = round(duration_s(p), 3)
        timeline.append({"id": s["id"], "start": round(t, 3), "duration": d, "audio": f"audio/{s['id']}.mp3"})
        t += d + gap
    total = round(t - gap, 3)
    (OUT / "timeline.json").write_text(json.dumps(
        {"segments": timeline, "total": total, "model_id": model, "voice": voice_name, "measured": True}, indent=2) + "\n")

    # one narration track: segments joined with the configured silence between them
    lst = OUT / "concat.txt"
    silence = OUT / "gap.mp3"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=44100:cl=mono",
                    "-t", str(gap), "-q:a", "9", str(silence)], check=True)
    lines = []
    for i, s in enumerate(segs):
        lines.append(f"file 'audio/{s['id']}.mp3'")
        if i < len(segs) - 1:
            lines.append("file 'gap.mp3'")
    lst.write_text("\n".join(lines) + "\n")
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst),
                    "-ar", "44100", "-ac", "1", "-b:a", "128k", str(OUT / "narration.mp3")], check=True)

    print(f"voice: {voice_name} · model: {model} · total narration {total:.1f} s")
    for row in timeline:
        print(f"  {row['start']:>6.1f}s  {row['duration']:>5.1f}s  {row['id']}")
    video = total + END_CARD_S
    if video >= float(plan["hard_limit_s"]):
        sys.exit(f"TOO LONG: video would be {video:.1f} s (narration {total:.1f} s + {END_CARD_S} s end card) "
                 f"≥ {plan['hard_limit_s']} s — raise voice_settings.speed (max 1.2) or trim text")
    print(f"OK: narration {total:.1f} s + {END_CARD_S} s end card = {video:.1f} s < {plan['hard_limit_s']} s")


if __name__ == "__main__":
    main()
