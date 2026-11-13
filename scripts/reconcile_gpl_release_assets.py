#!/usr/bin/env python3
"""Recover metadata for already-published GPL releases after interrupted CI jobs.

Never classify old Wayback assets as direct Sonos acquisitions automatically.
Use --version only when direct-source publication is independently evidenced.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path

from sonos_firmware.gpl_archive import atomic_json, load_json, timestamp, valid_version

ROOT = Path(__file__).resolve().parents[1]


def reconcile(catalog: dict, receipts: dict, version: str, assets: list[dict], *, run_url: str) -> tuple[int, int]:
    """Reconstruct proven publication receipts from release-side size and SHA-256.

    Preserve existing records unchanged; never infer a source URL from an
    arbitrary release asset or overwrite a conflicting previously recorded hash.
    """
    record = catalog["releases"].get(valid_version(version))
    if not record or record["state"] != "verified":
        raise ValueError(f"{version}: no directly verified index")
    expected = {link["filename"]: link for link in record["links"]}
    expected["gpl.html"] = {"filename": "gpl.html", "kind": "index", "url": record["url"],
                            "fetch_url": record["url"], "origin": "sonos"}
    dest = receipts.setdefault("artifacts", {}).setdefault(version, {})
    imported = 0
    conflicts = 0
    for asset in assets:
        release_name = asset["name"]
        sha256 = (asset.get("digest") or "").removeprefix("sha256:")
        if not re.fullmatch(r"[0-9a-f]{64}", sha256) or not isinstance(asset.get("size"), int) or asset["size"] <= 0:
            continue
        # The direct pipeline preserves old Wayback assets untouched by
        # renaming conflicting new files to direct-{sha12}-{original-name}.
        # Decode these only when the prefix agrees with the published digest.
        match = re.fullmatch(r"direct-([0-9a-f]{12})-(.+)", release_name)
        if match:
            if sha256[:12] != match.group(1):
                continue
            filename = match.group(2)
        else:
            filename = release_name
        original = expected.get(filename)
        if not original:
            continue
        # An old Wayback GPL index may occupy gpl.html. Never mistake it for
        # the directly fetched index just because it has the expected name.
        if filename == "gpl.html" and (record["sha256"] != sha256 or record["bytes"] != asset["size"]):
            continue
        prior = dest.get(filename)
        if prior:
            if prior.get("sha256") != sha256 or prior.get("bytes") != asset["size"]:
                # The original, unprefixed Wayback file is not a conflict with
                # an already-recovered direct copy in the same release.
                if not match and prior.get("release_asset", filename) != release_name:
                    continue
                conflicts += 1
            elif prior.get("published") is not True:
                prior.update(published=True, state="published", release_tag=f"gpl-{version}",
                             release_asset=release_name, release_url=asset["browser_download_url"],
                             original_name_conflict=bool(match))
            continue
        dest[filename] = {"state": "published", "published": True, "filename": filename,
                          "kind": original["kind"], "source_url": original["url"],
                          "fetch_url": original.get("fetch_url"), "origin": original["origin"],
                          "bytes": asset["size"], "sha256": sha256,
                          "release_tag": f"gpl-{version}", "release_asset": release_name,
                          "original_name_conflict": bool(match),
                          "release_url": asset["browser_download_url"],
                          "verification": "published-binary-digest-confirmed",
                          "receipt_recovered_from": run_url,
                          "source_tar_integrity_reevaluated": False}
        imported += 1
    return imported, conflicts


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, default=ROOT / "data/gpl/live")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--run-url", required=True, help="Successful upload workflow run evidencing original direct download")
    args = parser.parse_args()
    catalog = load_json(args.output / "catalog.json", {"releases": {}})
    receipts = load_json(args.output / "artifacts.json", {"schema_version": 1, "artifacts": {}})
    release = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{args.repo}/releases/tags/gpl-{valid_version(args.version)}"], text=True))
    imported, conflicts = reconcile(catalog, receipts, args.version, release["assets"], run_url=args.run_url)
    if conflicts:
        raise RuntimeError(f"refusing to write {conflicts} mismatched publication receipts")
    receipts["updated"] = timestamp()
    atomic_json(args.output / "artifacts.json", receipts)
    print(f"recovered={imported} conflicts={conflicts} total={len(receipts['artifacts'][args.version])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
