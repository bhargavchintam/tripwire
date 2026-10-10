"""Two-pass EBU R128 loudness normalization of the rendered demo (video stream copied untouched).

    python3 demo/loudnorm.py demo/out/tripwire-demo.mp4        # in place: -16 LUFS, -1.5 dBTP, LRA 11

HyperFrames mixes the narration at about -23.7 LUFS, quiet next to web video; the published cut is
normalized to -16 LUFS. Pass 1 measures, pass 2 applies a linear gain with those measurements.
Needs ffmpeg on PATH.
"""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

TARGET = "I=-16:TP=-1.5:LRA=11"


def main() -> None:
    src = Path(sys.argv[1])
    tmp = src.with_suffix(".norm.mp4")
    probe = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-i",
            str(src),
            "-af",
            f"loudnorm={TARGET}:print_format=json",
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    m = json.loads(re.search(r"\{[^{}]*\"input_i\"[^{}]*\}", probe.stderr, re.S).group(0))
    af = (
        f"loudnorm={TARGET}:measured_I={m['input_i']}:measured_TP={m['input_tp']}:measured_LRA={m['input_lra']}"
        f":measured_thresh={m['input_thresh']}:offset={m['target_offset']}:linear=true"
    )
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(src),
            "-c:v",
            "copy",
            "-af",
            af,
            "-ar",
            "48000",
            "-c:a",
            "aac",
            "-b:a",
            "192k",
            "-movflags",
            "+faststart",
            str(tmp),
        ],
        check=True,
    )
    tmp.replace(src)
    print(f"loudnorm: {m['input_i']} LUFS -> -16 LUFS ({src})")


if __name__ == "__main__":
    main()
