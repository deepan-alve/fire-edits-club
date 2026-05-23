"""One-time OAuth bootstrap for the YouTube channel.

Run this once. It opens a browser, you grant the YT scopes to the Fire Edits
Club Google account, and it saves a refresh token to secrets/yt-token.json.
The deployed bot uses that refresh token forever (until the OAuth consent
app is taken out of Testing mode, at which point it expires every 7 days).
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube",
]

ROOT = Path(__file__).parent
CLIENT_SECRET = ROOT / "secrets" / "client_secret.json"
TOKEN_FILE = ROOT / "secrets" / "yt-token.json"


def main() -> None:
    if not CLIENT_SECRET.exists():
        sys.exit(f"Missing {CLIENT_SECRET}. Did you move the downloaded JSON into secrets/?")

    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    creds = flow.run_local_server(
        port=0,
        prompt="consent",  # force the refresh_token to come back
        access_type="offline",
    )

    if not creds.refresh_token:
        sys.exit(
            "No refresh_token returned. This usually means you've granted this "
            "client before. Revoke it at https://myaccount.google.com/permissions "
            "and re-run."
        )

    TOKEN_FILE.write_text(
        json.dumps(
            {
                "refresh_token": creds.refresh_token,
                "client_id": creds.client_id,
                "client_secret": creds.client_secret,
                "token_uri": creds.token_uri,
                "scopes": list(creds.scopes or SCOPES),
            },
            indent=2,
        )
    )
    print(f"[ok] Saved refresh token to {TOKEN_FILE}")

    yt = build("youtube", "v3", credentials=creds, cache_discovery=False)
    resp = yt.channels().list(part="snippet,statistics", mine=True).execute()
    if not resp.get("items"):
        sys.exit("Authed, but no channels on this Google account. Did you log in with the right account?")
    ch = resp["items"][0]
    print(f"[ok] Channel: {ch['snippet']['title']}")
    print(f"     handle:  {ch['snippet'].get('customUrl', '(none yet)')}")
    print(f"     id:      {ch['id']}")
    print(f"     subs:    {ch['statistics'].get('subscriberCount', '?')}")
    print(f"     videos:  {ch['statistics'].get('videoCount', '?')}")


if __name__ == "__main__":
    main()
