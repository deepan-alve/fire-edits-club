"""Download media for a tweet from URLs already in the DB.

The scraper stores video URLs in tweets.media_json. This module pulls those
files into data/media/<tweet_id>/ for downstream transform/compile use.
"""
from __future__ import annotations

import json
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "seen.sqlite"
MEDIA_ROOT = ROOT / "data" / "media"


def download_tweet_media(conn: sqlite3.Connection, tweet_id: int) -> list[Path]:
    """Download all media for a tweet. Returns list of local paths."""
    row = conn.execute("SELECT media_json FROM tweets WHERE tweet_id=?", (tweet_id,)).fetchone()
    if not row:
        raise ValueError(f"tweet {tweet_id} not in DB")
    media = json.loads(row[0])
    if not media:
        return []

    out_dir = MEDIA_ROOT / str(tweet_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for i, m in enumerate(media):
        url = m.get("url")
        if not url:
            continue
        ext = m.get("extension") or m.get("filename", "f").rsplit(".", 1)[-1] or "mp4"
        if "." not in ext or len(ext) > 5:
            ext = "mp4" if m.get("type") == "video" else "jpg"
        dst = out_dir / f"{i:02d}.{ext}"
        if dst.exists() and dst.stat().st_size > 0:
            paths.append(dst)
            continue
        print(f"    downloading {url[:80]}... → {dst.name}")
        subprocess.run(
            ["curl", "-sSL", "-o", str(dst), url],
            check=True, timeout=180,
        )
        paths.append(dst)
    return paths


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python downloader.py <tweet_id>")
    tid = int(sys.argv[1])
    conn = sqlite3.connect(DB_PATH)
    paths = download_tweet_media(conn, tid)
    print(f"downloaded {len(paths)} media file(s) for tweet {tid}:")
    for p in paths:
        print(f"  {p} ({p.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
