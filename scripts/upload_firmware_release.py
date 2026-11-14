#!/usr/bin/env python3
"""Resumably upload a firmware discovery receipt to an existing Release."""

from __future__ import annotations

import argparse
import hashlib
import mimetypes
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

from sonos_firmware.firmware_archive import upload_receipt  # noqa: E402


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

    upload_receipt(args.receipt, args.directory, args.tag, args.repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
