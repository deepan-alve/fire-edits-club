"""Convert a video to 9:16 with blur-padded background + end-card watermark.

Usage:
    python transformer.py <input.mp4> <output.mp4>
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

FONT_PATH = os.environ.get("FONT_PATH", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")
HANDLE = "@fireeditsclub"
ENDCARD_SECONDS = 0.4
OUTPUT_W, OUTPUT_H = 1080, 1920


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(result.stdout)["format"]["duration"])


def transform(src: Path, dst: Path) -> None:
    if not src.exists():
        sys.exit(f"missing input: {src}")
    if not Path(FONT_PATH).exists():
        sys.exit(f"font missing: {FONT_PATH}")
    dst.parent.mkdir(parents=True, exist_ok=True)

    duration = probe_duration(src)
    endcard_start = duration

    drawtext = (
        f"drawtext=fontfile={FONT_PATH}:"
        f"text='{HANDLE}':"
        f"fontcolor=white:fontsize=96:"
        f"box=1:boxcolor=black@0.75:boxborderw=24:"
        f"x=(w-text_w)/2:y=(h-text_h)/2:"
        f"enable='gte(t,{endcard_start})'"
    )
    darken = (
        f"drawbox=x=0:y=0:w=iw:h=ih:color=black@0.92:t=fill:"
        f"enable='gte(t,{endcard_start})'"
    )
    filter_complex = (
        f"[0:v]split=2[bg0][fg0];"
        f"[bg0]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_W}:{OUTPUT_H},gblur=sigma=30[bg];"
        f"[fg0]scale={OUTPUT_W}:-2:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[main];"
        f"[main]tpad=stop_mode=clone:stop_duration={ENDCARD_SECONDS}[padded];"
        f"[padded]{darken},{drawtext}[outv];"
        f"[0:a]apad=pad_dur={ENDCARD_SECONDS}[outa]"
    )

    cmd = [
        "ffmpeg", "-y", "-loglevel", "warning", "-stats",
        "-i", str(src),
        "-filter_complex", filter_complex,
        "-map", "[outv]", "-map", "[outa]",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22",
        "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        str(dst),
    ]
    print(f"  duration: {duration:.2f}s + {ENDCARD_SECONDS}s endcard")
    print(f"  running ffmpeg...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"ffmpeg failed (exit {result.returncode}):\n{result.stderr[:2000]}")
    if result.stderr.strip():
        print(result.stderr.strip())

    out_meta = subprocess.run(
        ["ffprobe", "-v", "error",
         "-select_streams", "v:0",
         "-show_entries", "stream=width,height,duration",
         "-of", "json", str(dst)],
        capture_output=True, text=True, check=True,
    )
    s = json.loads(out_meta.stdout)["streams"][0]
    print(f"  output: {s.get('width')}x{s.get('height')}, duration={s.get('duration')}s, file_size={dst.stat().st_size:,} bytes")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("input")
    ap.add_argument("output")
    args = ap.parse_args()
    transform(Path(args.input), Path(args.output))


if __name__ == "__main__":
    main()
