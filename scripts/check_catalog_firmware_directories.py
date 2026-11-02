#!/usr/bin/env python3
"""Expand cataloged opaque firmware directories over speaker package IDs."""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.discovery import Candidate, probe_all, write_json  # noqa: E402

DIRECTORY_RE = re.compile(
    r"^(https?://update(?:-firmware)?\.sonos\.com/firmware/(?:Prod|Dev)/[^/]+)/[^/?]+"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-min", type=int, default=0)
    parser.add_argument("--model-max", type=int, default=80)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    known = {
        package["id"]: package
        for package in catalog["packages"]
        if package.get("artifact_type") == "upd"
    }
    directories: set[tuple[str, str]] = set()
    for package in catalog["packages"]:
        match = DIRECTORY_RE.match(package.get("source_url", ""))
        if match and package.get("artifact_type") in {"dmg", "exe"}:
            directories.add((package["version"], match.group(1).replace("http://", "https://", 1)))

    candidates = []
    for version, directory in sorted(directories):
        for model in range(args.model_min, args.model_max + 1):
            filename = f"{version}-1-{model}.upd"
            candidates.append(
                Candidate(version, model, f"{directory}/{filename}", "catalog-directory", filename)
            )
    results = probe_all(candidates, args.workers)
    discoveries = [
        item
        for item in results
        if item.get("available")
        and known.get(f"{item['version']}-1-{item['package_model']}", {}).get("artifact_status")
        != "preserved"
    ]
    report = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "method": "bounded HEAD expansion of opaque directories already evidenced by official desktop installers",
        "directory_total": len(directories),
        "candidate_total": len(results),
        "available_total": sum(bool(item.get("available")) for item in results),
        "uncatalogued_available_total": len(discoveries),
        "error_total": sum(bool(item.get("error")) for item in results),
        "uncatalogued_available": discoveries,
        "directories": [
            {"version": version, "directory": directory}
            for version, directory in sorted(directories)
        ],
        "candidates": results,
    }
    write_json(args.output, report)
    print(
        f"directories={len(directories)} checked={len(results)} "
        f"live={report['available_total']} new={len(discoveries)} "
        f"errors={report['error_total']}"
    )
    if report["error_total"]:
        return 1
    return 2 if discoveries else 0


if __name__ == "__main__":
    raise SystemExit(main())
