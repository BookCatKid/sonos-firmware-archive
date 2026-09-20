#!/usr/bin/env python3
"""Capture credential-free metadata from the two official Google Play listings."""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

PACKAGES = {
    "com.sonos.acr2": "s2",
    "com.sonos.acr": "s1",
}


def parse_updated_on(text: str) -> str | None:
    """Extract the locale-pinned public listing's update date."""
    decoded = html.unescape(text)
    match = re.search(r'Updated on</div><div class="[^"]+">([^<]+)</div>', decoded)
    return match.group(1) if match else None


def fetch(package: str, family: str) -> dict:
    url = "https://play.google.com/store/apps/details?" + urllib.parse.urlencode(
        {"id": package, "hl": "en_US", "gl": "US"}
    )
    request = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(request, timeout=45) as response:
        body = response.read()
        status = response.status
    text = body.decode("utf-8", "replace")
    return {
        "package": package,
        "family": family,
        "url": url,
        "http_status": status,
        "updated_on": parse_updated_on(text),
        "page_sha256": hashlib.sha256(body).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    listings = [fetch(package, family) for package, family in PACKAGES.items()]
    document = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "scope": "official-public-google-play-listing-metadata",
        "listings": listings,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    for item in listings:
        print(f"{item['package']}: updated_on={item['updated_on']}")
    return 0 if all(x["updated_on"] for x in listings) else 1


if __name__ == "__main__":
    raise SystemExit(main())
