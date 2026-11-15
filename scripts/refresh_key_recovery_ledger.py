#!/usr/bin/env python3
"""Rebuild UPD recipient coverage from the catalog and section manifests."""

from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sonos_firmware.storage import atomic_json
from sonos_firmware.keyring import update_recovery_summary


def package_summary(package: dict) -> dict:
    return {
        "artifact_status": package["artifact_status"],
        "package_id": package["id"],
        "package_model": package["package_model"],
        "version": package["version"],
    }


def main() -> int:
    ledger_path = ROOT / "data/key-recovery-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8")) if ledger_path.exists() else {"recovered_keys": {}, "key_origin": {}}
    catalog_path = ROOT / 'data/catalog.json'
    catalog = json.loads(catalog_path.read_text(encoding="utf-8")) if catalog_path.exists() else {'packages': []}
    recovered = ledger["recovered_keys"]
    encrypted: dict[str, list[dict]] = defaultdict(list)
    plaintext: list[dict] = []
    missing_manifests = 0
    encrypted_ids = set()
    unidentified = []

    for package in catalog["packages"]:
        if package.get("artifact_status") != "preserved" or package.get("artifact_type") != "upd":
            continue
        path = ROOT / "data/upd" / f"{package['id']}.json"
        if not path.is_file():
            missing_manifests += 1
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        sections = manifest.get("sections", [])
        recipients = {
            section["recipient_id"]
            for section in sections
            if section.get("encrypted") and section.get("recipient_id")
        }
        item = package_summary(package)
        if any(section.get("encrypted") for section in sections):
            encrypted_ids.add(package["id"])
        if any(section.get("encrypted") and not section.get("recipient_id") for section in sections):
            unidentified.append(item)
        if recipients:
            for recipient in recipients:
                encrypted[recipient].append(item)
        elif sections and all(not section.get("encrypted") for section in sections):
            plaintext.append(item)

    def package_key(item: dict) -> tuple:
        return (item["version"], item["package_model"] is None, item["package_model"] or 0, item["package_id"])

    for recipient, metadata in recovered.items():
        metadata["coverage_packages"] = sorted(encrypted.pop(recipient, []), key=package_key)
        metadata.pop("packages", None)
    ledger["missing_keys"] = [
        {"packages": sorted(packages, key=package_key), "recipient_id": recipient}
        for recipient, packages in sorted(encrypted.items())
    ]
    ledger["plaintext_packages"] = sorted(plaintext, key=package_key)
    ledger["unidentified_encrypted_packages"] = sorted(unidentified, key=package_key)
    ledger["summary"] = {
        "encrypted_upd_packages": len(encrypted_ids),
        "unidentified_encrypted_packages": len(unidentified),
        "missing_upd_section_manifests": missing_manifests,
        "plaintext_upd_packages": len(plaintext),
        "unique_recipients": len(recovered) + len(ledger["missing_keys"]),
    }
    update_recovery_summary(ledger)
    atomic_json(ledger_path, ledger)
    print(json.dumps(ledger["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
