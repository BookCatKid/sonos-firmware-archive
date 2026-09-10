#!/usr/bin/env python3
"""Check public Sonos metadata and known URLs for new downloadable firmware."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sonos_firmware.discovery import write_json  # noqa: E402
from sonos_firmware.monitor import load_catalog, run_monitor  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    try:
        report = run_monitor(load_catalog(args.catalog), args.workers)
    except Exception as error:
        write_json(args.output, {"schema_version": 1, "status": "error", "error": str(error)})
        print(f"monitor failed: {error}", file=sys.stderr)
        return 1
    report["status"] = "change-detected" if report["change_detected"] else "ok"
    write_json(args.output, report)
    print(
        f"checked {report['current_candidate_total']} current candidates and "
        f"{report['known_missing_checked_total']} known missing URLs; "
        f"status={report['status']}"
    )
    return 2 if report["change_detected"] else (1 if report["probe_errors"] else 0)


if __name__ == "__main__":
    raise SystemExit(main())
