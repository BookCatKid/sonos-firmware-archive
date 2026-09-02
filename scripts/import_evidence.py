#!/usr/bin/env python3
"""Import redacted firmware-evidence records into the catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.discovery import write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    args = parser.parse_args()

    records = json.loads(args.evidence.read_text(encoding="utf-8"))
    if not isinstance(records, list):
        raise ValueError("evidence file must contain a list of records")

    catalog_path = ROOT / "data/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    evidence = {record["id"]: record for record in catalog.get("evidence", [])}
    for record in records:
        record_id = record.get("id")
        if not record_id:
            raise ValueError("evidence record is missing id")
        evidence[record_id] = record
    catalog["evidence"] = sorted(
        evidence.values(),
        key=lambda record: (record["id"], record.get("version", "")),
    )
    write_json(catalog_path, catalog)
    print(f"registered {len(records)} evidence records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
