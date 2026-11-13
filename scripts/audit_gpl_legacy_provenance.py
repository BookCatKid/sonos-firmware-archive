#!/usr/bin/env python3
"""Reconcile historical GPL/Wayback receipts with direct-Sonos source hashes.

This is entirely offline: it uses already-preserved metadata and never fetches
Wayback, Sonos, or GitHub contents. Historical CDX hashes remain unchanged.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sonos_firmware.gpl_archive import atomic_json, load_json, timestamp, valid_version

ROOT = Path(__file__).resolve().parents[1]


def audit(legacy: dict, direct: dict) -> dict:
    versions = {}
    totals = {"legacy_records": 0, "compared": 0, "identical": 0,
              "different": 0, "pending": 0, "cdx_sha1_mismatches_compared": 0,
              "cdx_sha1_mismatches_with_identical_direct_bytes": 0}
    for release in legacy.get("releases", []):
        version = valid_version(release["release"])
        direct_records = direct.get("artifacts", {}).get(version, {})
        entries = {}
        counts = {k: 0 for k in totals}
        for item in release.get("artifacts", []):
            name = item["filename"]
            old_hash = item.get("local_sha256")
            old_bytes = item.get("local_bytes")
            current = direct_records.get(name)
            verified = bool(current and current.get("published") and current.get("sha256") and
                            isinstance(current.get("bytes"), int))
            compare = {"filename": name, "legacy_sha256": old_hash, "legacy_bytes": old_bytes,
                       "legacy_cdx_sha1": item.get("cdx_sha1"),
                       "legacy_local_sha1": item.get("local_sha1"),
                       "legacy_cdx_digest_match": item.get("cdx_digest_match"),
                       "direct_sha256": current.get("sha256") if verified else None,
                       "direct_bytes": current.get("bytes") if verified else None,
                       "direct_release_asset": current.get("release_asset") if verified else None}
            counts["legacy_records"] += 1
            if verified and old_hash and isinstance(old_bytes, int):
                equal = old_hash.lower() == current["sha256"].lower() and old_bytes == current["bytes"]
                compare["state"] = "identical-direct-source" if equal else "historical-content-differs"
                counts["compared"] += 1
                counts["identical" if equal else "different"] += 1
                if item.get("cdx_digest_match") is False:
                    counts["cdx_sha1_mismatches_compared"] += 1
                    if equal:
                        counts["cdx_sha1_mismatches_with_identical_direct_bytes"] += 1
            else:
                compare["state"] = "awaiting-direct-published-source"
                counts["pending"] += 1
            entries[name] = compare
        for key, value in counts.items():
            totals[key] += value
        versions[version] = {"counts": counts, "entries": entries}
    return {
        "schema_version": 1,
        "updated": timestamp(),
        "source": "published-direct-Sonos-receipts-compared-to-historical-local-metadata",
        "no_wayback_requests": True,
        "totals": totals,
        "versions": versions,
        "limitations": [
            "SHA-256 and size identity demonstrates equality of the preserved files with overwhelming confidence; it does not validate the Wayback CDX digest derivation.",
            "A differing historical index is not necessarily corrupted; the web page can have changed and the direct pipeline preserves the two versions separately.",
            "Publication receipt checksums do not by themselves prove complete corresponding source for a firmware binary.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--legacy", type=Path, default=ROOT / "data/gpl/wayback-catalog.json")
    parser.add_argument("--direct", type=Path, default=ROOT / "data/gpl/live/artifacts.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data/gpl/live/legacy-crosscheck.json")
    args = parser.parse_args()
    result = audit(load_json(args.legacy, {}), load_json(args.direct, {}))
    atomic_json(args.output, result)
    print(json.dumps(result["totals"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
