#!/usr/bin/env python3
"""Register extracted raw-component receipts in the archive catalog."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.catalog import EXTRACTABLE_SECTION_TYPES, SECTION_KIND  # noqa: E402
from sonos_firmware.discovery import write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("receipts", type=Path, nargs="+")
    parser.add_argument("--upd-manifest-directory", type=Path, default=ROOT / "data/upd")
    args = parser.parse_args()

    catalog_path = ROOT / "data/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    packages = {item["id"]: item for item in catalog["packages"]}
    raw = {
        (item.get("package_id"), item["filename"]): item
        for item in catalog["raw_images"]
    }

    imported = 0
    affected: set[str] = set()
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
            affected.add(package_id)
            imported += 1

    for package_id in affected:
        # "complete" must mean every extractable section has a component.
        # A partial import (e.g. only the plaintext sections of a package
        # whose rootfs still awaits a key) must not flip the status: the
        # catalog validator reads "complete" as "encrypted sections have
        # extracted assets too" and would demand components no key can
        # produce yet.
        package = packages[package_id]
        manifest_path = args.upd_manifest_directory / f"{package_id}.json"
        if not manifest_path.is_file():
            package["raw_status"] = "complete"
            continue
        sections = json.loads(manifest_path.read_text(encoding="utf-8")).get("sections", [])
        def covered(section):
            if any(item.get("section_index") == section.get("index")
                   for (pid, _), item in raw.items() if pid == package_id):
                return True
            kind = SECTION_KIND.get(section.get("section_type"))
            return any(
                pid == package_id and item.get("section_index") is None and item.get("kind") == kind
                for (pid, _), item in raw.items()
            )
        needed = [s for s in sections if s.get("section_type") in EXTRACTABLE_SECTION_TYPES]
        if needed and all(covered(s) for s in needed):
            package["raw_status"] = "complete"

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
