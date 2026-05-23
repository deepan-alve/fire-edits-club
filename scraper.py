"""Scrape @EditsGoesHard posts via gallery-dl, dedup against SQLite.

Usage:
    python scraper.py [start_index] [end_index]

Default: fetches posts 1-100 from the timeline. Pages of ~20 per request.
Use larger ranges for archive backfill; smaller for live polling.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import shutil

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "seen.sqlite"


def _find_binary(name: str, fallbacks: list[Path]) -> Path:
    found = shutil.which(name)
    if found:
        return Path(found)
    for f in fallbacks:
        if f.exists():
            return f
    return fallbacks[0]


GALLERY_DL = _find_binary("gallery-dl", [
    Path("/usr/local/bin/gallery-dl"),
    Path.home() / ".local" / "bin" / "gallery-dl",
])
SOURCE_USER = "EditsGoesHard"

SCHEMA = """
CREATE TABLE IF NOT EXISTS tweets (
    tweet_id        INTEGER PRIMARY KEY,
    date            TEXT NOT NULL,
    content         TEXT,
    view_count      INTEGER,
    favorite_count  INTEGER,
    retweet_count   INTEGER,
    quote_count     INTEGER,
    reply_count     INTEGER,
    bookmark_count  INTEGER,
    media_json      TEXT NOT NULL,
    first_seen_at   TEXT NOT NULL,
    score           REAL
);
CREATE TABLE IF NOT EXISTS posts (
    tweet_id   INTEGER NOT NULL,
    platform   TEXT NOT NULL,
    media_id   TEXT,
    posted_at  TEXT NOT NULL,
    PRIMARY KEY (tweet_id, platform),
    FOREIGN KEY (tweet_id) REFERENCES tweets(tweet_id)
);
CREATE INDEX IF NOT EXISTS idx_tweets_score ON tweets(score DESC);
CREATE INDEX IF NOT EXISTS idx_tweets_date  ON tweets(date DESC);
"""


def init_db() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(SCHEMA)
    return conn


def fetch_tweets(start: int, end: int) -> list[dict]:
    if not GALLERY_DL.exists():
        sys.exit(f"gallery-dl not found at {GALLERY_DL}")
    cookies_file = Path("secrets/x-cookies.txt")
    if cookies_file.exists():
        cookies_args = ["--cookies", str(cookies_file)]
    else:
        # dev fallback: use the local browser session
        cookies_args = ["--cookies-from-browser", "brave"]
    cmd = [
        str(GALLERY_DL),
        *cookies_args,
        "-j",
        "--range", f"{start}-{end}",
        f"https://x.com/{SOURCE_USER}/timeline",
    ]
    print(f"  running: gallery-dl --range {start}-{end} ...")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        sys.exit(f"gallery-dl failed (exit {result.returncode}):\n{result.stderr[:1000]}")
    raw = json.loads(result.stdout) if result.stdout.strip() else []

    tweets: dict[int, dict] = {}
    for entry in raw:
        if not isinstance(entry, list) or len(entry) < 2:
            continue
        type_id = entry[0]
        if type_id == 2:
            meta = entry[1]
            tid = meta.get("tweet_id")
            if not tid:
                continue
            tweets[tid] = {
                "tweet_id": tid,
                "date": meta.get("date"),
                "content": meta.get("content"),
                "view_count": meta.get("view_count"),
                "favorite_count": meta.get("favorite_count"),
                "retweet_count": meta.get("retweet_count"),
                "quote_count": meta.get("quote_count"),
                "reply_count": meta.get("reply_count"),
                "bookmark_count": meta.get("bookmark_count"),
                "media": [],
            }
        elif type_id == 3:
            url = entry[1]
            meta = entry[2]
            tid = meta.get("tweet_id")
            if tid in tweets:
                tweets[tid]["media"].append({
                    "type": meta.get("type", "image"),
                    "url": url,
                    "width": meta.get("width"),
                    "height": meta.get("height"),
                    "duration": meta.get("duration"),
                    "filename": meta.get("filename"),
                })
    return list(tweets.values())


def compute_score(t: dict) -> float:
    v = t.get("view_count") or 0
    f = t.get("favorite_count") or 0
    r = t.get("retweet_count") or 0
    return v + f * 5 + r * 10


def save_tweets(conn: sqlite3.Connection, tweets: list[dict]) -> tuple[int, int]:
    now = datetime.now(timezone.utc).isoformat()
    new = existing = 0
    for t in tweets:
        cur = conn.execute(
            """
            INSERT OR IGNORE INTO tweets (
                tweet_id, date, content,
                view_count, favorite_count, retweet_count,
                quote_count, reply_count, bookmark_count,
                media_json, first_seen_at, score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                t["tweet_id"], t["date"], t["content"],
                t["view_count"], t["favorite_count"], t["retweet_count"],
                t["quote_count"], t["reply_count"], t["bookmark_count"],
                json.dumps(t["media"]), now, compute_score(t),
            ),
        )
        if cur.rowcount:
            new += 1
        else:
            existing += 1
    conn.commit()
    return new, existing


def report(conn: sqlite3.Connection) -> None:
    total = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    with_media = conn.execute(
        "SELECT COUNT(*) FROM tweets WHERE media_json != '[]'"
    ).fetchone()[0]
    print(f"\n  db state: {total} tweets total, {with_media} with media")
    top = conn.execute(
        "SELECT tweet_id, score, content FROM tweets ORDER BY score DESC LIMIT 5"
    ).fetchall()
    if top:
        print("  top 5 by score:")
        for tid, score, content in top:
            content_clip = (content or "")[:60].replace("\n", " ")
            print(f"    {int(score):>10d}  {tid}  {content_clip}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("start", nargs="?", type=int, default=1)
    ap.add_argument("end", nargs="?", type=int, default=100)
    args = ap.parse_args()

    print(f"scraping @{SOURCE_USER} posts {args.start}-{args.end}")
    conn = init_db()
    tweets = fetch_tweets(args.start, args.end)
    print(f"  gallery-dl returned {len(tweets)} tweets")
    new, existing = save_tweets(conn, tweets)
    print(f"  saved: {new} new, {existing} existing")
    report(conn)


if __name__ == "__main__":
    main()
