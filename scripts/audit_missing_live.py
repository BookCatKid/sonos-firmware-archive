#!/usr/bin/env python3
"""Re-probe every catalog entry marked missing-cdn without downloading it."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sonos_firmware.discovery import Candidate, probe_all, write_json  # noqa: E402


def as_candidate(package: dict) -> Candidate:
    return Candidate(
        version=package["version"],
        package_model=package["package_model"],
        url=package["source_url"],
        source=package["source_manifest"],
        filename=package["filename"],
    )


def on_alternate_host(candidate: Candidate) -> Candidate:
    return Candidate(
        version=candidate.version,
        package_model=candidate.package_model,
        url=candidate.url.replace(
            "https://update-firmware.sonos.com/",
            "https://update.sonos.com/",
            1,
        ),
        source=candidate.source,
        filename=candidate.filename,
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    missing = [
        package
        for package in catalog["packages"]
        if package["artifact_status"] == "missing-cdn"
    ]
    preserved = [
        package
        for package in catalog["packages"]
        if package["artifact_status"] == "preserved" and package.get("source_url")
    ]
    missing_prefixes = {
        package["source_url"].rsplit("/", 1)[0] + "/" for package in missing
    }
    controls_by_prefix = {}
    for package in preserved:
        prefix = package["source_url"].rsplit("/", 1)[0] + "/"
        if prefix in missing_prefixes and prefix not in controls_by_prefix:
            controls_by_prefix[prefix] = package

    canonical_candidates = [as_candidate(package) for package in missing]
    results = probe_all(canonical_candidates, args.workers)
    alternate_results = probe_all(
        [on_alternate_host(candidate) for candidate in canonical_candidates],
        args.workers,
    )
    controls = probe_all(
        [as_candidate(package) for package in controls_by_prefix.values()],
        args.workers,
    )
    receipt = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "method": "HEAD metadata-only, User-Agent sonos-firmware-archive/0.1",
        "candidate_total": len(results),
        "candidate_statuses": dict(
            sorted(Counter(str(result.get("http_status")) for result in results).items())
        ),
        "available_candidate_total": sum(result["available"] for result in results),
        "alternate_host_statuses": dict(
            sorted(
                Counter(
                    str(result.get("http_status")) for result in alternate_results
                ).items()
            )
        ),
        "available_alternate_host_total": sum(
            result["available"] for result in alternate_results
        ),
        "control_total": len(controls),
        "available_control_total": sum(result["available"] for result in controls),
        "candidates": results,
        "alternate_host_candidates": alternate_results,
        "controls": controls,
    }
    write_json(args.output, receipt)
    print(
        f"checked {len(results)} missing candidates; "
        f"{receipt['available_candidate_total']} now available; "
        f"{receipt['available_alternate_host_total']} available on alternate host; "
        f"{receipt['available_control_total']}/{len(controls)} controls available"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
