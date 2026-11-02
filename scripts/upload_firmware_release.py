#!/usr/bin/env python3
"""Resumably upload a firmware discovery receipt to an existing Release."""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from archive_web_apps import public_release, remote_assets, upload_asset  # noqa: E402


def verified_remote_digest(asset: dict) -> str:
    digest = (asset.get("digest") or "").removeprefix("sha256:")
    if digest:
        return digest
    hasher = hashlib.sha256()
    request = urllib.request.Request(
        asset["browser_download_url"], headers={"User-Agent": "sonos-firmware-archive/1"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        while block := response.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def upload_streaming(repository: str, release_id: int, name: str, path: Path) -> None:
    endpoint = (
        f"repos/{repository}/releases/{release_id}/assets?"
        + urllib.parse.urlencode({"name": name})
    )
    subprocess.run([
        "gh", "api", "--method", "POST",
        "-H", f"Content-Type: {mimetypes.guess_type(name)[0] or 'application/octet-stream'}",
        "--input", str(path), f"https://uploads.github.com/{endpoint}",
    ], check=True, stdout=subprocess.DEVNULL)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    args = parser.parse_args()

    release = public_release(args.repo, args.tag)
    records = [
        item for item in json.loads(args.receipt.read_text(encoding="utf-8"))["artifacts"]
        if item.get("downloaded")
    ]
    observed = remote_assets(args.repo, args.tag)
    uploaded = skipped = 0
    for record in records:
        name = record["local_file"]
        path = args.directory / name
        if path.stat().st_size != record["bytes"]:
            raise RuntimeError(f"local size mismatch: {path}")
        remote = observed.get(name)
        if remote is not None:
            try:
                digest = verified_remote_digest(remote) if remote["size"] == record["bytes"] else ""
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                subprocess.run([
                    "gh", "api", "--method", "DELETE",
                    f"repos/{args.repo}/releases/assets/{remote['id']}",
                ], check=True)
                print(f"removed incomplete remote record: {name}", flush=True)
                remote = None
                digest = ""
        if remote is not None:
            if remote["size"] != record["bytes"] or digest != record["sha256"]:
                raise RuntimeError(f"remote mismatch: {args.tag}/{name}")
            skipped += 1
            continue
        if path.stat().st_size >= 32 * 1024 * 1024:
            upload_streaming(args.repo, release["id"], name, path)
        else:
            upload_asset(args.repo, release["id"], name, path)
        uploaded += 1
        print(f"uploaded {uploaded}: {name}", flush=True)
        time.sleep(1.1)

    failures = []
    for attempt in range(10):
        observed = remote_assets(args.repo, args.tag)
        failures = []
        for record in records:
            remote = observed.get(record["local_file"])
            digest = verified_remote_digest(remote) if remote and remote["size"] == record["bytes"] else ""
            if remote is None or remote["size"] != record["bytes"] or digest != record["sha256"]:
                failures.append(record["local_file"])
        if not failures:
            break
        time.sleep(2)
    if failures:
        raise RuntimeError(f"GitHub verification failed: {failures[0]}")
    print(f"verified={len(records)} uploaded={uploaded} skipped={skipped}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
