"""Export X.com cookies from Brave to a Netscape cookies.txt for prod scraper.

Brave stores cookies as an encrypted Chromium SQLite DB. browser_cookie3 reads
it (handles the kernel keyring decryption) and we write them out in the
Netscape format that gallery-dl / yt-dlp expect.

Run this on the same laptop where you're logged into X in Brave. The output
goes to secrets/x-cookies.txt — copy that file to alveta with the rest of
the secrets bundle.

Usage:
    python export_x_cookies.py
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import browser_cookie3

OUT = Path(__file__).parent / "secrets" / "x-cookies.txt"
DOMAINS = (".x.com", ".twitter.com", "x.com", "twitter.com")


def to_netscape_line(c) -> str:
    """One Netscape cookies.txt line."""
    # # Netscape HTTP Cookie File format:
    # domain TAB include_subdomain TAB path TAB secure TAB expires TAB name TAB value
    domain = c.domain
    include_sub = "TRUE" if domain.startswith(".") else "FALSE"
    secure = "TRUE" if c.secure else "FALSE"
    expires = str(int(c.expires)) if c.expires else "0"
    return f"{domain}\t{include_sub}\t{c.path}\t{secure}\t{expires}\t{c.name}\t{c.value}"


def main() -> None:
    print("reading Brave cookies...")
    try:
        jar = browser_cookie3.brave()
    except Exception as e:
        sys.exit(f"failed to read Brave cookies: {e}")

    x_cookies = [c for c in jar if c.domain.lstrip(".") in {"x.com", "twitter.com"}]
    if not x_cookies:
        sys.exit("no X.com / twitter.com cookies found. are you logged in?")

    # Sanity check: critical X auth cookies that gallery-dl needs
    names = {c.name for c in x_cookies}
    critical = {"auth_token", "ct0"}
    missing = critical - names
    if missing:
        print(f"⚠ missing critical cookies: {missing}")
        print("  the scraper may not auth properly. ensure you're logged into x.com in Brave.")

    OUT.parent.mkdir(exist_ok=True)
    lines = ["# Netscape HTTP Cookie File", f"# generated {int(time.time())}"]
    for c in x_cookies:
        lines.append(to_netscape_line(c))
    OUT.write_text("\n".join(lines) + "\n")
    OUT.chmod(0o600)

    print(f"\nwrote {len(x_cookies)} cookies to {OUT}")
    print(f"key cookies present: {sorted(names & critical)}")
    print(f"all cookie names:    {sorted(names)}")


if __name__ == "__main__":
    main()
