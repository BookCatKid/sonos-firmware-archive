#!/usr/bin/env python3
"""Run non-AI desktop-app discovery and report unarchived installers."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def write(path: Path, document: dict) -> None:
    path.write_text(json.dumps(document, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "data/apps/desktop-archive.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--discovery-output", type=Path, required=True)
    args = parser.parse_args()
    try:
        subprocess.run([
            sys.executable, str(ROOT / "scripts/discover_desktop_installers.py"),
            "--output", str(args.discovery_output),
        ], cwd=ROOT, check=True)
        discovery = json.loads(args.discovery_output.read_text())
        archive = json.loads(args.archive.read_text())
    except Exception as error:
        write(args.output, {"schema_version": 1, "status": "error", "error": str(error)})
        return 1
    archived_urls = {
        url for item in archive.get("artifacts", []) for url in item.get("source_urls", [])
    }
    unknown = [
        item for item in discovery["candidates"]
        if item.get("http_status") == 200 and item["url"] not in archived_urls
    ]
    direct_locations = [
        item for item in discovery.get("direct_redirects", []) if item.get("location")
    ]
    unknown_direct = [
        item for item in direct_locations if item["location"] not in archived_urls
    ]
    report = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "status": "change-detected" if unknown or unknown_direct else "ok",
        "discovered_candidate_total": discovery["candidate_total"],
        "live_candidate_total": discovery["available_total"],
        "archived_url_total": len(archived_urls),
        "unknown_installer_total": len(unknown),
        "unknown_direct_redirect_total": len(unknown_direct),
        "direct_redirects": discovery.get("direct_redirects", []),
        "wayback": discovery.get("wayback"),
        "unknown_installers": unknown,
        "unknown_direct_redirects": unknown_direct,
    }
    write(args.output, report)
    print(
        f"desktop apps: live={report['live_candidate_total']} "
        f"archived_urls={report['archived_url_total']} "
        f"unknown={report['unknown_installer_total'] + report['unknown_direct_redirect_total']}"
    )
    return 2 if unknown or unknown_direct else 0


if __name__ == "__main__":
    raise SystemExit(main())
