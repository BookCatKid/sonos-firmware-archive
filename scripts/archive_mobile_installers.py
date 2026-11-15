#!/usr/bin/env python3
"""Stream discovered official Sonos APKs into hash-verified GitHub Releases."""

from __future__ import annotations

import argparse
from pathlib import Path

from sonos_firmware.app_archive import preserve
from sonos_firmware.installers import source as installer_source

ROOT = Path(__file__).resolve().parents[1]

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, default=ROOT / "data/apps/mobile-discovery.json")
    parser.add_argument("--receipt", type=Path, default=ROOT / "data/apps/mobile-archive.json")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--limit", type=int)
    parser.add_argument('--refresh-hours', type=float, default=6, help='recheck current installer bytes at this interval')
    parser.add_argument('--historical-refresh-hours', type=float, default=168)
    args = parser.parse_args()
    source = installer_source(refresh_hours=args.historical_refresh_hours,
                              current_refresh_hours=args.refresh_hours,
                              default_platform='fireos-android-apk')
    result = preserve(source, args.discovery, args.receipt, args.repo, root=ROOT, limit=args.limit)
    print(f"verified artifacts={result['artifact_total']} attempted={result['attempted_this_run']} new={result['added_this_run']}")
    return 1 if result['status'] == 'partial' else 0


if __name__ == "__main__":
    raise SystemExit(main())
