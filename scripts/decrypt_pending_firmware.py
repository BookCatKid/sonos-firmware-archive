#!/usr/bin/env python3
"""Extract components from preserved packages, decrypting where keys exist.

Finds two kinds of preserved catalog packages that still lack a checked-in
receipt in `data/raw/`:

1. packages covered by `recovered_keys` in the key ledger - downloaded,
   verified against the catalog and extracted with the matching model key;
2. packages with no ledger coverage whose UPD section manifest lists
   unencrypted extractable sections - those need no key at all, and the
   catalog validator holds their raw components to the same standard.

Key material is read only from PEM files inside --key-directory (one
`model<N>.pem` per model) and is never logged.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.catalog import EXTRACTABLE_SECTION_TYPES  # noqa: E402
from sonos_firmware.extract import extract_components  # noqa: E402


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--key-directory", type=Path, required=True)
    parser.add_argument("--staging", type=Path, required=True)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    parser.add_argument("--ledger", type=Path, default=ROOT / "data/key-recovery-ledger.json")
    parser.add_argument("--raw-directory", type=Path, default=ROOT / "artifacts/raw")
    parser.add_argument("--receipt-directory", type=Path, default=ROOT / "data/raw")
    parser.add_argument("--upd-manifest-directory", type=Path, default=ROOT / "data/upd")
    parser.add_argument("--receipt-output", type=Path,
                        help="where new receipts go (default: --receipt-directory); "
                             "use a staging dir so receipts only reach data/raw after "
                             "their components are published")
    parser.add_argument("--summary", type=Path)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    ledger = json.loads(args.ledger.read_text(encoding="utf-8"))
    recovered = ledger["recovered_keys"]

    # recipient -> preserved package ids, from the ledger's own coverage rows
    pending: list[dict] = []
    seen: set[str] = set()
    packages = {p["id"]: p for p in catalog["packages"]}
    for recipient, group in sorted(recovered.items()):
        coverage = group.get("packages", group.get("coverage_packages", []))
        for item in coverage:
            if item.get("artifact_status") != "preserved":
                continue
            package = packages.get(item["package_id"])
            if package is None:
                continue
            receipt = args.receipt_directory / f"{package['id']}.json"
            if not receipt.is_file() and package["id"] not in seen:
                seen.add(package["id"])
                pending.append({"package": package, "model": group.get("package_model"),
                                "recipient": recipient})

    # Preserved UPDs outside the ledger can still owe raw components: any
    # unencrypted extractable section is held to the same catalog standard
    # and needs no key. Mixed packages keep their encrypted sections for a
    # future key; fully plaintext packages are extracted in full.
    covered_ids = set()
    for group in recovered.values():
        for item in group.get("packages", group.get("coverage_packages", [])):
            covered_ids.add(item.get("package_id"))
    for package in catalog["packages"]:
        if package["id"] in seen or package["id"] in covered_ids:
            continue
        if package.get("artifact_status") != "preserved" or package.get("artifact_type") != "upd":
            continue
        if (args.receipt_directory / f"{package['id']}.json").is_file():
            continue
        manifest_path = args.upd_manifest_directory / f"{package['id']}.json"
        if not manifest_path.is_file():
            continue
        sections = json.loads(manifest_path.read_text(encoding="utf-8")).get("sections", [])
        if any(s.get("section_type") in EXTRACTABLE_SECTION_TYPES and not s.get("encrypted")
               for s in sections):
            seen.add(package["id"])
            pending.append({"package": package, "model": None, "recipient": None})

    if not pending:
        print("no preserved packages are pending decryption")
        if args.summary:
            args.summary.write_text(json.dumps({"pending": 0, "decrypted": [], "extracted_plaintext": [], "skipped": []}) + "\n", encoding="utf-8")
        return 0

    receipt_output = args.receipt_output or args.receipt_directory
    args.staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.raw_directory.mkdir(parents=True, exist_ok=True)
    receipt_output.mkdir(parents=True, exist_ok=True)

    decrypted: list[str] = []
    extracted_plaintext: list[str] = []
    skipped: list[dict] = []
    for entry in pending:
        package, model, recipient = entry["package"], entry["model"], entry["recipient"]
        key_path = args.key_directory / f"model{model}.pem" if model is not None else None
        if key_path is not None and not key_path.is_file():
            skipped.append({"package_id": package["id"], "reason": f"no key file for model {model}"})
            print(f"skipping {package['id']}: no key file for model {model}", flush=True)
            continue
        source = args.staging / package["filename"]
        if source.exists() and not (source.stat().st_size == package["bytes"] and _sha256(source) == package["sha256"]):
            source.unlink()
        if not source.exists():
            subprocess.run(
                ["gh", "release", "download", package["release_tag"], "--pattern",
                 package["filename"], "--dir", str(args.staging)],
                cwd=ROOT, check=True)
        if source.stat().st_size != package["bytes"] or _sha256(source) != package["sha256"]:
            raise ValueError(f"source package hash mismatch: {package['id']}")
        output = args.raw_directory / package["id"]
        output.mkdir(parents=True, exist_ok=True)
        if key_path is None:
            components = extract_components(source, output, None, skip_encrypted=True)
            if not components:
                raise ValueError(f"no plaintext components extracted: {package['id']}")
            if any(c.get("source_encrypted") for c in components):
                raise ValueError(f"keyless extraction produced decrypted content: {package['id']}")
        else:
            components = extract_components(source, output, key_path)
            encrypted_recipients = {c.get("recipient_id") for c in components} - {None}
            if not components or encrypted_recipients != {recipient}:
                raise ValueError(f"decrypted recipient mismatch: {package['id']}")
        (receipt_output / f"{package['id']}.json").write_text(
            json.dumps({"source": package["filename"], "components": components}, indent=2) + "\n",
            encoding="utf-8")
        source.unlink()
        if key_path is None:
            extracted_plaintext.append(package["id"])
            print(f"extracted plaintext {package['id']} ({len(components)} components)", flush=True)
        else:
            decrypted.append(package["id"])
            print(f"decrypted {package['id']} ({len(components)} components)", flush=True)

    summary = {"pending": len(pending), "decrypted": decrypted,
               "extracted_plaintext": extracted_plaintext, "skipped": skipped}
    print(json.dumps(summary, indent=2))
    if args.summary:
        args.summary.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
