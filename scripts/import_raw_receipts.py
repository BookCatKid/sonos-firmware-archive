#!/usr/bin/env python3
"""Register extracted raw-component receipts in the archive catalog."""

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
    parser.add_argument("receipts", type=Path, nargs="+")
    args = parser.parse_args()

    catalog_path = ROOT / "data/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    packages = {item["id"]: item for item in catalog["packages"]}
    raw = {
        (item.get("package_id"), item["filename"]): item
        for item in catalog["raw_images"]
    }

    imported = 0
    for receipt_path in args.receipts:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        for component in receipt["components"]:
            package_id = component["package_id"]
            package = packages.get(package_id)
            if package is None:
                raise ValueError(f"unknown source package {package_id}")
            if component["section_type"] == 13:
                # Older hand-entered records called this model-specific blob
                # "fpga". The neutral section name avoids preserving the same
                # bytes twice when the payload's purpose is not proven.
                raw = {
                    key: value
                    for key, value in raw.items()
                    if not (
                        value.get("package_id") == package_id
                        and value.get("kind") in {"fpga", "device-payload"}
                    )
                }
            item = {
                **component,
                "release_tag": package["release_tag"],
                "release_asset": component["filename"],
            }
            raw[(package_id, component["filename"])] = item
            package["raw_status"] = "complete"
            imported += 1

    catalog["raw_images"] = sorted(
        raw.values(),
        key=lambda item: (
            item.get("package_id", ""),
            item.get("version", ""),
            item.get("kind", ""),
            item["filename"],
        ),
    )
    write_json(catalog_path, catalog)
    print(f"registered {imported} raw components from {len(args.receipts)} receipts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
