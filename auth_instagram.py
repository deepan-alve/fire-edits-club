"""One-time auth bootstrap for the Instagram Graph API.

Reads the short-lived user token + FB app secret, exchanges for a long-lived
user token (60 days, refreshable), fetches the never-expiring Page Access
Token for the Fire Edits Club Page, finds the linked IG Business Account ID,
and writes everything to secrets/ig-token.json.

The deployed bot uses ONLY the values in ig-token.json — it never sees the
short-lived token or the app secret again.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GRAPH = "https://graph.facebook.com/v25.0"
ROOT = Path(__file__).parent
RAW_TOKEN = ROOT / "secrets" / "ig-token-raw.txt"
APP_SECRET_FILE = ROOT / "secrets" / "fb-app-secret.txt"
OUT = ROOT / "secrets" / "ig-token.json"

APP_ID = "2076222469599666"  # FB App ID for Fire Edits Club Bot, from debug_token
TARGET_PAGE_NAME = "Fire Edits Club"


def get(path: str, **params) -> dict:
    url = f"{GRAPH}{path}?{urllib.parse.urlencode(params)}"
    try:
        with urllib.request.urlopen(url, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        sys.exit(f"HTTP {e.code} on {path}: {e.read().decode()[:500]}")


def main() -> None:
    if not RAW_TOKEN.exists():
        sys.exit(f"Missing {RAW_TOKEN}")
    if not APP_SECRET_FILE.exists():
        sys.exit(f"Missing {APP_SECRET_FILE}")

    short_token = RAW_TOKEN.read_text().strip()
    app_secret = APP_SECRET_FILE.read_text().strip()

    print("[1/4] Exchanging short-lived → long-lived user token...")
    longlived = get(
        "/oauth/access_token",
        grant_type="fb_exchange_token",
        client_id=APP_ID,
        client_secret=app_secret,
        fb_exchange_token=short_token,
    )
    user_token = longlived["access_token"]
    expires_in = longlived.get("expires_in", 0)
    expires_at = int(time.time()) + expires_in if expires_in else None
    print(f"      user token expires_in={expires_in}s (~{expires_in // 86400}d)")

    print("[2/4] Fetching Page Access Token from long-lived user token...")
    pages = get(
        "/me/accounts",
        fields="id,name,access_token,instagram_business_account{id,username,name,followers_count,media_count}",
        access_token=user_token,
    )
    target = next(
        (p for p in pages["data"] if p["name"] == TARGET_PAGE_NAME),
        None,
    )
    if not target:
        names = [p["name"] for p in pages["data"]]
        sys.exit(f"Couldn't find Page '{TARGET_PAGE_NAME}'. Pages found: {names}")
    if "instagram_business_account" not in target:
        sys.exit(f"Page '{TARGET_PAGE_NAME}' has no linked IG Business account.")

    page_id = target["id"]
    page_token = target["access_token"]
    ig = target["instagram_business_account"]
    ig_id = ig["id"]
    print(f"      Page: {target['name']} (id={page_id})")
    print(f"      IG:   @{ig['username']} (id={ig_id})")

    print("[3/4] Verifying Page Token can read IG account...")
    verify = get(
        f"/{ig_id}",
        fields="id,username,name,followers_count,media_count,biography,profile_picture_url",
        access_token=page_token,
    )
    print(f"      verified: @{verify['username']} — followers={verify.get('followers_count')} media={verify.get('media_count')}")

    print("[4/4] Saving credentials...")
    OUT.write_text(
        json.dumps(
            {
                "app_id": APP_ID,
                "page_id": page_id,
                "page_name": target["name"],
                "page_access_token": page_token,
                "ig_business_account_id": ig_id,
                "ig_username": ig["username"],
                "user_access_token_longlived": user_token,
                "user_token_expires_at": expires_at,
                "scopes_granted": [
                    "instagram_basic",
                    "instagram_content_publish",
                    "pages_show_list",
                    "pages_read_engagement",
                    "business_management",
                ],
            },
            indent=2,
        )
    )
    OUT.chmod(0o600)
    print(f"      wrote {OUT}")

    print("[cleanup] Removing raw short-lived token and app secret files...")
    RAW_TOKEN.unlink()
    APP_SECRET_FILE.unlink()
    print("      done. secrets/ig-token.json is the only credential file the bot needs.")
    print()
    print("=== SUCCESS ===")
    print(f"  IG Business Account ID: {ig_id}")
    print(f"  Page ID:                {page_id}")
    print(f"  Token type:             Page Access Token (never expires)")


if __name__ == "__main__":
    main()
