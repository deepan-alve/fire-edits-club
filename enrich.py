"""Analyze edit videos with Gemini, return structured metadata for SEO/captioning.

Auth modes (auto-detected):
    - Vertex AI:    if secrets/vertex-sa.json exists (service account)
    - AI Studio:    if secrets/gemini-key.txt exists (simpler API key)

For Vertex with videos >20MB, we compress to a small low-bitrate preview
before sending — Gemini only needs to SEE the video, not get every pixel.

Returns dict with:
    subjects:           ["Spider-Man", "Doctor Strange"]
    fandoms:            ["Marvel", "MCU"]
    mood:               "epic, hype"
    suggested_hashtags: ["#mcu", "#spiderman", "#marveledit"]
    suggested_mentions: [{"handle": "@marvel", "confidence": 0.95}]
    one_line_summary:   "Doctor Strange and Spider-Man multiverse showdown"

Cost: ~$0.001 per ~30s video on gemini-2.0-flash. Negligible.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from google import genai
from google.genai import types

ROOT = Path(__file__).parent
VERTEX_SA = ROOT / "secrets" / "vertex-sa.json"
AISTUDIO_KEY = ROOT / "secrets" / "gemini-key.txt"
DB_PATH = ROOT / "data" / "seen.sqlite"
MODEL = "gemini-2.5-flash"

# Vertex defaults — override via env or by editing
VERTEX_PROJECT = os.environ.get("GCP_PROJECT", "hokuai")
VERTEX_LOCATION = os.environ.get("GCP_LOCATION", "us-central1")

# Vertex inline-data limit is 20MB; we compress below this.
INLINE_LIMIT_MB = 18  # leave a little headroom

PROMPT = """\
You are analyzing a short video edit (typically 15-60 seconds) for a content-curation account that reposts viral edits.

Look at the video and identify:

1. **subjects**: specific characters, athletes, actors, or recognizable named things shown (e.g., "Spider-Man", "Iron Man", "LeBron James", "Goku", "Bugatti Chiron"). Be specific. 1-5 items.

2. **fandoms**: which fandom/genre groups this falls under. Use lowercase short tokens from this safe list when applicable: marvel, mcu, dc, spider-man, spiderman, star wars, starwars, anime, naruto, dragon ball, demon slayer, attack on titan, nba, lakers, warriors, mma, ufc, f1, formula 1, sigma, cars, gaming, fortnite, minecraft. Otherwise pick a short fandom name. 1-3 items.

3. **mood**: a brief 2-3 word description (e.g., "epic, hype", "melancholy aesthetic", "intense action").

4. **suggested_hashtags**: 5-10 lowercase hashtag candidates without the # symbol, ordered by relevance. Mix specific (e.g., "spidermanedit") with broad (e.g., "marveledit").

5. **suggested_mentions**: list of plausible Instagram/Twitter accounts that would be relevant to mention, each with confidence 0.0-1.0. Only suggest big official accounts (e.g., @marvel, @ufc), never individuals you're guessing about. Use 0.9+ only when extremely sure.

6. **one_line_summary**: a single sentence (max 80 chars) describing what's in the video, suitable for use as a video title.

Respond ONLY with a valid JSON object matching this schema:
{
  "subjects": ["..."],
  "fandoms": ["..."],
  "mood": "...",
  "suggested_hashtags": ["..."],
  "suggested_mentions": [{"handle": "@...", "confidence": 0.0}],
  "one_line_summary": "..."
}
"""


def _client() -> tuple[genai.Client, str]:
    """Return (client, mode) where mode is 'vertex' or 'aistudio'."""
    if VERTEX_SA.exists():
        os.environ.setdefault("GOOGLE_APPLICATION_CREDENTIALS", str(VERTEX_SA))
        return genai.Client(
            vertexai=True,
            project=VERTEX_PROJECT,
            location=VERTEX_LOCATION,
        ), "vertex"
    if AISTUDIO_KEY.exists():
        return genai.Client(api_key=AISTUDIO_KEY.read_text().strip()), "aistudio"
    sys.exit(
        "missing credentials — drop a service account at secrets/vertex-sa.json "
        "or an API key at secrets/gemini-key.txt"
    )


def _compress_for_gemini(src: Path) -> Path:
    """Compress to ~360p / low bitrate so we fit inline. Gemini only needs to SEE it."""
    dst = Path(tempfile.mkstemp(suffix=".mp4", prefix="gemini_")[1])
    cmd = [
        "ffmpeg", "-y", "-loglevel", "error",
        "-i", str(src),
        "-vf", "scale=-2:360,fps=10",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "32",
        "-c:a", "aac", "-b:a", "48k", "-ac", "1",
        "-movflags", "+faststart",
        str(dst),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
    return dst


def analyze_video(video_path: Path) -> dict:
    """Send video to Gemini, return parsed enrichment dict."""
    if not video_path.exists():
        raise FileNotFoundError(video_path)
    client, mode = _client()

    print(f"  mode: {mode}")

    size_mb = video_path.stat().st_size / (1024 * 1024)

    if mode == "vertex":
        # Vertex AI: inline data, may need to compress
        send_path = video_path
        if size_mb > INLINE_LIMIT_MB:
            print(f"  source is {size_mb:.1f}MB > {INLINE_LIMIT_MB}MB, compressing for Gemini...")
            send_path = _compress_for_gemini(video_path)
            print(f"    compressed → {send_path.stat().st_size / (1024*1024):.1f}MB")
        video_part = types.Part.from_bytes(
            data=send_path.read_bytes(),
            mime_type="video/mp4",
        )
        contents = [video_part, PROMPT]
    else:
        # AI Studio: Files API handles big uploads + processing
        print(f"  uploading {video_path.name} ({video_path.stat().st_size:,} bytes) to Files API...")
        file = client.files.upload(file=str(video_path))
        while file.state.name == "PROCESSING":
            time.sleep(2)
            file = client.files.get(name=file.name)
        if file.state.name != "ACTIVE":
            raise RuntimeError(f"file processing failed: {file.state.name}")
        contents = [file, PROMPT]

    print(f"  calling {MODEL}...")
    response = client.models.generate_content(
        model=MODEL,
        contents=contents,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.3,
        ),
    )

    if mode == "aistudio":
        try:
            client.files.delete(name=contents[0].name)
        except Exception:
            pass
    elif mode == "vertex" and size_mb > INLINE_LIMIT_MB:
        send_path.unlink(missing_ok=True)

    try:
        return json.loads(response.text)
    except json.JSONDecodeError as e:
        raise RuntimeError(f"bad JSON from Gemini: {e}\n{response.text[:500]}")


def enrich_tweet(conn: sqlite3.Connection, tweet_id: int, video_path: Path) -> dict:
    """Run enrichment on a downloaded video, save to tweets.enrichment_json."""
    data = analyze_video(video_path)
    conn.execute(
        "UPDATE tweets SET enrichment_json=? WHERE tweet_id=?",
        (json.dumps(data), tweet_id),
    )
    conn.commit()
    return data


def enrich_unenriched_top(conn: sqlite3.Connection, limit: int = 10) -> int:
    """Run enrichment on the top-`limit` highest-scored tweets that aren't enriched yet."""
    from downloader import download_tweet_media
    rows = conn.execute(
        """SELECT tweet_id FROM tweets
        WHERE enrichment_json IS NULL AND media_json LIKE '%"type": "video"%'
        ORDER BY score DESC LIMIT ?""",
        (limit,),
    ).fetchall()
    count = 0
    for (tid,) in rows:
        try:
            paths = download_tweet_media(conn, tid)
            videos = [p for p in paths if p.suffix.lower() in (".mp4", ".mov", ".webm")]
            if not videos:
                continue
            print(f"\nenriching tweet {tid}...")
            data = enrich_tweet(conn, tid, videos[0])
            print(f"  subjects: {data.get('subjects', [])}")
            print(f"  fandoms:  {data.get('fandoms', [])}")
            print(f"  mood:     {data.get('mood')}")
            count += 1
        except Exception as e:
            print(f"  ✗ enrichment failed for {tid}: {e}")
    return count


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    sb = sub.add_parser("batch")
    sb.add_argument("--limit", type=int, default=10)

    sf = sub.add_parser("file")
    sf.add_argument("path")

    args = ap.parse_args()

    if args.cmd == "batch":
        conn = sqlite3.connect(DB_PATH)
        n = enrich_unenriched_top(conn, args.limit)
        print(f"\nenriched {n} tweet(s)")
    elif args.cmd == "file":
        data = analyze_video(Path(args.path))
        print(json.dumps(data, indent=2))


if __name__ == "__main__":
    main()
