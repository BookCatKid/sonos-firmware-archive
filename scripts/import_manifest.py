#!/usr/bin/env python3
"""Register an exact update-manifest snapshot and its provenance."""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.discovery import write_json  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("file", type=Path)
    parser.add_argument("--source-url", required=True)
    parser.add_argument("--provenance", required=True)
    parser.add_argument("--release-tag", required=True)
    args = parser.parse_args()

    data = args.file.read_bytes()
    text = data.decode("utf-8")
    closing = "</update_manifest>"
    end = text.find(closing)
    root = ET.fromstring(text[: end + len(closing)])
    item = {
        "version": root.get("system_version"),
        "default_version": root.get("default_version"),
        "revision": root.get("revision"),
        "filename": args.file.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "source_url": args.source_url,
        "provenance": args.provenance,
        "release_tag": args.release_tag,
        "release_asset": args.file.name,
    }
    catalog_path = ROOT / "data/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    manifests = {entry["filename"]: entry for entry in catalog["source_manifests"]}
    manifests[item["filename"]] = item
    catalog["source_manifests"] = sorted(manifests.values(), key=lambda entry: entry["filename"])
    write_json(catalog_path, catalog)
    print(f"registered {item['filename']} {item['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
