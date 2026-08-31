#!/usr/bin/env python3
"""Regenerate per-package UPD manifests and extracted-rootfs inventories."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.catalog import filesystem_manifest  # noqa: E402
from sonos_firmware.upd import parse_file  # noqa: E402


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path, help="local sonos-firmware-downloads directory")
    args = parser.parse_args()
    catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
    for package in catalog["packages"]:
        filename = package.get("filename")
        if not filename:
            continue
        source = args.source / filename
        if not source.exists():
            source = args.source / "legacy-34.16-37101" / filename
        data = source.read_bytes()
        actual = hashlib.sha256(data).hexdigest()
        if actual != package["sha256"]:
            raise ValueError(f"hash mismatch for {source}")
        manifest = {
            "package_id": package["id"],
            "bytes": len(data),
            "sha256": actual,
            "sections": [item.to_dict() for item in parse_file(source)],
        }
        write_json(ROOT / "data/upd" / f"{package['id']}.json", manifest)

    roots = {
        "86.8-78270-1-8": args.source / "raw/Play1-S1-86.8-78270/rootfs-extracted",
        "86.8-78270-1-9": args.source / "raw/Playbar-S9-86.8-78270/rootfs-extracted",
        "34.16-37101-1-9": args.source / "raw/Playbar-S9-34.16-37101/rootfs-extracted",
    }
    for package_id, rootfs in roots.items():
        if rootfs.exists():
            write_json(ROOT / "data/filesystems" / f"{package_id}.json", filesystem_manifest(rootfs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

