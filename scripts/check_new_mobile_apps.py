#!/usr/bin/env python3
"""Run non-AI official Sonos APK discovery and report unarchived packages."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "data/apps/mobile-archive.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discovery-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        subprocess.run([sys.executable, str(ROOT / "scripts/discover_mobile_installers.py"),
                        "--output", str(args.discovery_output)], cwd=ROOT, check=True)
        discovery = json.loads(args.discovery_output.read_text())
        archive = json.loads(args.archive.read_text())
    except Exception as error:
        args.output.write_text(json.dumps({"schema_version": 1, "status": "error",
                                          "error": str(error)}, indent=2) + "\n")
        return 1
    archived = {url for x in archive.get("artifacts", []) for url in x.get("source_urls", [])}
    known_gaps = {x["url"] for x in archive.get("gaps", [])}
    known = archived | known_gaps
    unknown = [x for x in discovery["candidates"] if x["url"] not in known]
    report = {
        "schema_version": 1, "checked": datetime.now(timezone.utc).isoformat(),
        "status": "change-detected" if unknown else "ok",
        "candidate_total": discovery["candidate_total"],
        "live_total": discovery["available_live_total"],
        "archived_url_total": len(archived), "known_gap_total": len(known_gaps),
        "unknown_total": len(unknown),
        "official_redirect": discovery["official_redirect"],
        "unknown_installers": unknown,
    }
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"mobile APKs: candidates={report['candidate_total']} archived_urls={len(archived)} unknown={len(unknown)}")
    return 2 if unknown else 0


if __name__ == "__main__":
    raise SystemExit(main())
