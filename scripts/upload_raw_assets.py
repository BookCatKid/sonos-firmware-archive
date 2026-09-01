#!/usr/bin/env python3
"""Upload locally extracted raw components beside their source OTA Releases."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import subprocess
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--directory", type=Path, default=ROOT / "artifacts/raw")
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
    expected = {
        item["filename"]: item
        for item in catalog["raw_images"]
        if item.get("release_tag") and item.get("release_asset")
    }
    groups: dict[str, list[Path]] = defaultdict(list)
    for path in sorted(args.directory.glob("*/*")):
        item = expected.get(path.name)
        if item:
            groups[item["release_tag"]].append(path)

    def upload(entry: tuple[str, list[Path]]) -> tuple[str, int]:
        tag, paths = entry
        subprocess.run(
            ["gh", "release", "upload", tag, *map(str, paths), "--clobber"],
            cwd=ROOT,
            check=True,
        )
        return tag, len(paths)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        for tag, count in pool.map(upload, sorted(groups.items())):
            print(f"uploaded {count} raw assets to {tag}")

    errors: list[str] = []
    for tag, paths in sorted(groups.items()):
        result = subprocess.run(
            ["gh", "release", "view", tag, "--json", "assets"],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        assets = {item["name"]: item for item in json.loads(result.stdout)["assets"]}
        for path in paths:
            item = expected[path.name]
            asset = assets.get(path.name)
            if asset is None:
                errors.append(f"{tag}/{path.name}: missing")
                continue
            digest = (asset.get("digest") or "").removeprefix("sha256:")
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
