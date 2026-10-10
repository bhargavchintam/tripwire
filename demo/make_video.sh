#!/usr/bin/env bash
# Build + render the Tripwire demo video with HeyGen HyperFrames.
#   1. demo/build_composition.py  -> demo/video/index.html (reads narration.json + out/timeline.json)
#   2. hyperframes render          -> demo/out/tripwire-demo.mp4
#   3. ffprobe check               -> fails if the video is longer than 179.5 s
# Needs: python3, Node >= 22, ffmpeg/ffprobe. Run demo/tts.py first (writes demo/out/timeline.json + audio);
# put the screen recordings in demo/video/clips/<segment.clip> (names in demo/narration.json).
set -euo pipefail

HF_VERSION="0.8.143"
MAX_S="179.5"
DEMO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_MP4="$DEMO/out/tripwire-demo.mp4"

command -v ffprobe >/dev/null || { echo "ffprobe missing: HOMEBREW_NO_AUTO_UPDATE=1 brew install ffmpeg" >&2; exit 1; }
[ -f "$DEMO/out/timeline.json" ] || echo "WARNING: demo/out/timeline.json missing -> ESTIMATED timing and NO narration audio (run demo/tts.py first)" >&2

python3 "$DEMO/build_composition.py"
mkdir -p "$DEMO/out"

cd "$DEMO/video"
npx --yes "hyperframes@$HF_VERSION" render \
  --output ../out/tripwire-demo.mp4 \
  --fps 30 \
  --quality high \
  --video-frame-format png \
  ${HF_EXTRA_ARGS:-}   # e.g. HF_EXTRA_ARGS="--workers 2 --browser-timeout 180" on a busy machine

DUR="$(ffprobe -v error -show_entries format=duration -of default=nw=1:nk=1 "$OUT_MP4")"
ffprobe -v error -show_entries stream=codec_type,codec_name,width,height -of compact=p=0 "$OUT_MP4"
echo "duration: ${DUR} s  ($OUT_MP4)"
if awk -v d="$DUR" -v m="$MAX_S" 'BEGIN { exit !(d > m) }'; then
  echo "FAIL: ${DUR} s is over the ${MAX_S} s limit" >&2
  exit 1
fi
echo "OK: under ${MAX_S} s"
