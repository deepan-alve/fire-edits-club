"""Identify the music in a video using AcoustID + Chromaprint (free, no AI).

Pipeline:
    1. fpcalc <video> → audio fingerprint + duration
    2. POST to api.acoustid.org/v2/lookup → AcoustID + MusicBrainz recording IDs
    3. Return best match as (title, artist) or None.

Realistic match rate: ~40-60% for trending edits — many use short, pitched, or
sped-up clips that throw off fingerprinting. When it fails, return None and
the composer skips the song line.
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).parent
KEY_FILE = ROOT / "secrets" / "acoustid-key.txt"


def _find_fpcalc() -> Path:
    found = shutil.which("fpcalc")
    if found:
        return Path(found)
    for f in (Path("/usr/local/bin/fpcalc"), Path.home() / ".local" / "bin" / "fpcalc"):
        if f.exists():
            return f
    return Path("/usr/local/bin/fpcalc")


FPCALC = _find_fpcalc()


@dataclass
class MusicMatch:
    title: str
    artist: str
    score: float
    recording_id: str | None = None


def fingerprint(video_path: Path) -> tuple[int, str]:
    """Return (duration_seconds, fingerprint_string)."""
    if not FPCALC.exists():
        sys.exit(f"fpcalc not found at {FPCALC}")
    result = subprocess.run(
        [str(FPCALC), "-json", str(video_path)],
        capture_output=True, text=True, check=True,
    )
    data = json.loads(result.stdout)
    return int(data["duration"]), data["fingerprint"]


def lookup(duration: int, fp: str, api_key: str) -> MusicMatch | None:
    params = {
        "client": api_key,
        "duration": duration,
        "fingerprint": fp,
        "meta": "recordings+releasegroups+compress",
        "format": "json",
    }
    url = "https://api.acoustid.org/v2/lookup"
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            body = json.load(r)
    except urllib.error.HTTPError as e:
        print(f"  acoustid HTTP {e.code}: {e.read().decode()[:200]}", file=sys.stderr)
        return None

    if body.get("status") != "ok":
        print(f"  acoustid error: {body}", file=sys.stderr)
        return None

    results = body.get("results", [])
    if not results:
        return None
    # results are sorted by descending score
    for r in results:
        recordings = r.get("recordings") or []
        for rec in recordings:
            title = rec.get("title")
            artists = rec.get("artists") or []
            if not title or not artists:
                continue
            artist = ", ".join(a.get("name", "") for a in artists if a.get("name"))
            if not artist:
                continue
            return MusicMatch(
                title=title,
                artist=artist,
                score=r.get("score", 0.0),
                recording_id=rec.get("id"),
            )
    return None


def identify(video_path: Path, api_key: str | None = None) -> MusicMatch | None:
    if api_key is None:
        if not KEY_FILE.exists():
            print(f"  no api key at {KEY_FILE}, skipping music id", file=sys.stderr)
            return None
        api_key = KEY_FILE.read_text().strip()
    duration, fp = fingerprint(video_path)
    return lookup(duration, fp, api_key)


def main() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python music_id.py <video.mp4>")
    path = Path(sys.argv[1])
    print(f"identifying music in {path.name}...")
    match = identify(path)
    if match:
        print(f"  match (score={match.score:.2f}): {match.title} — {match.artist}")
    else:
        print("  no match")


if __name__ == "__main__":
    main()
