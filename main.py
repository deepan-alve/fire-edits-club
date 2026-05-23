"""Fire Edits Club orchestrator.

Subcommands:
    python main.py scrape [N]       — pull latest N tweets from @EditsGoesHard
    python main.py schedule         — fill upcoming slots with content
    python main.py compile [N]      — build a long-form compilation from N tweets
    python main.py publish-due      — publish any scheduled posts that are due now
    python main.py status           — print state of the queue + recent posts
    python main.py run              — full tick: scrape + schedule + publish-due

Cron-driven: schedule `python main.py run` every 5 minutes on alveta.
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import traceback
from datetime import datetime, timezone
from pathlib import Path

import scheduler
import scraper
import compose
import compile as compilemod
from downloader import download_tweet_media
from instagram import upload_reel
from transformer import transform
from youtube import upload as upload_yt

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "seen.sqlite"
WORK_DIR = ROOT / "data" / "work"

MIGRATIONS = """
CREATE TABLE IF NOT EXISTS migrations (
    name TEXT PRIMARY KEY, applied_at TEXT NOT NULL
);
"""


def _apply_migration(conn: sqlite3.Connection, name: str, sql: str) -> None:
    row = conn.execute("SELECT 1 FROM migrations WHERE name=?", (name,)).fetchone()
    if row:
        return
    conn.executescript(sql)
    conn.execute(
        "INSERT INTO migrations (name, applied_at) VALUES (?, ?)",
        (name, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(MIGRATIONS)
    # add enrichment + music columns to tweets table if missing
    _apply_migration(conn, "001_tweet_enrichment",
                     "ALTER TABLE tweets ADD COLUMN enrichment_json TEXT;")
    _apply_migration(conn, "002_tweet_music",
                     "ALTER TABLE tweets ADD COLUMN music_json TEXT;")
    _apply_migration(conn, "003_tweet_status",
                     "ALTER TABLE tweets ADD COLUMN status TEXT DEFAULT 'new';")


def _conn() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.executescript(scraper.SCHEMA)
    scheduler.init_schema(conn)
    migrate(conn)
    return conn


# ─── subcommands ──────────────────────────────────────────────────────────────


def cmd_scrape(args: argparse.Namespace) -> None:
    n = args.n or 30
    conn = _conn()
    tweets = scraper.fetch_tweets(1, n)
    new, existing = scraper.save_tweets(conn, tweets)
    print(f"scrape: {new} new, {existing} existing (window 1-{n})")


def cmd_schedule(args: argparse.Namespace) -> None:
    conn = _conn()
    added = scheduler.fill_upcoming(conn, hours=args.hours or 48)
    print(f"schedule: {added} new slots filled")


def cmd_compile(args: argparse.Namespace) -> None:
    n = args.n or 10
    conn = _conn()
    tids = compilemod.cluster_for_compilation(conn, n)
    if not tids:
        sys.exit("no unused tweets available for compilation")
    out, chapters = compilemod.build_compilation(conn, tids, theme=args.theme or "Fire")
    conn.execute(
        "INSERT INTO compilations (theme, tweet_ids_json, landscape_path, created_at) "
        "VALUES (?, ?, ?, ?)",
        (args.theme or "Fire", json.dumps(tids), str(out),
         datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()


def _enrichment_for(conn: sqlite3.Connection, tweet_id: int) -> compose.Enrichment | None:
    row = conn.execute(
        "SELECT enrichment_json FROM tweets WHERE tweet_id=?", (tweet_id,),
    ).fetchone()
    if not row or not row[0]:
        return None
    data = json.loads(row[0])
    return compose.Enrichment(
        subjects=data.get("subjects", []),
        fandoms=data.get("fandoms", []),
        mood=data.get("mood"),
        suggested_hashtags=data.get("suggested_hashtags", []),
        suggested_mentions=[
            (m["handle"], m.get("confidence", 0.0))
            for m in data.get("suggested_mentions", [])
        ],
    )


def _music_for(conn: sqlite3.Connection, tweet_id: int) -> compose.MusicMatch | None:
    row = conn.execute(
        "SELECT music_json FROM tweets WHERE tweet_id=?", (tweet_id,),
    ).fetchone()
    if not row or not row[0]:
        return None
    data = json.loads(row[0])
    if not data.get("title") or not data.get("artist"):
        return None
    return compose.MusicMatch(title=data["title"], artist=data["artist"])


def _publish_short_reel(conn: sqlite3.Connection, sched_id: int, tweet_id: int) -> None:
    row = conn.execute(
        "SELECT content, enrichment_json, music_json FROM tweets WHERE tweet_id=?",
        (tweet_id,),
    ).fetchone()
    tweet_text = row[0] if row else ""
    has_enrichment = bool(row and row[1])
    has_music = bool(row and row[2])

    print(f"  publishing tweet {tweet_id}")
    WORK_DIR.mkdir(parents=True, exist_ok=True)

    print(f"  → downloading media")
    paths = download_tweet_media(conn, tweet_id)
    if not paths:
        raise RuntimeError("no media downloaded")
    src = paths[0]

    # Self-updating: enrich + music ID on the fly if missing
    if not has_enrichment:
        try:
            print(f"  → enriching (Vertex Gemini)")
            import enrich
            enrich.enrich_tweet(conn, tweet_id, src)
        except Exception as e:
            print(f"    ⚠ enrichment skipped: {e}")
    if not has_music:
        try:
            print(f"  → music ID (AcoustID)")
            import music_id
            match = music_id.identify(src)
            data = (
                {"title": match.title, "artist": match.artist, "score": match.score}
                if match else {"title": None, "artist": None}
            )
            conn.execute(
                "UPDATE tweets SET music_json=? WHERE tweet_id=?",
                (json.dumps(data), tweet_id),
            )
            conn.commit()
            if match:
                print(f"    found: {match.title} — {match.artist}")
            else:
                print(f"    no match (~expected on edits)")
        except Exception as e:
            print(f"    ⚠ music id skipped: {e}")

    enrichment = _enrichment_for(conn, tweet_id)
    music = _music_for(conn, tweet_id)

    transformed = WORK_DIR / f"{tweet_id}_short.mp4"
    print(f"  → transforming to 9:16")
    transform(src, transformed)

    yt_title = compose.compose_short_title(tweet_text, enrichment)
    yt_desc = compose.compose_short_description(tweet_text, enrichment, music)
    yt_tags = compose.compose_short_tags(enrichment)
    ig_caption = compose.compose_reel(tweet_text, enrichment, music)

    print(f"  → uploading to YouTube")
    yt_id = upload_yt(transformed, yt_title, yt_desc, yt_tags, privacy="public")
    conn.execute(
        "INSERT OR IGNORE INTO posts (tweet_id, platform, media_id, posted_at) "
        "VALUES (?, 'yt_short', ?, ?)",
        (tweet_id, yt_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()

    print(f"  → uploading to Instagram")
    try:
        ig_id = upload_reel(transformed, ig_caption)
        conn.execute(
            "INSERT OR IGNORE INTO posts (tweet_id, platform, media_id, posted_at) "
            "VALUES (?, 'ig_reel', ?, ?)",
            (tweet_id, ig_id, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
    except SystemExit as e:
        ig_id = None
        print(f"  ⚠ IG upload failed: {e}")

    scheduler.mark_posted(conn, sched_id, yt_id=yt_id, ig_id=ig_id)


def _publish_yt_long(conn: sqlite3.Connection, sched_id: int, compilation_id: int) -> None:
    row = conn.execute(
        "SELECT theme, tweet_ids_json, landscape_path FROM compilations WHERE compilation_id=?",
        (compilation_id,),
    ).fetchone()
    if not row:
        raise RuntimeError(f"compilation {compilation_id} not found")
    theme, _tids_json, path = row
    if not path or not Path(path).exists():
        raise RuntimeError(f"compilation file missing: {path}")

    tids = json.loads(_tids_json)
    chapters = []
    cursor = compilemod.INTRO_SECONDS
    chapters.append((0.0, "Intro"))
    for tid in tids:
        r = conn.execute("SELECT content FROM tweets WHERE tweet_id=?", (tid,)).fetchone()
        label = compilemod._label_for_tweet(r[0] if r else None, tid)
        chapters.append((cursor, label))
        # We can't know each clip's duration without re-probing
        cursor += 30  # rough — actual chapter recalc happens at build time

    title = compose.compose_compilation_title(theme, len(tids))
    desc = compose.compose_compilation_description(theme, chapters)
    tags = [theme.lower(), f"{theme.lower()} edits", "edit compilation",
            "fire edits", "best edits", "viral edits", "fireeditsclub"]

    print(f"  → uploading compilation to YouTube long-form")
    yt_id = upload_yt(Path(path), title, desc, tags, privacy="public")
    conn.execute(
        "UPDATE compilations SET used_at=? WHERE compilation_id=?",
        (datetime.now(timezone.utc).isoformat(), compilation_id),
    )
    for tid in tids:
        conn.execute(
            "INSERT OR IGNORE INTO posts (tweet_id, platform, media_id, posted_at) "
            "VALUES (?, 'yt_long', ?, ?)",
            (tid, yt_id, datetime.now(timezone.utc).isoformat()),
        )
    conn.commit()
    scheduler.mark_posted(conn, sched_id, yt_id=yt_id)


def cmd_publish_due(args: argparse.Namespace) -> None:
    conn = _conn()
    due = scheduler.due_now(conn)
    print(f"publish-due: {len(due)} post(s) due")
    for sched_id, stream, tweet_id, compilation_id, sched_for in due:
        print(f"\n[#{sched_id}] stream={stream} due={sched_for}")
        try:
            if stream == "short_reel":
                _publish_short_reel(conn, sched_id, tweet_id)
            elif stream == "yt_long":
                _publish_yt_long(conn, sched_id, compilation_id)
            else:
                raise RuntimeError(f"unknown stream: {stream}")
        except Exception as e:
            err = "".join(traceback.format_exception_only(type(e), e)).strip()
            print(f"  ✗ failed: {err}")
            scheduler.mark_failed(conn, sched_id, err)


def cmd_status(args: argparse.Namespace) -> None:
    conn = _conn()
    tweets = conn.execute("SELECT COUNT(*) FROM tweets").fetchone()[0]
    posted = conn.execute("SELECT COUNT(DISTINCT tweet_id) FROM posts").fetchone()[0]
    queued = conn.execute(
        "SELECT COUNT(*) FROM scheduled_posts WHERE status='queued'",
    ).fetchone()[0]
    failed = conn.execute(
        "SELECT COUNT(*) FROM scheduled_posts WHERE status='failed'",
    ).fetchone()[0]
    comps_total = conn.execute("SELECT COUNT(*) FROM compilations").fetchone()[0]
    comps_unused = conn.execute(
        "SELECT COUNT(*) FROM compilations WHERE used_at IS NULL",
    ).fetchone()[0]
    print(f"tweets:       {tweets}")
    print(f"posted:       {posted}")
    print(f"queued:       {queued}")
    print(f"failed:       {failed}")
    print(f"compilations: {comps_total} ({comps_unused} unused)")
    print()
    print("=== next 5 queued ===")
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
    rows = conn.execute(
        """SELECT id, stream, tweet_id, compilation_id, scheduled_for
        FROM scheduled_posts WHERE status='queued'
        ORDER BY scheduled_for ASC LIMIT 5""",
    ).fetchall()
    for sid, stream, tid, cid, sched in rows:
        when_ist = datetime.fromisoformat(sched).astimezone(IST).strftime("%a %d %b %H:%M IST")
        ref = f"tweet={tid}" if tid else f"comp={cid}"
        print(f"  #{sid}  {when_ist}  {stream:>11s}  {ref}")


def cmd_run(args: argparse.Namespace) -> None:
    print("=== TICK ===", datetime.now(timezone.utc).isoformat())
    cmd_scrape(argparse.Namespace(n=30))
    cmd_schedule(argparse.Namespace(hours=48))
    cmd_publish_due(args)


def main() -> None:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("scrape"); s.add_argument("n", nargs="?", type=int, default=30); s.set_defaults(func=cmd_scrape)
    s = sub.add_parser("schedule"); s.add_argument("--hours", type=int, default=48); s.set_defaults(func=cmd_schedule)
    s = sub.add_parser("compile"); s.add_argument("n", nargs="?", type=int, default=10); s.add_argument("--theme", default="Fire"); s.set_defaults(func=cmd_compile)
    s = sub.add_parser("publish-due"); s.set_defaults(func=cmd_publish_due)
    s = sub.add_parser("status"); s.set_defaults(func=cmd_status)
    s = sub.add_parser("run"); s.set_defaults(func=cmd_run)

    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
