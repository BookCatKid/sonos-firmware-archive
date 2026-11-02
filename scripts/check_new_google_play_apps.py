#!/usr/bin/env python3
"""Detect changes to official Sonos Google Play listing update dates."""

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
    parser.add_argument("--baseline", type=Path,
                        default=ROOT / "data/apps/google-play-listings.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discovery-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        subprocess.run([
            sys.executable, str(ROOT / "scripts/discover_google_play_listings.py"),
            "--output", str(args.discovery_output),
        ], cwd=ROOT, check=True)
        current = json.loads(args.discovery_output.read_text())
        baseline = json.loads(args.baseline.read_text())
        known = {x["package"]: x.get("updated_on") for x in baseline["listings"]}
        changed = [x for x in current["listings"] if known.get(x["package"]) != x.get("updated_on")]
        report = {
            "schema_version": 1,
            "checked": datetime.now(timezone.utc).isoformat(),
            "status": "change-detected" if changed else "ok",
            "changed_total": len(changed),
            "changed_listings": changed,
            "baseline_updated_on": known,
        }
        code = 2 if changed else 0
    except Exception as error:
        report = {"schema_version": 1, "status": "error", "error": str(error)}
        code = 1
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"Google Play listings: status={report['status']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
