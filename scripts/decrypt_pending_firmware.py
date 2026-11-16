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
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.catalog import EXTRACTABLE_SECTION_TYPES  # noqa: E402
from sonos_firmware.extract import extract_components, extract_available_components  # noqa: E402
from sonos_firmware.storage import atomic_json, file_sha256 as _sha256  # noqa: E402
from sonos_firmware.retry import checkpoint, select  # noqa: E402
from sonos_firmware.keyring import recipient_key_path  # noqa: E402
from sonos_firmware.releases import download_verified


def section_keys(args, package, model, recipient):
    model = package.get('package_model', model)
    manifest = args.upd_manifest_directory / (package['id'] + '.json')
    rows = json.loads(manifest.read_text()).get('sections', []) if manifest.exists() else []
    fingerprints = {row['recipient_id'] for row in rows if row.get('recipient_id') and row.get('encrypted')}
    if recipient:
        fingerprints.add(recipient)
    return {fp: path for fp in fingerprints
            if (path := recipient_key_path(args.key_directory, model, fp)).is_file()}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
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
    parser.add_argument('--max-packages', type=int, default=24)
    parser.add_argument('--state', type=Path)
    args = parser.parse_args()
    if args.max_packages <= 0:
        parser.error('--max-packages must be positive')

    catalog = json.loads(args.catalog.read_text(encoding="utf-8")) if args.catalog.exists() else {'packages': [], 'raw_images': []}
    ledger = json.loads(args.ledger.read_text(encoding="utf-8")) if args.ledger.exists() else {'recovered_keys': {}}
    recovered = ledger["recovered_keys"]
    inventory_skipped = []

    def still_needs(package, model=None, recipient=None):
        receipt = args.receipt_directory / (package['id'] + '.json')
        if not receipt.exists():
            return True
        manifest = args.upd_manifest_directory / (package['id'] + '.json')
        if not manifest.exists():
            return package.get('extraction_policy') == 'all-sections'
        keys = section_keys(args, package, model, recipient)
        key = recipient_key_path(args.key_directory, model, recipient) if model is not None else None
        desired = [s for s in json.loads(manifest.read_text()).get('sections', [])
                   if (package.get('extraction_policy') == 'all-sections' or s.get('section_type') in EXTRACTABLE_SECTION_TYPES)
                   and (not s.get('encrypted') or s.get('recipient_id') in keys or
                        not s.get('recipient_id') and key is not None and key.is_file())]
        components = json.loads(receipt.read_text()).get('components', [])
        covered = {row.get('section_index') for row in components}
        return any(row['index'] not in covered for row in desired)


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
            model = package.get('package_model', group.get('package_model'))
            if still_needs(package, model, recipient) and package["id"] not in seen:
                chosen = recipient
                manifest = args.upd_manifest_directory / (package['id'] + '.json')
                if package.get('extraction_policy') != 'all-sections' and manifest.exists():
                    keys = section_keys(args, package, model, recipient)
                    receipt_path = args.receipt_directory / (package['id'] + '.json')
                    receipt = json.loads(receipt_path.read_text()) if receipt_path.exists() else {}
                    covered = {row.get('section_index') for row in receipt.get('components', [])}
                    wanted = sorted({row['recipient_id'] for row in json.loads(manifest.read_text()).get('sections', [])
                                     if row.get('section_type') in EXTRACTABLE_SECTION_TYPES and row.get('encrypted')
                                     and row.get('recipient_id') in keys and row['index'] not in covered})
                    chosen = wanted[0] if wanted else recipient
                seen.add(package["id"])
                pending.append({"package": package, "model": model,
                                "recipient": chosen})

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
        if not still_needs(package):
            continue
        manifest_path = args.upd_manifest_directory / f"{package['id']}.json"
        if not manifest_path.is_file():
            continue
        inventory = json.loads(manifest_path.read_text(encoding="utf-8"))
        if inventory.get('inventory_error'):
            inventory_skipped.append({'package_id': package['id'], 'reason': inventory['inventory_error']})
        sections = inventory.get("sections", [])
        if any((package.get("extraction_policy") == "all-sections" or s.get("section_type") in EXTRACTABLE_SECTION_TYPES) and not s.get("encrypted")
               for s in sections):
            seen.add(package["id"])
            pending.append({"package": package, "model": None, "recipient": None})
        elif any(s.get('encrypted') for s in sections):
            inventory_skipped.append({'package_id': package['id'], 'reason': 'no matching recipient key is available'})

    if not pending:
        print("no preserved packages are pending decryption")
        if args.summary:
            atomic_json(args.summary, {"pending": 0, "decrypted": [], "extracted_plaintext": [], "skipped": inventory_skipped})
        return 0

    receipt_output = args.receipt_output or args.receipt_directory
    args.staging.mkdir(mode=0o700, parents=True, exist_ok=True)
    args.raw_directory.mkdir(parents=True, exist_ok=True)
    receipt_output.mkdir(parents=True, exist_ok=True)
    state_path = args.state or args.catalog.parent / 'discovery/firmware-extraction.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'schema_version': 1, 'repositories': {}}
    observations = state['repositories'].setdefault(args.repo, {})
    def identity(entry):
        return entry['package']['id']
    def revision(entry):
        key = recipient_key_path(args.key_directory, entry['model'], entry['recipient'])
        return {'sha256': entry['package']['sha256'], 'recipient': entry['recipient'], 'key_available': key.is_file(),
                'ready_recipients': sorted(section_keys(args, entry['package'], entry['model'], entry['recipient'])),
                'policy': entry['package'].get('extraction_policy', 'legacy-components')}
    eligible = {key: row for key, row in observations.items()}
    for entry in pending:
        if observations.get(identity(entry), {}).get('revision') != revision(entry):
            eligible.pop(identity(entry), None)
    queue = select(pending, eligible, key=identity, limit=args.max_packages, state=state, queue=args.repo)

    decrypted: list[str] = []
    extracted_plaintext: list[str] = []
    skipped: list[dict] = inventory_skipped
    failures = []
    for entry in queue:
        package, model, recipient = entry["package"], entry["model"], entry["recipient"]
        prior = observations.get(package['id'], {})
        try:
            kind, blocked = extract_one(entry, args, receipt_output)
            skipped.extend(blocked)
            if kind is None:
                observations[package['id']] = {**checkpoint(prior), 'status': 'blocked', 'revision': revision(entry)}
            else:
                (extracted_plaintext if kind == 'plaintext' else decrypted).append(package['id'])
                observations[package['id']] = {**checkpoint(prior, success=True, success_hours=1), 'status': 'extracted', 'revision': revision(entry)}
        except Exception as error:
            failures.append({'package_id': package['id'], 'error': str(error)})
            observations[package['id']] = {**checkpoint(prior), 'status': 'failed', 'revision': revision(entry), 'error': str(error)}
        atomic_json(state_path, state)

    summary = {"pending": len(pending), 'attempted': len(queue), 'deferred': len(pending) - len(queue),
               "decrypted": decrypted, "extracted_plaintext": extracted_plaintext, "skipped": skipped,
               'errors': failures, 'status': 'partial' if failures or skipped or len(queue) < len(pending) else 'ok'}
    print(json.dumps(summary, indent=2))
    if args.summary:
        atomic_json(args.summary, summary)
    return int(bool(failures))


def extract_one(entry, args, receipt_output):
    package, model, recipient = entry['package'], entry['model'], entry['recipient']
    if not package['id'] or Path(package['id']).name != package['id'] or package['id'] in {'.', '..'}:
        raise ValueError('package ID must be a basename')
    skipped = []
    key_path = recipient_key_path(args.key_directory, model, recipient) if model is not None and recipient is not None else None
    if package.get('extraction_policy') == 'all-sections':
        available_keys = section_keys(args, package, model, recipient)
        if available_keys:
            key_path = next(iter(available_keys.values()))
    if key_path is not None and not key_path.is_file():
        manifest_path = args.upd_manifest_directory / (package['id'] + '.json')
        sections = json.loads(manifest_path.read_text()).get('sections', []) if manifest_path.exists() else []
        if any(not row.get('encrypted') and (package.get('extraction_policy') == 'all-sections' or
               row.get('section_type') in EXTRACTABLE_SECTION_TYPES) for row in sections):
            key_path = None
        else:
            print(f"skipping {package['id']}: no key file for model {model}", flush=True)
            return None, [{"package_id": package["id"], "reason": f"no key file for model {model}"}]
    source = args.staging / (package["id"] + ".upd")
    if source.exists() and not (source.stat().st_size == package["bytes"] and _sha256(source) == package["sha256"]):
        source.unlink()
    if not source.exists():
        download_verified({**package, 'release_asset': package.get('release_asset', package['filename'])}, source, args.repo)
    if source.stat().st_size != package["bytes"] or _sha256(source) != package["sha256"]:
        raise ValueError(f"source package hash mismatch: {package['id']}")
    output = args.raw_directory / package["id"]
    output.mkdir(parents=True, exist_ok=True)
    if package.get('extraction_policy') == 'all-sections':
        keys = section_keys(args, package, model, recipient)
        chosen = keys if len(keys) > 1 else next(iter(keys.values()), key_path)
        available = extract_available_components(source, output, chosen)
        components = available['components']
        skipped.extend({'package_id': package['id'], **row} for row in available['skipped'])
    elif key_path is None:
        components = extract_components(source, output, None, skip_encrypted=True)
        if not components:
            raise ValueError(f"no plaintext components extracted: {package['id']}")
        if any(c.get("source_encrypted") for c in components):
            raise ValueError(f"keyless extraction produced decrypted content: {package['id']}")
    else:
        keys = section_keys(args, package, model, recipient)
        manifest = args.upd_manifest_directory / (package['id'] + '.json')
        sections = json.loads(manifest.read_text()).get('sections', []) if manifest.exists() else []
        fingerprints = {row['recipient_id'] for row in sections if row.get('encrypted') and row.get('recipient_id')}
        if len(fingerprints) > 1:
            components = extract_components(source, output, keys, skip_encrypted=True)
            expected = set(keys)
        else:
            components = extract_components(source, output, key_path)
            expected = {recipient}
        encrypted_recipients = {c.get("recipient_id") for c in components} - {None}
        if not components or not encrypted_recipients <= expected:
            raise ValueError(f"decrypted recipient mismatch: {package['id']}")
    if not components:
        return None, skipped
    prior_path = args.receipt_directory / (package['id'] + '.json')
    prior_receipt = json.loads(prior_path.read_text()) if prior_path.exists() else {}
    if prior_receipt.get('source_sha256', package['sha256']) != package['sha256']:
        raise ValueError('existing extraction receipt belongs to another source capture')
    prior_components = prior_receipt.get('components', [])
    if any(row.get('package_id') != package['id'] for row in prior_components):
        raise ValueError('existing extraction receipt contains another package')
    filenames = {row['filename'] for row in components}
    def component_key(row):
        return ('section', row['section_index']) if row.get('section_index') is not None else ('file', row['filename'])
    merged = {component_key(row): row for row in prior_components
              if row.get('section_index') is not None or row['filename'] not in filenames}
    merged.update({component_key(row): row for row in components})
    atomic_json(receipt_output / f"{package['id']}.json", {**prior_receipt, "source": package["filename"],
                'source_sha256': package['sha256'], "components": list(merged.values())})
    source.unlink()
    kind = 'plaintext' if key_path is None else 'decrypted'
    print(f"extracted {package['id']} ({len(components)} components)", flush=True)
    return kind, skipped


if __name__ == "__main__":
    raise SystemExit(main())
