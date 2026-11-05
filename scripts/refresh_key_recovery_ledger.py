#!/usr/bin/env python3
"""Rebuild UPD recipient coverage from the catalog and section manifests."""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
EXTRACTABLE_SECTION_TYPES = {3, 4, 6, 13}


def package_summary(package: dict) -> dict:
    return {
        "artifact_status": package["artifact_status"],
        "package_id": package["id"],
        "package_model": package["package_model"],
        "version": package["version"],
    }


def main() -> int:
    ledger_path = ROOT / "data/key-recovery-ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    catalog = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
    recovered = ledger["recovered_keys"]
    encrypted: dict[str, list[dict]] = defaultdict(list)
    plaintext: list[dict] = []
    missing_manifests = 0

    for package in catalog["packages"]:
        if package.get("artifact_status") != "preserved" or package.get("artifact_type") != "upd":
            continue
        path = ROOT / "data/upd" / f"{package['id']}.json"
        if not path.is_file():
            missing_manifests += 1
            continue
        manifest = json.loads(path.read_text(encoding="utf-8"))
        sections = [
            section
            for section in manifest.get("sections", [])
            if section.get("section_type") in EXTRACTABLE_SECTION_TYPES
        ]
        recipients = {
            section["recipient_id"]
            for section in sections
            if section.get("encrypted") and section.get("recipient_id")
        }
        item = package_summary(package)
        if recipients:
            for recipient in recipients:
                encrypted[recipient].append(item)
        elif sections:
            plaintext.append(item)

    def package_key(item: dict) -> tuple:
        return (item["version"], item["package_model"], item["package_id"])

    for recipient, metadata in recovered.items():
        metadata["coverage_packages"] = sorted(encrypted.pop(recipient, []), key=package_key)
    ledger["missing_keys"] = [
        {"packages": sorted(packages, key=package_key), "recipient_id": recipient}
        for recipient, packages in sorted(encrypted.items())
    ]
    ledger["plaintext_packages"] = sorted(plaintext, key=package_key)
    ledger["summary"] = {
        "encrypted_upd_packages": sum(
            len(item["coverage_packages"]) for item in recovered.values()
        ) + sum(len(item["packages"]) for item in ledger["missing_keys"]),
        "missing_upd_section_manifests": missing_manifests,
        "plaintext_upd_packages": len(plaintext),
        "unique_recipients": len(recovered) + len(ledger["missing_keys"]),
    }
    for field in [name for name in ledger["key_origin"] if name.startswith("local_status_")]:
        del ledger["key_origin"][field]
    recovered_models = sorted(metadata["package_model"] for metadata in recovered.values())
    legacy_models = sorted(set(recovered_models) & {1, 8, 9, 12, 16, 17})
    mechanisms = []
    if legacy_models:
        mechanisms.append(
            "Models " + ", ".join(map(str, legacy_models))
            + " are reproducible from encrypted wrappers in plaintext 34.16-37101 updaters"
        )
    if 26 in recovered_models:
        mechanisms.append(
            "Model 26 is reproducible from the publicly documented MDP3 model-key "
            "field and matching HITB 2023 OTP using scripts/recover_model26_key.py"
        )
    ledger["key_origin"]["local_status_current"] = (
        f"{len(recovered)} recovered recipient keys cover package models "
        f"{', '.join(map(str, recovered_models))}. "
        + ". ".join(mechanisms)
        + ". Private-key PEM outputs remain in the local gitignored recovery-work "
        "vault rather than in Git. "
        + f"{len(ledger['missing_keys'])} recipient groups remain missing."
    )
    ledger_path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(ledger["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
