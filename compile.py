"""Build a YouTube long-form compilation from N archive tweets.

Layout:
    [2s intro card]
    [tweet 1 video, normalized to 1920x1080]
    [tweet 2 video, normalized to 1920x1080]
    ...
    [3s outro card "subscribe"]

All clips are normalized to 1920x1080 (YT-friendly landscape). Vertical clips
get blur-padded; landscape clips get scaled-to-fit. Audio is preserved.
Returns the output path + a list of chapter markers (seconds, label).
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from downloader import download_tweet_media

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "seen.sqlite"
COMPILE_ROOT = ROOT / "data" / "compilations"
FONT_PATH = os.environ.get("FONT_PATH", "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf")

OUTPUT_W, OUTPUT_H = 1920, 1080
INTRO_SECONDS = 2.0
OUTRO_SECONDS = 3.0


@dataclass
class Clip:
    tweet_id: int
    label: str
    path: Path
    duration: float


def probe_duration(path: Path) -> float:
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "json", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(json.loads(r.stdout)["format"]["duration"])


def _label_for_tweet(content: str | None, tweet_id: int) -> str:
    if not content:
        return f"Edit #{tweet_id}"
    label = content.strip().split("\n")[0]
    # Strip trailing emojis/punctuation for cleaner chapter titles
    label = label.rstrip(" 🔥.,!?-")
    if len(label) > 60:
        label = label[:57].rstrip() + "..."
    return label or f"Edit #{tweet_id}"


def prepare_clips(conn: sqlite3.Connection, tweet_ids: list[int]) -> list[Clip]:
    """Download + identify each tweet's primary video. Returns Clip[] in input order."""
    clips: list[Clip] = []
    for tid in tweet_ids:
        row = conn.execute(
            "SELECT content, media_json FROM tweets WHERE tweet_id=?", (tid,),
        ).fetchone()
        if not row:
            print(f"  skip {tid}: not in DB")
            continue
        content, media_json = row
        media = json.loads(media_json)
        videos = [m for m in media if m.get("type") == "video"]
        if not videos:
            print(f"  skip {tid}: no video media")
            continue
        paths = download_tweet_media(conn, tid)
        video_paths = [p for p in paths if p.suffix.lower() in (".mp4", ".mov", ".webm")]
        if not video_paths:
            print(f"  skip {tid}: download produced no video")
            continue
        path = video_paths[0]
        dur = probe_duration(path)
        clips.append(Clip(
            tweet_id=tid,
            label=_label_for_tweet(content, tid),
            path=path,
            duration=dur,
        ))
    return clips


def build_card_video(text: str, seconds: float, out_path: Path) -> None:
    """Render a solid-color intro/outro card with centered text."""
    drawtext = (
        f"drawtext=fontfile={FONT_PATH}:"
        f"text='{text}':fontcolor=white:fontsize=72:"
        f"x=(w-text_w)/2:y=(h-text_h)/2"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i", f"color=c=black:s={OUTPUT_W}x{OUTPUT_H}:r=30:d={seconds}",
        "-f", "lavfi", "-i", f"anullsrc=channel_layout=stereo:sample_rate=44100",
        "-shortest",
        "-vf", drawtext,
        "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k",
        "-t", f"{seconds}",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def normalize_clip(src: Path, dst: Path) -> None:
    """Normalize one clip to 1920x1080 with blur-padding for vertical sources."""
    filter_complex = (
        f"[0:v]split=2[bg0][fg0];"
        f"[bg0]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=increase,"
        f"crop={OUTPUT_W}:{OUTPUT_H},gblur=sigma=20[bg];"
        f"[fg0]scale={OUTPUT_W}:{OUTPUT_H}:force_original_aspect_ratio=decrease[fg];"
        f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v]"
    )
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-filter_complex", filter_complex,
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "fast", "-crf", "22", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-ac", "2",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def concat_all(parts: list[Path], out_path: Path) -> None:
    """Concatenate normalized clips via concat demuxer (fast, no re-encode if codecs match)."""
    list_file = out_path.with_suffix(".list.txt")
    list_file.write_text("".join(f"file '{p.resolve()}'\n" for p in parts))
    cmd = [
        "ffmpeg", "-y", "-loglevel", "warning",
        "-f", "concat", "-safe", "0",
        "-i", str(list_file),
        "-c", "copy",
        "-movflags", "+faststart",
        str(out_path),
    ]
    subprocess.run(cmd, check=True)
    list_file.unlink(missing_ok=True)


def build_compilation(
    conn: sqlite3.Connection,
    tweet_ids: list[int],
    theme: str,
) -> tuple[Path, list[tuple[float, str]]]:
    """Compile clips into one landscape video. Returns (output_path, chapters)."""
    if not tweet_ids:
        raise ValueError("no tweet_ids provided")
    COMPILE_ROOT.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    work = COMPILE_ROOT / f"{stamp}_{theme.lower().replace(' ', '_')}"
    work.mkdir(parents=True, exist_ok=True)

    print(f"[1/4] preparing clips for theme '{theme}' (n={len(tweet_ids)})")
    clips = prepare_clips(conn, tweet_ids)
    if not clips:
        raise ValueError("no usable clips after prepare")

    print(f"[2/4] building intro/outro cards")
    intro_path = work / "00_intro.mp4"
    outro_path = work / "99_outro.mp4"
    build_card_video(
        f"FIRE EDITS CLUB presents\n\n{len(clips)} {theme} Edits",
        INTRO_SECONDS, intro_path,
    )
    build_card_video(
        "Subscribe @fireeditsclub\n\nDaily fire edits",
        OUTRO_SECONDS, outro_path,
    )

    print(f"[3/4] normalizing {len(clips)} clips to {OUTPUT_W}x{OUTPUT_H}")
    parts: list[Path] = [intro_path]
    chapters: list[tuple[float, str]] = [(0.0, "Intro")]
    cursor = INTRO_SECONDS
    for i, c in enumerate(clips, 1):
        norm = work / f"{i:02d}_clip.mp4"
        print(f"  [{i:>2}/{len(clips)}] {c.tweet_id}: {c.label[:50]}")
        normalize_clip(c.path, norm)
        parts.append(norm)
        chapters.append((cursor, c.label))
        cursor += c.duration
    parts.append(outro_path)
    chapters.append((cursor, "Subscribe"))

    print(f"[4/4] concatenating into final video")
    out_path = work / "compilation.mp4"
    concat_all(parts, out_path)
    total_secs = probe_duration(out_path)
    print(f"  done: {out_path} ({out_path.stat().st_size:,} bytes, {total_secs:.1f}s)")
    return out_path, chapters


def cluster_for_compilation(conn: sqlite3.Connection, n: int = 10) -> list[int]:
    """Pick N highest-scored unused tweets with video media (no enrichment-based clustering yet)."""
    rows = conn.execute(
        """
        SELECT t.tweet_id
        FROM tweets t
        WHERE t.media_json LIKE '%"type": "video"%'
          AND t.tweet_id NOT IN (
              SELECT tweet_id FROM posts WHERE tweet_id IS NOT NULL
          )
          AND t.tweet_id NOT IN (
              SELECT json_each.value
              FROM compilations, json_each(compilations.tweet_ids_json)
          )
        ORDER BY t.score DESC NULLS LAST
        LIMIT ?
        """,
        (n,),
    ).fetchall()
    return [r[0] for r in rows]


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "--build":
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 10
        conn = sqlite3.connect(DB_PATH)
        tids = cluster_for_compilation(conn, n)
        if not tids:
            sys.exit("no unused tweets available for compilation")
        out, chapters = build_compilation(conn, tids, theme="Fire")
        # Record the compilation in DB
        conn.execute(
            """
            INSERT INTO compilations (theme, tweet_ids_json, landscape_path, created_at)
            VALUES (?, ?, ?, ?)
            """,
            ("Fire", json.dumps(tids), str(out),
             datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        print("\nchapter markers:")
        for sec, label in chapters:
            m, s = divmod(int(sec), 60)
            print(f"  {m:>2d}:{s:02d}  {label}")
    else:
        sys.exit("usage: python compile.py --build [n_clips]")


if __name__ == "__main__":
    main()
