"""Upload a video to YouTube. Auto-classifies as Short if vertical + <60s.

Usage as module:
    from youtube import upload
    video_id = upload(path, title, description, tags)

Usage as smoke test:
    python youtube.py <video.mp4> [title]
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

ROOT = Path(__file__).parent
TOKEN_FILE = ROOT / "secrets" / "yt-token.json"

CATEGORY_ENTERTAINMENT = "24"


def _credentials() -> Credentials:
    data = json.loads(TOKEN_FILE.read_text())
    creds = Credentials(
        token=None,
        refresh_token=data["refresh_token"],
        client_id=data["client_id"],
        client_secret=data["client_secret"],
        token_uri=data["token_uri"],
        scopes=data["scopes"],
    )
    creds.refresh(Request())
    return creds


def upload(
    video_path: Path,
    title: str,
    description: str,
    tags: list[str],
    privacy: str = "public",
    category_id: str = CATEGORY_ENTERTAINMENT,
) -> str:
    if not video_path.exists():
        sys.exit(f"missing video: {video_path}")

    creds = _credentials()
    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)

    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags[:25],
            "categoryId": category_id,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
            "embeddable": True,
        },
    }

    media = MediaFileUpload(
        str(video_path),
        chunksize=4 * 1024 * 1024,
        resumable=True,
        mimetype="video/mp4",
    )
    request = yt.videos().insert(part="snippet,status", body=body, media_body=media)

    print(f"  uploading {video_path.name} ({video_path.stat().st_size:,} bytes)")
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status:
            print(f"    {int(status.progress() * 100)}%")
    video_id = response["id"]
    print(f"  done: https://youtube.com/shorts/{video_id}")
    return video_id


def smoke_test() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python youtube.py <video.mp4> [title]")
    path = Path(sys.argv[1])
    title = sys.argv[2] if len(sys.argv) > 2 else "sheesh, this edit go sooo hard 🔥 #Shorts"
    description = (
        "🎬 Originally posted by @EditsGoesHard on X\n\n"
        "Follow for daily fire edits 🔥\n\n"
        "#edits #fireedits #viraledits #shorts #editsthatgohard"
    )
    tags = [
        "edits", "fire edits", "viral edits", "hard edits", "fireeditsclub",
        "edits that go hard", "shorts", "edit compilation", "tiktok edits",
    ]
    upload(path, title, description, tags, privacy="public")


if __name__ == "__main__":
    smoke_test()
