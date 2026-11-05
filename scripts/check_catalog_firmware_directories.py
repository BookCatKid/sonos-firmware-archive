#!/usr/bin/env python3
"""Expand cataloged opaque firmware directories over speaker package IDs."""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.discovery import Candidate, probe, write_json  # noqa: E402

DIRECTORY_RE = re.compile(
    r"^(https?://update(?:-firmware)?\.sonos\.com/firmware/(?:Prod|Dev|Gold|Alpha|Beta)/[^/]+)/[^/?]+"
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-min", type=int, default=0)
    parser.add_argument("--model-max", type=int, default=80)

    parser.add_argument("--monitor-report", type=Path)
    parser.add_argument("--seeds", type=Path, default=ROOT / "config/discovery-seeds.json")
    parser.add_argument("--max-probes", type=int, default=8000)
    parser.add_argument("--max-seconds", type=float, default=2400)
    parser.add_argument("--delay", type=float, default=.25)
    args = parser.parse_args()
    if not 0 <= args.model_min <= args.model_max <= 255 or args.max_probes < 1 or args.delay < 0:
        parser.error("invalid bounded scan limits")

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    known = {
        package["id"]: package
        for package in catalog["packages"]
        if package.get("artifact_type") == "upd"
    }
    directories: set[tuple[str, str]] = set()
    for package in catalog["packages"]:
        match = DIRECTORY_RE.match(package.get("source_url", ""))
        if match and package.get("artifact_type") in {"dmg", "exe", "upd"}:
            directories.add((package["version"], match.group(1).replace("http://", "https://", 1)))

    from sonos_firmware.crawl import official, normalize, PACKAGE
    seeds = json.loads(args.seeds.read_text()) if args.seeds.exists() else []
    for item in seeds:
        url = normalize(item["url"])
        name = url.rsplit("/", 1)[-1]
        match = PACKAGE.match(name)
        if official(url) and match:
            directories.add((match.group(1), url.rsplit("/", 1)[0]))
    incomplete_reasons: list[str] = []
    monitor_incomplete_evidence = {}
    if args.monitor_report:
        monitor = json.loads(args.monitor_report.read_text())
        if monitor.get("status") == "error":
            write_json(args.output, {"schema_version":1, "incomplete":True, "error_total":1,
                "error":"directory probes skipped after monitor error", "uncatalogued_available":[],
                "uncatalogued_available_total":0})
            return 1
        if monitor.get("discovery_incomplete"):
            incomplete_reasons.append("monitor reported incomplete discovery")
            monitor_incomplete_evidence = {
                "discovery_graph": monitor.get("discovery_graph", {}),
                "manifests": monitor.get("manifests", []),
            }
        graph = monitor.get("discovery_graph", {})
        if graph.get("limits_reached"):
            incomplete_reasons.append("monitor traversal reached its manifest limit")
        if any(f.get("kind") == "error" for f in graph.get("failures", [])):
            incomplete_reasons.append("monitor traversal recorded fetch errors")
        challenged = monitor.get("discovery_graph", {}).get("blocked") or any(
            x.get("http_status") in {403,429} for key in ("current_candidates", "known_missing_candidates", "alternate_host_candidates", "controls")
            for x in monitor.get(key, []))
        if challenged:
            write_json(args.output, {"schema_version":1, "incomplete":True, "error_total":1,
                "error":"directory probes skipped after source challenge", "uncatalogued_available":[],
                "uncatalogued_available_total":0})
            return 1
        for row in monitor.get("discovery_graph", {}).get("directories", []):
            if official(row["directory"] + "/update.upm"):
                directories.add((row["version"], row["directory"]))

    candidates = []
    for version, directory in sorted(directories):
        for model in range(args.model_min, args.model_max + 1):
            filename = f"{version}-1-{model}.upd"
            candidates.append(
                Candidate(version, model, f"{directory}/{filename}", "catalog-directory", filename)
            )
    candidates = sorted({c.url: c for c in candidates}.values(), key=lambda c: (c.version, c.package_model, c.url))
    results = []
    deadline = time.monotonic() + args.max_seconds if args.max_seconds else None
    for c in candidates[:args.max_probes]:
        if deadline is not None and time.monotonic() > deadline:
            incomplete_reasons.append("probe time budget exhausted")
            break
        row = probe(c)
        results.append(row)
        if row.get("http_status") in {403, 429}:
            break
        if args.delay:
            time.sleep(args.delay)
    incomplete = len(results) < len(candidates) or bool(incomplete_reasons)
    discoveries = [
        item
        for item in results
        if item.get("available")
        and known.get(f"{item['version']}-1-{item['package_model']}", {}).get("artifact_status")
        != "preserved"
    ]
    report = {
        "schema_version": 1,
        "incomplete": incomplete,
        "incomplete_reasons": incomplete_reasons,
        "monitor_incomplete_evidence": monitor_incomplete_evidence,
        "pending_total": len(candidates) - len(results),
        "checked": datetime.now(timezone.utc).isoformat(),
        "method": "bounded sequential HEAD expansion of exact directories evidenced by catalog, public seeds or current traversal; no campaign-code guessing",
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
    if report["error_total"] or incomplete:
        return 1
    return 2 if discoveries else 0


if __name__ == "__main__":
    raise SystemExit(main())
