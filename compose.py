"""Generate platform-specific captions, titles, descriptions, and tags.

Content streams:
    short_reel   — IG Reel + YT Short (same copy, cross-posted)
    ig_only      — IG Reel only (off-peak slots)
    yt_long      — YT long-form compilation

Inputs:
    tweet metadata (text, score, etc.)
    enrichment from enrich.py (subjects, fandoms, suggested hashtags/mentions)
    music match from music_id.py (song + artist, or None)
    compilation context (only for yt_long)

Caption strategy (growth-tuned):
    - Hook line uses enrichment subjects/fandoms when available
    - CTAs drive saves + comments (the strongest IG algo signals)
    - Hashtags split: ~8 in caption (fandom-specific), ~10 in first comment via IG API
"""
from __future__ import annotations

import random
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

BASE_HASHTAGS_REEL = ["#edits", "#fireedits", "#editsthatgohard"]
BASE_HASHTAGS_SHORT = ["#Shorts", "#edits", "#fireedits", "#editsthatgohard"]
# Dumped into first comment after IG publish (extends reach without cluttering caption)
EXTRA_FIRST_COMMENT_HASHTAGS = [
    "#viraledits", "#reelsindia", "#explorepage", "#editsoftheday",
    "#aestheticedits", "#hardedits", "#trendingreels", "#fyp",
    "#explore", "#reelitfeelit",
]
BASE_TAGS_YT = [
    "edits", "fire edits", "viral edits", "edits that go hard",
    "fireeditsclub", "edit compilation",
]

# Hook templates — random selection per post for variety
HOOK_TEMPLATES_WITH_SUBJECT = [
    "{subject} hits different 🔥",
    "this {subject} edit goes insane",
    "{subject} edit alert 🔥",
    "POV: {subject} edits done right",
    "the {subject} edit you needed today 🔥",
    "wait for the {subject} drop 👀",
]
HOOK_TEMPLATES_FANDOM_ONLY = [
    "{fandom} edits hit different 🔥",
    "this {fandom} edit is unreal",
    "POV: {fandom} fans on a Tuesday",
    "{fandom} edit you didn't ask for but needed 🔥",
]
HOOK_TEMPLATES_GENERIC = [
    "this one hits different 🔥",
    "ok this goes hard 🔥",
    "wait for it... 🔥",
    "no thoughts just vibes 🔥",
    "this edit is too clean 🔥",
]

# CTAs — pick 1-2 per post, rotate for variety
CTA_POOL = [
    "💾 save this for later",
    "🏷 tag someone who needs to see this",
    "💬 which one was your favorite?",
    "🔥 follow @fireeditsclub for daily fire",
    "👀 who else got chills?",
    "🔄 share with the group chat",
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
    """Caption hashtags: fandom-specific first, base tags last. Keep tight (~8)."""
    tags: list[str] = []
    if enrichment:
        # Fandom-specific tags FIRST (these are the discoverability winners)
        for h in enrichment.suggested_hashtags:
            tag = h if h.startswith("#") else f"#{h}"
            tag = re.sub(r"[^A-Za-z0-9#_]", "", tag)
            if tag not in tags and len(tag) > 1:
                tags.append(tag)
    # Brand/generic tags fill the rest
    for t in base:
        if t not in tags:
            tags.append(t)
    return tags[:cap]


def _hook(tweet_text: str, enrichment: Enrichment | None) -> str:
    """Pick a hook line. Use enrichment when available, else tweet text, else generic."""
    cleaned = _clean_text(tweet_text)
    # If tweet has decent original text and not just emoji-only, prefer it
    if cleaned and len(cleaned) >= 8 and not re.fullmatch(r"[\W_]+", cleaned):
        return cleaned

    if enrichment and enrichment.subjects:
        subject = enrichment.subjects[0]
        return random.choice(HOOK_TEMPLATES_WITH_SUBJECT).format(subject=subject)

    if enrichment and enrichment.fandoms:
        fandom = enrichment.fandoms[0]
        # capitalize fandom for hook
        return random.choice(HOOK_TEMPLATES_FANDOM_ONLY).format(fandom=fandom)

    return random.choice(HOOK_TEMPLATES_GENERIC)


def _ctas(n: int = 2) -> list[str]:
    """Pick n random CTAs from the pool."""
    return random.sample(CTA_POOL, min(n, len(CTA_POOL)))


def first_comment_hashtags(enrichment: Enrichment | None = None) -> str:
    """Build the first-comment hashtag dump (posted by instagram.py after publish)."""
    tags = list(EXTRA_FIRST_COMMENT_HASHTAGS)
    if enrichment:
        # Add a few enrichment-derived tags that DIDN'T make the caption (overflow)
        seen = {t.lower() for t in tags}
        for h in enrichment.suggested_hashtags[8:14]:  # skip the first 8 (in caption)
            tag = (h if h.startswith("#") else f"#{h}").lower()
            tag = re.sub(r"[^A-Za-z0-9#_]", "", tag)
            if tag not in seen and len(tag) > 1:
                tags.append(tag)
                seen.add(tag)
    return " ".join(tags[:15])


def compose_reel(
    tweet_text: str,
    enrichment: Enrichment | None = None,
    music: MusicMatch | None = None,
) -> str:
    """Single-video Instagram Reel caption — growth-tuned with hook + CTAs."""
    hook = _hook(tweet_text, enrichment)
    mentions = " ".join(_safe_mentions(enrichment))
    tags = " ".join(_hashtags(enrichment, BASE_HASHTAGS_REEL, cap=10))
    song_line = f"🎵 {music.title} — {music.artist}\n\n" if music else ""
    ctas = "\n".join(_ctas(2))
    return (
        f"{hook}\n\n"
        f"{song_line}"
        f"🎬 by @editsgoeshard on X\n\n"
        f"{ctas}\n\n"
        f"{mentions}\n\n"
        f"{tags}"
    )


def compose_short_title(tweet_text: str, enrichment: Enrichment | None = None) -> str:
    """YouTube Short title — max 100 chars. Front-load keywords + fandom."""
    hook = _hook(tweet_text, enrichment)
    # Append fandom hashtag to title for SEO when we have it
    fandom_tag = ""
    if enrichment and enrichment.fandoms:
        fandom_tag = f" #{enrichment.fandoms[0].lower().replace(' ', '')}"
    title = f"{hook} 🔥{fandom_tag} #Shorts"
    return title[:100]


def compose_short_description(
    tweet_text: str,
    enrichment: Enrichment | None = None,
    music: MusicMatch | None = None,
) -> str:
    """YouTube Short description — up to 5000 chars but keep it tight, mobile-truncated."""
    hook = _hook(tweet_text, enrichment)
    mentions = " ".join(_safe_mentions(enrichment))
    tags = " ".join(_hashtags(enrichment, BASE_HASHTAGS_SHORT, cap=10))
    song_line = f"🎵 {music.title} — {music.artist}\n\n" if music else ""
    ctas = "\n".join(_ctas(2))
    return (
        f"{hook}\n\n"
        f"{song_line}"
        f"🎬 by @EditsGoesHard on X\n"
        f"📲 IG: @fireeditsclub for daily fire 🔥\n\n"
        f"{ctas}\n\n"
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
