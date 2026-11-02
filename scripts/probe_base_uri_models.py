#!/usr/bin/env python3
"""Expand one observed Sonos caret base URI over a bounded model range."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sonos_firmware.discovery import Candidate, probe_all, write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-uri", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument(
        "--placeholder",
        help="caret text when it differs from the catalog version (defaults to --version)",
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model-min", type=int, default=0)
    parser.add_argument("--model-max", type=int, default=80)
    parser.add_argument("--workers", type=int, default=6)
    args = parser.parse_args()
    if "^" not in args.base_uri:
        parser.error("--base-uri must contain a caret placeholder")
    placeholder = args.placeholder or args.version
    candidates = []
    for model in range(args.model_min, args.model_max + 1):
        url = args.base_uri.replace("http://", "https://", 1).replace(
            f"^{placeholder}", f"{placeholder}-1-{model}.upd"
        )
        candidates.append(Candidate(args.version, model, url, "metadata-sweep", url.rsplit("/", 1)[-1]))
    results = probe_all(candidates, args.workers)
    report = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "method": "bounded HEAD expansion of an observed caret base URI",
        "system_version": args.version,
        "base_uri": args.base_uri.replace("http://", "https://", 1),
        "model_min": args.model_min,
        "model_max": args.model_max,
        "candidate_total": len(results),
        "available_total": sum(bool(item.get("available")) for item in results),
        "error_total": sum(bool(item.get("error")) for item in results),
        "candidates": results,
    }
    write_json(args.output, report)
    print(
        f"checked={len(results)} available={report['available_total']} "
        f"errors={report['error_total']}"
    )
    return 1 if report["error_total"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
