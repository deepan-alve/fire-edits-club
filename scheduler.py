"""Assign scheduled posting slots in IST with jitter.

Three slots per day:
    09:00 IST → short_reel #1  (YT Short + IG Reel, same video)
    15:00 IST → yt_long        (long-form compilation)
    20:00 IST → short_reel #2  (YT Short + IG Reel, same video)

Each slot gets ±30 minutes of random jitter at scheduling time so we don't
hit Meta/Google "automated content" classifiers. Stored as UTC in DB.
"""
from __future__ import annotations

import json
import random
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent
DB_PATH = ROOT / "data" / "seen.sqlite"
IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc

# Growth schedule (5 slots/day, first 30 days then scale back):
#   08:30 IST  short_reel  (IG Reel + YT Short cross-post — morning commute)
#   13:00 IST  ig_only     (IG Reel only — lunch, conserve YT quota)
#   15:30 IST  yt_long     (long-form compilation)
#   19:30 IST  short_reel  (IG Reel + YT Short cross-post — prime time)
#   22:30 IST  ig_only     (IG Reel only — late night scroll)
SLOTS_IST = [
    (8, 30),
    (13, 0),
    (15, 30),
    (19, 30),
    (22, 30),
]
SLOT_STREAMS = ["short_reel", "ig_only", "yt_long", "short_reel", "ig_only"]
JITTER_MINUTES = 30


SCHEMA_ADDITIONS = """
CREATE TABLE IF NOT EXISTS scheduled_posts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    stream         TEXT NOT NULL,
    tweet_id       INTEGER,
    compilation_id INTEGER,
    scheduled_for  TEXT NOT NULL,
    posted_yt_id   TEXT,
    posted_ig_id   TEXT,
    status         TEXT NOT NULL DEFAULT 'queued',
    error          TEXT,
    created_at     TEXT NOT NULL,
    posted_at      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sched_status ON scheduled_posts(status, scheduled_for);
CREATE INDEX IF NOT EXISTS idx_sched_stream ON scheduled_posts(stream, status);

CREATE TABLE IF NOT EXISTS compilations (
    compilation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    theme          TEXT NOT NULL,
    tweet_ids_json TEXT NOT NULL,
    landscape_path TEXT,
    created_at     TEXT NOT NULL,
    used_at        TEXT
);
"""


@dataclass
class Slot:
    when_utc: datetime
    stream: str


def init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA_ADDITIONS)
    conn.commit()


def jittered_slot(base_ist: datetime) -> datetime:
    """Apply ±JITTER_MINUTES randomization to an IST datetime, return UTC."""
    offset = random.randint(-JITTER_MINUTES, JITTER_MINUTES)
    return (base_ist + timedelta(minutes=offset)).astimezone(UTC)


def slots_in_window(now_utc: datetime, hours: int = 48) -> list[Slot]:
    """Yield all upcoming slot datetimes (UTC) over the next `hours` hours."""
    now_ist = now_utc.astimezone(IST)
    out: list[Slot] = []
    end = now_utc + timedelta(hours=hours)
    day_offset = 0
    while True:
        target_date = (now_ist + timedelta(days=day_offset)).date()
        for (h, m), stream in zip(SLOTS_IST, SLOT_STREAMS):
            base_ist = datetime.combine(target_date, datetime.min.time()).replace(
                hour=h, minute=m, tzinfo=IST,
            )
            when = jittered_slot(base_ist)
            if when < now_utc:
                continue
            if when > end:
                return out
            out.append(Slot(when, stream))
        day_offset += 1
        if day_offset > hours // 24 + 2:
            return out


def already_filled_slots(conn: sqlite3.Connection, window_end_utc: datetime) -> set[datetime]:
    """Return set of approximate scheduled times that are already taken (rounded to nearest hour)."""
    rows = conn.execute(
        "SELECT scheduled_for FROM scheduled_posts WHERE status='queued' AND scheduled_for < ?",
        (window_end_utc.isoformat(),),
    ).fetchall()
    return {datetime.fromisoformat(r[0]).replace(minute=0, second=0, microsecond=0) for r in rows}


def pick_next_short_reel(conn: sqlite3.Connection) -> int | None:
    """Pick the highest-scored tweet with media that's never been scheduled or published."""
    row = conn.execute(
        """
        SELECT t.tweet_id
        FROM tweets t
        WHERE t.media_json != '[]'
          AND t.tweet_id NOT IN (
              SELECT tweet_id FROM scheduled_posts WHERE tweet_id IS NOT NULL
          )
          AND t.tweet_id NOT IN (
              SELECT tweet_id FROM posts WHERE tweet_id IS NOT NULL
          )
        ORDER BY t.score DESC NULLS LAST
        LIMIT 1
        """,
    ).fetchone()
    return row[0] if row else None


def pick_next_compilation(conn: sqlite3.Connection) -> int | None:
    """Pick an unused compilation that's ready for posting."""
    row = conn.execute(
        """
        SELECT compilation_id
        FROM compilations
        WHERE landscape_path IS NOT NULL AND used_at IS NULL
        ORDER BY created_at ASC
        LIMIT 1
        """,
    ).fetchone()
    return row[0] if row else None


def fill_upcoming(conn: sqlite3.Connection, hours: int = 48) -> int:
    """Schedule posts to fill any empty slots in the next `hours` hours. Returns count added."""
    now = datetime.now(UTC)
    slots = slots_in_window(now, hours)
    filled = already_filled_slots(conn, now + timedelta(hours=hours))

    added = 0
    for slot in slots:
        slot_hour = slot.when_utc.replace(minute=0, second=0, microsecond=0)
        if slot_hour in filled:
            continue

        if slot.stream in ("short_reel", "ig_only"):
            tid = pick_next_short_reel(conn)
            if not tid:
                continue
            conn.execute(
                """
                INSERT INTO scheduled_posts (stream, tweet_id, scheduled_for, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (slot.stream, tid, slot.when_utc.isoformat(), now.isoformat()),
            )
            added += 1
            filled.add(slot_hour)
        elif slot.stream == "yt_long":
            cid = pick_next_compilation(conn)
            if not cid:
                continue
            conn.execute(
                """
                INSERT INTO scheduled_posts (stream, compilation_id, scheduled_for, created_at)
                VALUES (?, ?, ?, ?)
                """,
                ("yt_long", cid, slot.when_utc.isoformat(), now.isoformat()),
            )
            added += 1
            filled.add(slot_hour)
    conn.commit()
    return added


def due_now(conn: sqlite3.Connection, grace_minutes: int = 5) -> list[tuple]:
    """Return scheduled_posts that should fire now (within grace window)."""
    now = datetime.now(UTC)
    cutoff = (now + timedelta(minutes=grace_minutes)).isoformat()
    return conn.execute(
        """
        SELECT id, stream, tweet_id, compilation_id, scheduled_for
        FROM scheduled_posts
        WHERE status='queued' AND scheduled_for <= ?
        ORDER BY scheduled_for ASC
        """,
        (cutoff,),
    ).fetchall()


def mark_posted(
    conn: sqlite3.Connection, sched_id: int,
    yt_id: str | None = None, ig_id: str | None = None,
) -> None:
    conn.execute(
        """
        UPDATE scheduled_posts
        SET status='posted', posted_yt_id=COALESCE(?, posted_yt_id),
            posted_ig_id=COALESCE(?, posted_ig_id), posted_at=?
        WHERE id=?
        """,
        (yt_id, ig_id, datetime.now(UTC).isoformat(), sched_id),
    )
    conn.commit()


def mark_failed(conn: sqlite3.Connection, sched_id: int, err: str) -> None:
    conn.execute(
        "UPDATE scheduled_posts SET status='failed', error=? WHERE id=?",
        (err[:1000], sched_id),
    )
    conn.commit()


def main() -> None:
    """CLI: print upcoming slots + fill empty ones."""
    conn = sqlite3.connect(DB_PATH)
    init_schema(conn)
    added = fill_upcoming(conn, hours=48)
    print(f"scheduled {added} new posts for the next 48h")

    print("\n=== upcoming queue ===")
    rows = conn.execute(
        """
        SELECT id, stream, tweet_id, compilation_id, scheduled_for, status
        FROM scheduled_posts
        WHERE status='queued'
        ORDER BY scheduled_for ASC
        LIMIT 10
        """,
    ).fetchall()
    for sid, stream, tid, cid, sched, status in rows:
        when_ist = datetime.fromisoformat(sched).astimezone(IST).strftime("%a %H:%M IST")
        ref = f"tweet={tid}" if tid else f"comp={cid}"
        print(f"  #{sid}  {when_ist}  {stream:>11s}  {ref}")


if __name__ == "__main__":
    main()
