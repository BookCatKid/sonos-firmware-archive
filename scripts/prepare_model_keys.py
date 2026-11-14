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
from sonos_firmware.keyring import prepare_keys


def main():
    from archive_connectx import recover_key
    from recover_model26_key import recover, INPUTS
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--repository', required=True)
    args = parser.parse_args()
    catalog = json.loads((ROOT / 'data/catalog.json').read_text())
    ledger = json.loads((ROOT / 'data/key-recovery-ledger.json').read_text())
    recipients = {r['package_model']: fingerprint for fingerprint, r in ledger['recovered_keys'].items()}
    def model26():
        output = args.directory / 'model26.pem'
        recover(INPUTS / 'mdp3-model-key.bin', INPUTS / 'otp.bin', output)
        return output
    result = prepare_keys(args.directory, recipients, os.environ,
                          {17: lambda: recover_key(catalog, args.directory, args.repository), 26: model26})
    write_json(args.report, result)
    print(f"prepared keys={len(result['ready'])}; unavailable models={len(result['unavailable'])}")


if __name__ == '__main__':
    main()
