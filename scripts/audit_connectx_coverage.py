#!/usr/bin/env python3
"""Report live ConnectX (Anacapa, package model 17) historical coverage.

ConnectX is served from a mutable URL, `.../JFFS_Static_Link/connectx.upd`, so
its bytes change as Sonos rolls forward. Every firmware manifest also advertises
a version-pinned copy at `<version>-1-17.upd` in its own directory. Both are
Sonos protocol sources: the archive discovers them by walking manifests and the
protocol root, never by importing a download list.

This audit answers item 3 without acquiring anything:

* how many firmware versions advertise a model-17 component;
* how many of those contents are preserved with a verified release receipt;
* whether preserved contents are genuinely distinct, rather than one content
  republished under many version labels;
* whether every preserved record is manifest-attributed;
* which advertised contents are unavailable at the source, and therefore need a
  historical-index route that Wayback and Common Crawl were observed not to have.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# A model-17 component is only accounted for if it names a terminal state.
PRESERVED = "preserved"
KNOWN_STATES = frozenset({
    PRESERVED, "missing-cdn", "missing", "unavailable", "encrypted-only",
    "download-failed", "not-advertised",
})


def audit(catalog: dict, index: dict | None = None) -> dict:
    packages = [row for row in catalog.get("packages", [])
                if row.get("package_model") == 17]
    preserved = [row for row in packages if row.get("artifact_status") == PRESERVED]
    by_version = defaultdict(list)
    for row in packages:
        by_version[row.get("version")].append(row)

    errors = []
    seen_ids = set()
    for row in packages:
        identity = row.get("id")
        if identity in seen_ids:
            errors.append({"id": identity, "error": "duplicate package identity"})
        seen_ids.add(identity)
        if row.get("artifact_status") not in KNOWN_STATES:
            errors.append({"id": identity, "error": f"unaccounted state {row.get('artifact_status')!r}"})
        if row.get("artifact_status") != PRESERVED:
            continue
        for field in ("sha256", "bytes", "release_tag", "release_asset"):
            if not row.get(field):
                errors.append({"id": identity, "error": f"preserved without {field}"})
        if row.get("release_tag") and not row["release_tag"].startswith("firmware-"):
            errors.append({"id": identity, "error": f"unexpected release tag {row['release_tag']!r}"})

    digests = Counter(row["sha256"] for row in preserved if row.get("sha256"))
    unavailable = [{"version": row.get("version"), "id": row.get("id"),
                    "filename": row.get("filename"), "source_url": row.get("source_url"),
                    "state": row.get("artifact_status")}
                   for row in packages if row.get("artifact_status") != PRESERVED]

    report = {
        "schema_version": 1,
        "advertised_versions": len(by_version),
        "advertised_components": len(packages),
        "preserved_components": len(preserved),
        "distinct_preserved_contents": len(digests),
        "reused_content_across_versions": sorted(
            digest for digest, count in digests.items() if count > 1),
        "manifest_attributed": sum(1 for row in preserved if row.get("source_manifest")),
        "unavailable": unavailable,
        "errors": errors,
    }
    if index is not None:
        snapshots = index.get("snapshots") or []
        index_versions = {row.get("version") for row in snapshots}
        catalog_versions = set(by_version)
        index_digests = Counter(row.get("sha256") for row in snapshots if row.get("sha256"))
        report["historical_index"] = {
            "snapshots": len(snapshots),
            "distinct_contents": len(index_digests),
            "versions_only_in_index": sorted(index_versions - catalog_versions),
            "versions_only_in_catalog": sorted(catalog_versions - index_versions),
            "shared_versions": len(index_versions & catalog_versions),
            # A snapshot with no receipt cannot be represented as preserved.
            "snapshots_missing_extraction": sum(1 for row in snapshots if not row.get("extraction_asset")),
        }
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    parser.add_argument("--index", type=Path, default=ROOT / "data/discovery/connectx-index.json")
    parser.add_argument("--output", type=Path, help="write the coverage report as JSON")
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text())
    index = json.loads(args.index.read_text()) if args.index.exists() else None
    report = audit(catalog, index)
    if args.output:
        from sonos_firmware.storage import atomic_json
        atomic_json(args.output, report)
    print(f"advertised_versions={report['advertised_versions']} "
          f"components={report['advertised_components']} preserved={report['preserved_components']} "
          f"distinct_contents={report['distinct_preserved_contents']} "
          f"manifest_attributed={report['manifest_attributed']} "
          f"unavailable={len(report['unavailable'])} errors={len(report['errors'])}")
    if index is not None:
        historical = report["historical_index"]
        print(f"historical_index snapshots={historical['snapshots']} "
              f"distinct_contents={historical['distinct_contents']} "
              f"index_only={len(historical['versions_only_in_index'])} "
              f"catalog_only={len(historical['versions_only_in_catalog'])}")
    for row in report["unavailable"]:
        print(f"  unavailable {row['version']}/{row['filename']} ({row['state']})")
    for row in report["errors"]:
        print(f"  error {row['id']}: {row['error']}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
