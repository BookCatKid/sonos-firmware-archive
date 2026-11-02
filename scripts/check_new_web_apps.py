#!/usr/bin/env python3
"""Detect public Sonos web deployment changes without credentials or AI."""

from __future__ import annotations

import argparse
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def load_archiver():
    spec = importlib.util.spec_from_file_location("archive_web_apps", ROOT / "scripts/archive_web_apps.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, default=ROOT / "data/apps/web-app-archive.json")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    web = load_archiver()
    try:
        probes = [web.public_probe(app) for app in web.APPS]
        archive = json.loads(args.archive.read_text()) if args.archive.exists() else {"deployments": []}
        known = {(item["app"], item.get("probe_fingerprint")) for item in archive["deployments"]}
        changed = [item for item in probes if (item["app"], item["fingerprint"]) not in known]
        report = {
            "schema_version": 1,
            "checked": datetime.now(timezone.utc).isoformat(),
            "status": "change-detected" if changed else "ok",
            "probes": probes,
            "changed_deployments": changed,
        }
        code = 2 if changed else 0
    except Exception as error:
        report = {"schema_version": 1, "status": "error", "error": str(error)}
        code = 1
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(f"web apps: status={report['status']}")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
