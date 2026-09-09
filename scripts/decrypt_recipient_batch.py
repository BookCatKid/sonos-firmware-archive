#!/usr/bin/env python3
"""Download, verify, decrypt, and immediately discard one recipient batch."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.extract import extract_components  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verified_existing(receipt_path: Path, output: Path, recipient: str) -> bool:
    if not receipt_path.is_file() or not output.is_dir():
        return False
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    for component in receipt.get("components", []):
        path = output / component["filename"]
        if component.get("recipient_id") != recipient or not path.is_file():
            return False
        if path.stat().st_size != component["bytes"] or _sha256(path) != component["sha256"]:
            return False
    return bool(receipt.get("components"))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipient", required=True)
    parser.add_argument("--private-key", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--raw-directory", type=Path, default=ROOT / "artifacts/raw")
    parser.add_argument("--receipt-directory", type=Path, default=ROOT / "data/raw")
    args = parser.parse_args()
    recipient = args.recipient.lower()

    catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
    ledger = json.loads((ROOT / "data/key-recovery-ledger.json").read_text(encoding="utf-8"))
    groups = {item["recipient_id"]: item for item in ledger["missing_keys"]}
    groups.update(ledger["recovered_keys"])
    group = groups.get(recipient)
    if group is None:
        raise ValueError(f"recipient is absent from the key ledger: {recipient}")
    coverage = group.get("packages", group.get("coverage_packages", []))
    ids = {item["package_id"] for item in coverage if item["artifact_status"] == "preserved"}
    packages = sorted((p for p in catalog["packages"] if p.get("id") in ids), key=lambda p: p["id"])
    args.staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.raw_directory.mkdir(parents=True, exist_ok=True)
    args.receipt_directory.mkdir(parents=True, exist_ok=True)

    completed = skipped = 0
    for package in packages:
        output = args.raw_directory / package["id"]
        receipt_path = args.receipt_directory / f"{package['id']}.json"
        if _verified_existing(receipt_path, output, recipient):
            skipped += 1
            print(f"verified existing output: {package['id']}", flush=True)
            continue
        source = args.staging / package["filename"]
        if source.exists():
            if source.stat().st_size == package["bytes"] and _sha256(source) == package["sha256"]:
                print(f"reusing verified staged source: {package['id']}", flush=True)
            else:
                source.unlink()
        if not source.exists():
            subprocess.run(
                [
                    "gh",
                    "release",
                    "download",
                    package["release_tag"],
                    "--pattern",
                    package["filename"],
                    "--dir",
                    str(args.staging),
                ],
                cwd=ROOT,
                check=True,
            )
        if source.stat().st_size != package["bytes"] or _sha256(source) != package["sha256"]:
            raise ValueError(f"source package hash mismatch: {package['id']}")
        output.mkdir(parents=True, exist_ok=True)
        components = extract_components(source, output, args.private_key)
        if not components or {item.get("recipient_id") for item in components} != {recipient}:
            raise ValueError(f"decrypted recipient mismatch: {package['id']}")
        receipt_path.write_text(
            json.dumps({"source": package["filename"], "components": components}, indent=2) + "\n",
            encoding="utf-8",
        )
        source.unlink()
        completed += 1
        print(f"decrypted and removed staged source: {package['id']}", flush=True)

    print(json.dumps({"packages": len(packages), "completed": completed, "skipped": skipped}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
