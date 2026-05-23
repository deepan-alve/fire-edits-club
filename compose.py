"""Generate platform-specific captions, titles, descriptions, and tags.

Three content streams:
    short_reel   — IG Reel + YT Short (same copy, cross-posted)
    yt_long      — YT long-form compilation

Inputs:
    tweet metadata (text, score, etc.)
    enrichment from enrich.py (subjects, fandoms, suggested hashtags/mentions)
    music match from music_id.py (song + artist, or None)
    compilation context (only for yt_long)
"""
from __future__ import annotations

import re
from dataclasses import dataclass


SAFE_FANDOM_MENTIONS = {
    "marvel": "@marvel",
    "mcu": "@marvel",
    "spider-man": "@spidermanmovie",
    "spiderman": "@spidermanmovie",
    "star wars": "@starwars",
    "starwars": "@starwars",
    "nba": "@nba",
    "lakers": "@lakers",
    "warriors": "@warriors",
    "mma": "@ufc",
    "ufc": "@ufc",
    "anime": "@crunchyroll",
    "naruto": "@narutoofficial",
    "dragon ball": "@dragonballofficial",
    "f1": "@f1",
    "formula 1": "@f1",
}

BASE_HASHTAGS_REEL = ["#edits", "#fireedits", "#viraledits", "#editsthatgohard", "#reels"]
BASE_HASHTAGS_SHORT = ["#Shorts", "#edits", "#fireedits", "#viraledits", "#editsthatgohard"]
BASE_TAGS_YT = [
    "edits", "fire edits", "viral edits", "edits that go hard",
    "fireeditsclub", "edit compilation",
]


@dataclass
class Enrichment:
    subjects: list[str]
    fandoms: list[str]
    mood: str | None
    suggested_hashtags: list[str]
    suggested_mentions: list[tuple[str, float]]  # (handle, confidence 0-1)


@dataclass
class MusicMatch:
    title: str
    artist: str


def _clean_text(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _safe_mentions(enrichment: Enrichment | None, confidence_floor: float = 0.85) -> list[str]:
    """Return only @EditsGoesHard + fandom mentions we're highly confident in + whitelisted."""
    mentions = ["@editsgoeshard"]
    if not enrichment:
        return mentions
    for fandom in enrichment.fandoms:
        m = SAFE_FANDOM_MENTIONS.get(fandom.lower())
        if m and m not in mentions:
            mentions.append(m)
    for handle, conf in enrichment.suggested_mentions:
        if conf >= confidence_floor and handle in SAFE_FANDOM_MENTIONS.values():
            if handle not in mentions:
                mentions.append(handle)
    return mentions[:5]


def _hashtags(enrichment: Enrichment | None, base: list[str], cap: int = 12) -> list[str]:
    tags = list(base)
    if enrichment:
        for h in enrichment.suggested_hashtags:
            tag = h if h.startswith("#") else f"#{h}"
            tag = re.sub(r"[^A-Za-z0-9#]", "", tag)
            if tag not in tags and len(tag) > 1:
                tags.append(tag)
    return tags[:cap]


def compose_reel(
    tweet_text: str,
    enrichment: Enrichment | None = None,
    music: MusicMatch | None = None,
) -> str:
    """Single-video Instagram Reel caption (also reused for YT Short description)."""
    hook = _clean_text(tweet_text) or "this one goes hard 🔥"
    mentions = " ".join(_safe_mentions(enrichment))
    tags = " ".join(_hashtags(enrichment, BASE_HASHTAGS_REEL))
    song_line = f"🎵 {music.title} — {music.artist}\n" if music else ""
    return (
        f"{hook}\n\n"
        f"{song_line}"
        f"🎬 Originally by @editsgoeshard on X\n\n"
        f"{mentions}\n\n"
        f"{tags}"
    )


def compose_short_title(tweet_text: str, enrichment: Enrichment | None = None) -> str:
    """YouTube Short title — max 100 chars. Front-load keywords."""
    base = _clean_text(tweet_text)
    if not base:
        if enrichment and enrichment.subjects:
            base = f"{enrichment.subjects[0]} edit goes hard"
        else:
            base = "this edit goes hard"
    title = f"{base} 🔥 #Shorts"
    return title[:100]


def compose_short_description(
    tweet_text: str,
    enrichment: Enrichment | None = None,
    music: MusicMatch | None = None,
) -> str:
    """YouTube Short description — up to 5000 chars but keep it tight, mobile-truncated."""
    hook = _clean_text(tweet_text) or "this one goes hard 🔥"
    mentions = " ".join(_safe_mentions(enrichment))
    tags = " ".join(_hashtags(enrichment, BASE_HASHTAGS_SHORT))
    song_line = f"🎵 {music.title} — {music.artist}\n" if music else ""
    return (
        f"{hook}\n\n"
        f"{song_line}"
        f"🎬 Originally by @EditsGoesHard on X\n"
        f"Follow for daily fire edits 🔥\n\n"
        f"IG: @fireeditsclub\n\n"
        f"{mentions}\n\n"
        f"{tags}"
    )


def compose_short_tags(enrichment: Enrichment | None = None) -> list[str]:
    """YouTube Short tags — max 25 tags, total length capped at 500 chars."""
    tags = list(BASE_TAGS_YT)
    if enrichment:
        for s in enrichment.subjects[:5]:
            t = s.lower()
            if t not in tags:
                tags.append(t)
        for f in enrichment.fandoms[:3]:
            t = f"{f.lower()} edits"
            if t not in tags:
                tags.append(t)
    # Truncate to ~500 chars total
    result: list[str] = []
    total = 0
    for t in tags:
        if total + len(t) + 1 > 500:
            break
        result.append(t)
        total += len(t) + 1
    return result[:25]


def compose_compilation_title(theme: str, count: int) -> str:
    """YouTube long-form compilation title — max 100, SEO-loaded."""
    title = f"{count} {theme} Edits That Go HARD 🔥 | Best Edit Compilation"
    return title[:100]


def compose_compilation_description(
    theme: str,
    chapter_markers: list[tuple[float, str]],
) -> str:
    """YouTube long-form description with chapter markers."""
    lines = [
        f"The hardest {theme.lower()} edits, all in one place 🔥",
        "",
        "🎬 All edits originally posted by @EditsGoesHard on X",
        "Curated and compiled by Fire Edits Club",
        "",
        "📲 Follow for daily fire edits:",
        "Instagram: @fireeditsclub",
        "",
        "⏱ Chapters:",
    ]
    for seconds, label in chapter_markers:
        m, s = divmod(int(seconds), 60)
        lines.append(f"{m:d}:{s:02d}  {label}")
    lines += [
        "",
        f"#{theme.lower().replace(' ', '')}edits #edits #fireedits #editcompilation #editsthatgohard",
    ]
    return "\n".join(lines)
