#!/usr/bin/env python3
"""Merge a discovery receipt into the catalog and generate UPD manifests."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.discovery import write_json  # noqa: E402
from sonos_firmware.upd import parse_file  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("discovery", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("artifacts", type=Path)
    args = parser.parse_args()

    catalog_path = ROOT / "data/catalog.json"
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
    receipt = json.loads(args.receipt.read_text(encoding="utf-8"))
    downloaded = {item["url"]: item for item in receipt["artifacts"] if item.get("downloaded")}
    existing = {}
    for item in catalog["packages"]:
        # UPD package IDs intentionally omit the extension. Other artifact
        # types retain it so a desktop installer and device package for the
        # same version/model cannot overwrite one another.
        if item.get("filename") and item.get("artifact_type") not in (None, "upd"):
            item["id"] = item["filename"]
        existing[item["id"]] = item
    model_map = {
        item["package_model"]: item
        for item in json.loads((ROOT / "config/models.json").read_text(encoding="utf-8"))["models"]
    }

    packages_by_id = dict(existing)
    generated = 0
    for candidate in discovery["candidates"]:
        artifact = downloaded.get(candidate["url"])
        package_id = (
            candidate["filename"].rsplit(".", 1)[0]
            if candidate["filename"].endswith(".upd")
            else candidate["filename"]
        )
        prior = existing.get(package_id, {})
        model = model_map.get(candidate["package_model"], {})
        sections = None
        if artifact and candidate["filename"].endswith(".upd"):
            sections = parse_file(args.artifacts / candidate["filename"])
        raw_status = prior.get(
            "raw_status",
            "complete"
            if sections is not None and all(not section.encrypted for section in sections)
            else "encrypted"
            if artifact
            else "unknown",
        )
        item = {
            "id": package_id,
            "version": candidate["version"],
            "package_model": candidate["package_model"],
            "product": model.get("display_name") or prior.get("product"),
            "model_number": None,
            "filename": candidate["filename"],
            "artifact_type": Path(candidate["filename"]).suffix.removeprefix("."),
            "bytes": artifact.get("bytes") if artifact else candidate.get("bytes"),
            "sha256": artifact.get("sha256") if artifact else None,
            "artifact_status": "preserved" if artifact else "missing-cdn",
            "raw_status": raw_status if candidate["filename"].endswith(".upd") else "not-applicable",
            "source_url": candidate["url"],
            "source_manifest": prior.get("source_manifest", discovery.get("system_version")),
        }
        if artifact:
            item.update(release_tag=f"firmware-{candidate['version']}", release_asset=candidate["filename"])
        elif prior.get("artifact_status") == "preserved":
            item.update(
                artifact_status="preserved",
                bytes=prior.get("bytes"),
                sha256=prior.get("sha256"),
                release_tag=prior.get("release_tag"),
                release_asset=prior.get("release_asset"),
            )
        if prior.get("note"):
            item["note"] = prior["note"]
        packages_by_id[package_id] = item

        if artifact and candidate["filename"].endswith(".upd"):
            write_json(
                ROOT / "data/upd" / f"{package_id}.json",
                {
                    "package_id": package_id,
                    "bytes": (args.artifacts / candidate["filename"]).stat().st_size,
                    "sha256": artifact["sha256"],
                    "sections": [section.to_dict() for section in sections],
                },
            )
            generated += 1

    catalog["generated"] = datetime.now(timezone.utc).date().isoformat()
    catalog.pop("release_tag", None)
    catalog["packages"] = sorted(packages_by_id.values(), key=lambda item: (item["version"], item["package_model"], item["id"]))
    write_json(catalog_path, catalog)
    print(f"cataloged {len(packages_by_id)} artifacts; generated {generated} UPD records")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
