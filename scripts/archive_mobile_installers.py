#!/usr/bin/env python3
"""Stream discovered official Sonos APKs into hash-verified GitHub Releases."""

from __future__ import annotations

import argparse
import urllib.request
import zipfile
from pathlib import Path

from sonos_firmware.app_archive import AppSource, preserve

ROOT = Path(__file__).resolve().parents[1]
RELEASES = {
    "s2": ("apps-fireos-s2", "Sonos S2/modern official Fire OS APK archive"),
    "s1": ("apps-fireos-s1", "Sonos S1 official Fire OS APK archive"),
    "legacy": ("apps-fireos-legacy", "Sonos legacy official Android APK archive"),
}


class InvalidApkError(RuntimeError):
    pass


def fetch(candidate: dict, destination: Path) -> str:
    urls = [candidate["url"]]
    timestamp = candidate.get("wayback_timestamp")
    if timestamp:
        urls.append(f"https://web.archive.org/web/{timestamp}id_/{candidate['url']}")
    error = None
    for url in urls:
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "sonos-firmware-archive/1"})
            with urllib.request.urlopen(request, timeout=180) as source, destination.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk)
            if not zipfile.is_zipfile(destination):
                destination.unlink(missing_ok=True)
                error = InvalidApkError(f"downloaded body is not a valid ZIP/APK: {url}")
                continue
            return url
        except Exception as caught:
            error = caught; destination.unlink(missing_ok=True)
    raise error


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, default=ROOT / "data/apps/mobile-discovery.json")
    parser.add_argument("--receipt", type=Path, default=ROOT / "data/apps/mobile-archive.json")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--limit", type=int)
    parser.add_argument('--refresh-hours', type=float, default=6, help='recheck current installer bytes at this interval')
    parser.add_argument('--historical-refresh-hours', type=float, default=168)
    args = parser.parse_args()
    source = AppSource(fetch, lambda candidate: RELEASES[candidate['family']],
                       'Official Sonos installer captures; exact source URLs, SHA-256 hashes and sizes are recorded in the archive receipt.',
                       default_platform='fireos-android-apk', refresh_hours=args.historical_refresh_hours, current_refresh_hours=args.refresh_hours)
    result = preserve(source, args.discovery, args.receipt, args.repo, root=ROOT, limit=args.limit)
    print(f"verified artifacts={result['artifact_total']} attempted={result['attempted_this_run']} new={result['added_this_run']}")
    return 1 if result['status'] == 'partial' else 0


if __name__ == "__main__":
    raise SystemExit(main())
