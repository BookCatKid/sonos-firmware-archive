#!/usr/bin/env python3
"""Stream discovered Sonos desktop installers into verified GitHub Releases.

Only one installer is retained in worker storage at a time. The receipt is rewritten
after every verified upload so interrupted runs are safely resumable.
"""

from __future__ import annotations

import argparse
import urllib.request
from pathlib import Path

from sonos_firmware.app_archive import AppSource, preserve

ROOT = Path(__file__).resolve().parents[1]
RELEASES = {
    ("s2", "windows"): ("apps-s2-windows", "Sonos S2/modern Windows app archive"),
    ("s2", "macos"): ("apps-s2-macos", "Sonos S2/modern macOS app archive"),
    ("s1", "windows"): ("apps-s1-windows", "Sonos S1 Windows app archive"),
    ("s1", "macos"): ("apps-s1-macos", "Sonos S1 macOS app archive"),
    ("legacy", "windows"): ("apps-legacy-windows", "Sonos pre-S1/S2 Windows app archive"),
    ("legacy", "macos"): ("apps-legacy-macos", "Sonos pre-S1/S2 macOS app archive"),
}


def wayback_url(candidate: dict) -> str | None:
    for source in candidate.get("sources", []):
        if source.startswith("wayback-cdx:"):
            timestamp = source.split(":", 2)[1]
            return f"https://web.archive.org/web/{timestamp}id_/{candidate['url']}"
    return None


def download(candidate: dict, destination: Path) -> str:
    urls = [candidate["url"]]
    archived = wayback_url(candidate)
    if archived:
        urls.append(archived)
    last_error = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "sonos-firmware-archive/1"})
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
            return url
        except Exception as error:
            last_error = error
            destination.unlink(missing_ok=True)
    raise last_error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, default=ROOT / "data/apps/desktop-discovery.json")
    parser.add_argument("--receipt", type=Path, default=ROOT / "data/apps/desktop-archive.json")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--limit", type=int, help="maximum acquisition attempts, including failures and refreshes")
    parser.add_argument('--refresh-hours', type=float, default=6, help='recheck current installer bytes at this interval')
    parser.add_argument('--historical-refresh-hours', type=float, default=168)
    args = parser.parse_args()
    source = AppSource(download, lambda candidate: RELEASES[(candidate['family'], candidate['platform'])],
                       'Official Sonos installer captures; exact source URLs, SHA-256 hashes and sizes are recorded in the archive receipt.',
                       default_platform='', refresh_hours=args.historical_refresh_hours, current_refresh_hours=args.refresh_hours)
    result = preserve(source, args.discovery, args.receipt, args.repo, root=ROOT, limit=args.limit)
    print(f"verified artifacts={result['artifact_total']} attempted={result['attempted_this_run']} new={result['added_this_run']}")
    return 1 if result['status'] == 'partial' else 0


if __name__ == "__main__":
    raise SystemExit(main())
