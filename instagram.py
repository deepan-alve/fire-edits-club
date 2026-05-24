"""Upload a video to Instagram as a Reel via Graph API.

The IG Content Publishing API requires the video at a publicly-hosted URL —
Meta downloads it from there, doesn't accept direct file upload. For smoke
tests we use 0x0.st (anonymous file hosting); for prod we'll set up
media.deepanalve.dev on alveta.

Two-step API flow:
    1. POST /{ig_id}/media          → returns container_id
    2. (poll container until FINISHED)
    3. POST /{ig_id}/media_publish  → returns media_id (the Reel ID)

Usage as module:
    from instagram import upload_reel
    media_id = upload_reel(path, caption)

Usage as smoke test:
    python instagram.py <video.mp4>
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).parent
TOKEN_FILE = ROOT / "secrets" / "ig-token.json"
GRAPH = "https://graph.facebook.com/v25.0"

# In prod, MEDIA_BASE_URL points at our nginx static server (alveta).
# In dev, it's unset and we fall back to catbox.moe.
MEDIA_BASE_URL = os.environ.get("MEDIA_BASE_URL", "").rstrip("/")
MEDIA_UPLOAD_DIR = ROOT / "data" / "uploads"


def _load_creds() -> dict:
    return json.loads(TOKEN_FILE.read_text())


class GraphError(Exception):
    def __init__(self, code: int, body: str):
        self.code = code
        self.body = body
        super().__init__(f"HTTP {code}: {body[:500]}")


def _graph_post(path: str, params: dict) -> dict:
    data = urllib.parse.urlencode(params).encode()
    req = urllib.request.Request(f"{GRAPH}{path}", data=data, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise GraphError(e.code, e.read().decode())


def _graph_get(path: str, **params) -> dict:
    url = f"{GRAPH}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise GraphError(e.code, e.read().decode())


def host_temporarily(video_path: Path) -> str:
    """Return a publicly-downloadable URL for the video.

    Prod path: copy into MEDIA_UPLOAD_DIR (bind-mounted into the nginx
    static server) and return MEDIA_BASE_URL/<filename>. Lifecycle: orchestrator
    cleans these up after publish.

    Dev fallback: upload to catbox.moe (anonymous, free, ~indefinite retention).
    """
    if not video_path.exists():
        sys.exit(f"missing video: {video_path}")

    if MEDIA_BASE_URL:
        MEDIA_UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
        fname = f"{uuid.uuid4().hex}_{video_path.name}"
        dst = MEDIA_UPLOAD_DIR / fname
        shutil.copy(video_path, dst)
        url = f"{MEDIA_BASE_URL}/{fname}"
        print(f"  hosting via prod nginx: {url}")
        return url

    print(f"  hosting {video_path.name} on catbox.moe ({video_path.stat().st_size:,} bytes)...")
    result = subprocess.run(
        [
            "curl", "-sSL",
            "-F", "reqtype=fileupload",
            "-F", f"fileToUpload=@{video_path}",
            "https://catbox.moe/user/api.php",
        ],
        capture_output=True, text=True, check=True,
    )
    url = result.stdout.strip()
    if not url.startswith("http"):
        sys.exit(f"catbox.moe upload failed:\n{result.stdout}\n{result.stderr}")
    print(f"    {url}")
    return url


def upload_reel(video_path: Path, caption: str) -> str:
    creds = _load_creds()
    ig_id = creds["ig_business_account_id"]
    page_token = creds["page_access_token"]
    user_token = creds.get("user_access_token_longlived", page_token)

    video_url = host_temporarily(video_path)

    print("  step 1/3: creating media container...")
    container = _graph_post(
        f"/{ig_id}/media",
        {
            "media_type": "REELS",
            "video_url": video_url,
            "caption": caption,
            "share_to_feed": "true",
            "access_token": page_token,
        },
    )
    container_id = container["id"]
    print(f"    container_id={container_id}")

    # Empirical: page token can't read container status (HTTP 400 subcode 33),
    # but the long-lived user token can. Page token is used for create + publish.
    print("  step 2/3: polling until container is ready (Meta is downloading + transcoding)...")
    time.sleep(3)
    for attempt in range(60):
        try:
            status = _graph_get(
                f"/{container_id}",
                fields="status_code,status",
                access_token=user_token,
            )
        except GraphError as e:
            print(f"    [{attempt+1:>2}] read err: HTTP {e.code} {e.body[:120]}")
            time.sleep(5)
            continue
        code = status.get("status_code")
        print(f"    [{attempt+1:>2}] status_code={code}")
        if code == "FINISHED":
            break
        if code in ("ERROR", "EXPIRED"):
            sys.exit(f"container failed: {status}")
        time.sleep(5)
    else:
        sys.exit("container didn't finish in 5 min")

    print("  step 3/3: publishing...")
    try:
        published = _graph_post(
            f"/{ig_id}/media_publish",
            {"creation_id": container_id, "access_token": page_token},
        )
    except GraphError as e:
        sys.exit(f"publish failed: {e}")
    media_id = published["id"]
    print(f"  done: media_id={media_id}")

    for tok in (user_token, page_token):
        try:
            permalink = _graph_get(f"/{media_id}", fields="permalink", access_token=tok)
            if "permalink" in permalink:
                print(f"        url: {permalink['permalink']}")
                break
        except GraphError:
            continue
    return media_id


def post_comment(media_id: str, message: str) -> str | None:
    """Post a comment on a published IG media (used for first-comment hashtag dump).

    Requires the `instagram_manage_comments` scope (we granted it in OAuth).
    Returns the comment_id on success, None on failure (non-fatal).
    """
    creds = _load_creds()
    page_token = creds["page_access_token"]
    try:
        resp = _graph_post(
            f"/{media_id}/comments",
            {"message": message, "access_token": page_token},
        )
        return resp.get("id")
    except GraphError as e:
        print(f"  ⚠ first-comment failed (non-fatal): {e}")
        return None


def smoke_test() -> None:
    if len(sys.argv) < 2:
        sys.exit("usage: python instagram.py <video.mp4>")
    path = Path(sys.argv[1])
    caption = (
        "sheesh, this edit go sooo hard 🔥\n\n"
        "🎬 Originally by @editsgoeshard on X\n"
        "Daily fire edits curated for you\n\n"
        "#edits #fireedits #viraledits #editsthatgohard #reels "
        "#editsoftheday #hardedits #viralreels"
    )
    upload_reel(path, caption)


if __name__ == "__main__":
    smoke_test()
