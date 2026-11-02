#!/usr/bin/env python3
"""Detect public Apple App Store metadata changes for the Sonos iOS apps."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APPS = {
    1488977981: "s2",
    293523031: "s1",
}
LOOKUP = "https://itunes.apple.com/lookup"
COMPARE_FIELDS = (
    "version",
    "current_version_release_date",
    "minimum_os_version",
    "file_size_bytes",
    "release_notes",
)


def fetch() -> dict:
    query = urllib.parse.urlencode({
        "id": ",".join(str(value) for value in APPS),
        "country": "us",
    })
    request = urllib.request.Request(
        f"{LOOKUP}?{query}",
        headers={"User-Agent": "sonos-firmware-archive/1"},
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        payload = json.load(response)
    by_id = {int(item["trackId"]): item for item in payload.get("results", [])}
    missing = sorted(set(APPS) - set(by_id))
    if missing:
        raise RuntimeError(f"Apple lookup omitted app IDs: {missing}")

    listings = []
    for track_id, family in APPS.items():
        item = by_id[track_id]
        listings.append({
            "family": family,
            "track_id": track_id,
            "name": item.get("trackName"),
            "bundle_id": item.get("bundleId"),
            "version": item.get("version"),
            "current_version_release_date": item.get("currentVersionReleaseDate"),
            "minimum_os_version": item.get("minimumOsVersion"),
            "file_size_bytes": int(item["fileSizeBytes"]) if item.get("fileSizeBytes") else None,
            "release_notes": item.get("releaseNotes"),
            "track_view_url": item.get("trackViewUrl"),
        })
    return {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "scope": "public-apple-itunes-lookup-metadata",
        "lookup_url": f"{LOOKUP}?{query}",
        "listings": listings,
    }


def changed(current: dict, baseline: dict) -> list[dict]:
    old = {item["track_id"]: item for item in baseline.get("listings", [])}
    changes = []
    for item in current["listings"]:
        prior = old.get(item["track_id"])
        fields = [
            field for field in COMPARE_FIELDS
            if prior is None or prior.get(field) != item.get(field)
        ]
        if fields:
            changes.append({
                "track_id": item["track_id"],
                "family": item["family"],
                "name": item["name"],
                "version": item["version"],
                "previous_version": prior.get("version") if prior else None,
                "changed_fields": fields,
                "track_view_url": item["track_view_url"],
            })
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--baseline",
        type=Path,
        default=ROOT / "data/apps/apple-app-store.json",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discovery-output", type=Path, required=True)
    args = parser.parse_args()

    try:
        current = fetch()
        baseline = (
            json.loads(args.baseline.read_text())
            if args.baseline.exists()
            else {"listings": []}
        )
        changes = changed(current, baseline)
        report = {
            "schema_version": 1,
            "checked": datetime.now(timezone.utc).isoformat(),
            "status": "change-detected" if changes else "ok",
            "changed_total": len(changes),
            "changed_listings": changes,
        }
        code = 2 if changes else 0
    except Exception as error:
        current = None
        report = {
            "schema_version": 1,
            "status": "error",
            "error": str(error),
        }
        code = 1

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    if current is not None:
        args.discovery_output.parent.mkdir(parents=True, exist_ok=True)
        args.discovery_output.write_text(json.dumps(current, indent=2) + "\n")
    print(f"Apple App Store: status={report['status']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
