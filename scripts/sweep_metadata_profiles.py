#!/usr/bin/env python3
"""Run and checkpoint a large, rate-limited synthetic metadata profile sweep."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sonos_firmware.metadata_sweep import run_sweep  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--raw-directory", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=1200)
    parser.add_argument("--delay", type=float, default=0.2)
    args = parser.parse_args()
    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    result = run_sweep(
        catalog,
        args.output,
        args.raw_directory,
        limit=args.limit,
        delay=args.delay,
    )
    print(
        f"status={result['status']} profiles={result['completed_profile_total']}/"
        f"{result['planned_profile_total']} variants={result['unique_response_total']} "
        f"unknown_manifests={len(result['unknown_manifest_uris'])} "
        f"errors={result['error_total']}"
    )
    return 0 if result["status"] == "complete" and not result["error_total"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
