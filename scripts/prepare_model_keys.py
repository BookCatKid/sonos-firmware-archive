#!/usr/bin/env python3
"""Prepare whichever recovered model keys are available to an archive worker."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sonos_firmware.discovery import write_json
from sonos_firmware.keyring import prepare_keys, recipient_models, update_recovery_summary
from sonos_firmware.key_recovery import recover_catalog_key


def main():
    from sonos_firmware.model26 import recover, RECIPIENT_ID
    inputs = ROOT / "data/key-material/model26"
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--catalog', type=Path, default=ROOT / 'data/catalog.json')
    parser.add_argument('--ledger', type=Path, default=ROOT / 'data/key-recovery-ledger.json')
    parser.add_argument('--max-sources', type=int, default=8)
    args = parser.parse_args()
    if args.max_sources <= 0:
        parser.error('--max-sources must be positive')
    catalog = json.loads(args.catalog.read_text()) if args.catalog.exists() else {'packages': []}
    ledger = json.loads(args.ledger.read_text()) if args.ledger.exists() else {'recovered_keys': {}, 'missing_keys': []}
    recipients = recipient_models(ledger)
    origins = {}
    def recoverer(model, fingerprint):
        def run():
            output = args.directory / ('recipient-' + fingerprint + '.pem')
            if model == 26 and fingerprint == RECIPIENT_ID:
                recover(inputs / 'mdp3-model-key.bin', inputs / 'otp.bin', output)
                origins[fingerprint] = {'method': 'public-model26-mdp3', 'model': model, 'recipient_id': fingerprint}
            else:
                origins[fingerprint] = recover_catalog_key(ROOT, catalog, model, fingerprint, output,
                                        args.repository, max_sources=args.max_sources)
            return output
        return run
    result = prepare_keys(args.directory, recipients, os.environ,
                          {(model, fingerprint): recoverer(model, fingerprint)
                           for model, fingerprints in recipients.items() for fingerprint in fingerprints if model is not None})
    result['recovery_provenance'] = {fp: proof for fp, proof in origins.items() if fp in result['ready_recipients']}
    missing = {row['recipient_id']: row for row in ledger.get('missing_keys', [])}
    for fingerprint, ready in result['ready_recipients'].items():
        prior = missing.pop(fingerprint, {})
        group = ledger.setdefault('recovered_keys', {}).setdefault(fingerprint, {
            'coverage_packages': prior.get('packages', []),
            'key_family': origins.get(fingerprint, {}).get('method', 'configured-model-key')})
        group.setdefault('package_model', ready['models'][0] if ready['models'] else None)
        group['package_models'] = sorted(ready['models'])
        if fingerprint in origins:
            proofs = group.setdefault('recovery_provenance', [])
            if origins[fingerprint] not in proofs:
                proofs.append(origins[fingerprint])
    ledger['missing_keys'] = list(missing.values())
    update_recovery_summary(ledger)
    if args.ledger.exists() or result['ready_recipients']:
        write_json(args.ledger, ledger)
    write_json(args.report, result)
    print(f"prepared recipients={len(result['ready_recipients'])}; unavailable recipients={len(result['unavailable'])}")


if __name__ == '__main__':
    main()
