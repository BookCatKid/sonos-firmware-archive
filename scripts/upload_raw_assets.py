#!/usr/bin/env python3
"""Upload locally extracted raw components beside their source OTA Releases."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

from sonos_firmware import releases
from sonos_firmware.storage import atomic_json


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--repo', default='BookCatKid/sonos-firmware-archive')
    parser.add_argument('--catalog', type=Path, default=ROOT / 'data/catalog.json')
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
    if args.workers <= 0:
        parser.error('--workers must be positive')

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    expected = {
        (item.get('package_id'), item["filename"]): item
        for item in catalog["raw_images"]
        if item.get("release_tag") and item.get("release_asset")
    }
    requested = set(args.package_id)
    missing = requested - {key[0] for key in expected}
    if missing:
        raise ValueError('no cataloged raw assets for requested packages: ' + ', '.join(sorted(missing)))
    groups = defaultdict(list)
    for (package_id, filename), item in expected.items():
        if not package_id or package_id in {'.', '..'} or Path(package_id).name != package_id or Path(filename).name != filename:
            raise ValueError('raw package and filename must be basenames')
        name = item['release_asset']
        if not name or Path(name).name != name or name in {'.', '..'}:
            raise ValueError('raw release asset must be a basename')
        path = args.directory / package_id / filename
        if not path.resolve().is_relative_to(args.directory.resolve()):
            raise ValueError('raw capture escapes the worker directory')
        if (requested and package_id in requested) or (not requested and path.is_file()):
            groups[item['release_tag']].append((path, item))

    preflight: list[dict] = []
    existing_targets: list[str] = []
    for tag, paths in sorted(groups.items()):
        assets = releases.remote_assets(args.repo, tag)
        names = [item['release_asset'] for path, item in paths]
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
        atomic_json(args.preflight_output, {
                    "audited_at": datetime.now(timezone.utc).isoformat(),
                    "repository": args.repo,
                    "releases": preflight,
                })
    if args.require_absent and existing_targets:
        for target in existing_targets:
            print(f"error: intended asset already exists: {target}")
        return 1

    def upload(entry):
        tag, paths = entry
        releases.publish_collection(args.repo, tag, paths)
        return tag, len(paths)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for tag, count in pool.map(upload, sorted(groups.items())):
            print(f"verified {count} raw assets in {tag}")

    print(f"reconciled {sum(map(len, groups.values()))} raw assets across {len(groups)} releases")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
