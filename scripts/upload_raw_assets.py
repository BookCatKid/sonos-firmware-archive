#!/usr/bin/env python3
"""Upload locally extracted raw components beside their source OTA Releases."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from archive_web_apps import public_release, remote_assets, upload_asset  # noqa: E402
from upload_firmware_release import upload_streaming, verified_remote_digest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=ROOT / "artifacts/raw")
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--package-id", action="append", default=[])
    parser.add_argument("--preflight-output", type=Path)
    parser.add_argument(
        "--require-absent",
        action="store_true",
        help="abort if any intended plaintext filename already exists in its release",
    )
    args = parser.parse_args()

    catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
    expected = {
        item["filename"]: item
        for item in catalog["raw_images"]
        if item.get("release_tag") and item.get("release_asset")
    }
    requested = set(args.package_id)
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(args.directory.glob("*/*")):
        item = expected.get(path.name)
        if item and (not requested or item.get("package_id") in requested):
            groups[item["release_tag"]].append(path)

    preflight: list[dict] = []
    existing_targets: list[str] = []
    for tag, paths in sorted(groups.items()):
        assets = remote_assets("BookCatKid/sonos-firmware-archive", tag)
        names = [path.name for path in paths]
        existing = [name for name in names if name in assets]
        existing_targets.extend(f"{tag}/{name}" for name in existing)
        preflight.append(
            {
                "release_tag": tag,
                "intended_assets": names,
                "intended_assets_already_present": existing,
            }
        )
    if args.preflight_output:
        args.preflight_output.write_text(
            json.dumps(
                {
                    "audited_at": datetime.now(timezone.utc).isoformat(),
                    "repository": "BookCatKid/sonos-firmware-archive",
                    "releases": preflight,
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    if args.require_absent and existing_targets:
        for target in existing_targets:
            print(f"error: intended asset already exists: {target}")
        return 1

    def upload(entry: tuple[str, list[Path]]) -> tuple[str, int]:
        tag, paths = entry
        release = public_release("BookCatKid/sonos-firmware-archive", tag)
        for path in paths:
            if path.stat().st_size >= 32 * 1024 * 1024:
                upload_streaming("BookCatKid/sonos-firmware-archive", release["id"], path.name, path)
            else:
                upload_asset("BookCatKid/sonos-firmware-archive", release["id"], path.name, path)
            time.sleep(1.1)
        return tag, len(paths)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for tag, count in pool.map(upload, sorted(groups.items())):
            print(f"uploaded {count} raw assets to {tag}")

    errors: list[str] = []
    for tag, paths in sorted(groups.items()):
        assets = remote_assets("BookCatKid/sonos-firmware-archive", tag)
        for path in paths:
            item = expected[path.name]
            asset = assets.get(path.name)
            if asset is None:
                errors.append(f"{tag}/{path.name}: missing")
                continue
            digest = verified_remote_digest(asset)
            if asset["size"] != item["bytes"] or digest != item["sha256"]:
                errors.append(f"{tag}/{path.name}: size or digest mismatch")
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print(f"reconciled {sum(map(len, groups.values()))} raw assets across {len(groups)} releases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
