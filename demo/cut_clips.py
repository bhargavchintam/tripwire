"""Cut the recorded console session into one clip per narration segment.

    python3 demo/cut_clips.py            # after: cd demo/recorder && node record.mjs

Reads demo/out/record_log.json (segment boundaries, cut windows and the screencast frame list, all
in epoch seconds) and demo/out/timeline.json (measured narration). For each segment's accepted
take it keeps the frames between its start and end minus the cut windows (waits for a human in
Guild, Guild's agent, the copilot's model), then writes demo/video/clips/<clip> at exactly the
segment's slot: narration + the 0.35 s gap (+ the 2.5 s end card for the last one). Long takes
lose their tail (the action happens early in each segment); short ones hold their last frame.
Output: H.264, 1920x1080, 30 fps, yuv420p. Needs ffmpeg on PATH.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "demo" / "out"
FRAMES = OUT / "frames"
CLIPS = ROOT / "demo" / "video" / "clips"
END_CARD_S = 2.5
FPS = 30


def keep_ranges(start: float, end: float, cuts: list[list[float]]) -> list[tuple[float, float]]:
    ranges, t = [], start
    for a, b in sorted(cuts):
        a, b = max(a, start), min(b, end)
        if b <= a:
            continue
        if a > t:
            ranges.append((t, a))
        t = max(t, b)
    if t < end:
        ranges.append((t, end))
    return ranges


def frame_list(frames: list[list], ranges: list[tuple[float, float]]) -> list[tuple[str, float]]:
    """(file, seconds on screen) for every frame visible inside the kept ranges, in order."""
    ts = [f[0] for f in frames]
    out: list[tuple[str, float]] = []
    for a, b in ranges:
        # the frame on screen at `a` is the last one captured at or before it
        i = max(0, next((k for k, t in enumerate(ts) if t > a), len(ts)) - 1)
        while i < len(frames) and ts[i] < b:
            shown_from = max(ts[i], a)
            shown_to = min(ts[i + 1] if i + 1 < len(ts) else b, b)
            if shown_to > shown_from:
                out.append((frames[i][1], shown_to - shown_from))
            i += 1
    return out


def main() -> None:
    log = json.loads((OUT / "record_log.json").read_text())
    timeline = {s["id"]: s for s in json.loads((OUT / "timeline.json").read_text())["segments"]}
    plan = json.loads((ROOT / "demo" / "narration.json").read_text())
    gap = float(plan["gap_s"])
    clip_of = {s["id"]: s["clip"] for s in plan["segments"]}
    plan_seg = {s["id"]: s for s in plan["segments"]}
    last_id = plan["segments"][-1]["id"]
    frames = log["frames"]
    if not frames:
        sys.exit("no frames recorded")
    CLIPS.mkdir(parents=True, exist_ok=True)

    takes = {s["id"]: s for s in log["segments"] if s["ok"]}  # the accepted take of each segment
    for sid, seg in timeline.items():
        take = takes.get(sid)
        if not take:
            sys.exit(f"{sid}: no accepted take in the log")
        target = seg["duration"] + gap + (END_CARD_S if sid == last_id else 0.0)
        skip = float(plan_seg[sid].get("clip_skip_s", 0) or 0)  # trim a glitch at the head (narration.json)
        items = frame_list(frames, keep_ranges(take["start"] + skip, take["end"], take["cuts"]))
        have = sum(d for _, d in items)
        lst = OUT / f"concat_{sid}.txt"
        lines = ["ffconcat version 1.0"]
        for f, d in items:
            lines += [f"file '{(FRAMES / f).as_posix()}'", f"duration {d:.4f}"]
        lines.append(f"file '{(FRAMES / items[-1][0]).as_posix()}'")  # concat demuxer: last entry repeated
        lst.write_text("\n".join(lines) + "\n")
        pad = max(0.0, target - have)
        vf = f"fps={FPS},scale=1920:1080:flags=lanczos,format=yuv420p"
        if pad > 0:
            vf += f",tpad=stop_mode=clone:stop_duration={pad + 0.1:.3f}"
        dest = CLIPS / clip_of[sid]
        subprocess.run(
            ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst), "-vf", vf,
             "-t", f"{target:.3f}", "-c:v", "libx264", "-preset", "medium", "-crf", "17", "-pix_fmt", "yuv420p",
             "-movflags", "+faststart", "-an", str(dest)],
            check=True,
        )
        cut = sum(b - a for a, b in take["cuts"])
        note = f"padded {pad:.1f} s" if pad > 0 else f"trimmed {have - target:.1f} s"
        print(f"{sid:<16} take {take['attempt']}  kept {have:5.1f} s  cut {cut:5.1f} s  -> {target:5.2f} s ({note})  {dest.name}")


if __name__ == "__main__":
    main()
