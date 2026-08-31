#!/usr/bin/env python3
"""Import one exact artifact with explicit provenance into the catalog."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.discovery import write_json  # noqa: E402
from sonos_firmware.upd import parse_file  # noqa: E402


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--version", required=True)
    parser.add_argument("--model", required=True, type=int)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--release-tag", required=True)
    parser.add_argument("--artifact-class", default="production")
    args = parser.parse_args()

    extension = args.file.suffix.removeprefix(".")
    package_id = args.file.stem if extension == "upd" else args.file.name
    item = {
        "id": package_id,
        "version": args.version,
        "package_model": args.model,
        "product": None,
        "model_number": None,
        "filename": args.file.name,
        "artifact_type": extension,
        "artifact_class": args.artifact_class,
        "bytes": args.file.stat().st_size,
        "sha256": sha256(args.file),
        "artifact_status": "preserved",
        "raw_status": "encrypted" if extension == "upd" else "not-applicable",
        "source_url": args.source_url,
        "provenance": args.provenance,
        "release_tag": args.release_tag,
        "release_asset": args.file.name,
    }
    catalog_path = ROOT / "data/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    packages = {entry["id"]: entry for entry in catalog["packages"]}
    packages[package_id] = item
    catalog["packages"] = sorted(packages.values(), key=lambda entry: (entry["version"], entry["package_model"], entry["id"]))
    write_json(catalog_path, catalog)

    if extension == "upd":
        write_json(
            ROOT / "data/upd" / f"{package_id}.json",
            {
                "package_id": package_id,
                "bytes": item["bytes"],
                "sha256": item["sha256"],
                "sections": [section.to_dict() for section in parse_file(args.file)],
            },
        )
    print(f"imported {package_id} {item['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
