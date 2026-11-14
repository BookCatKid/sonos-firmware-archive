"""Worker-only source tasks shared by the target adapters."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..discovery import write_json

ROOT = Path(__file__).resolve().parents[3]


def store_apple(root: Path, current_path: Path):
    current = json.loads(current_path.read_text())
    history_path = root / 'data/apps/apple-app-store-history.json'
    history = json.loads(history_path.read_text()) if history_path.exists() else {'schema_version': 1, 'snapshots': []}
    def identity(snapshot):
        return tuple((x['track_id'], x['version'], x['current_version_release_date'], x['file_size_bytes'])
                     for x in snapshot['listings'])
    if identity(current) not in {identity(s) for s in history['snapshots']}:
        history['snapshots'].append(current)
    write_json(history_path, history)
    write_json(root / 'data/apps/apple-app-store.json', current)


def extract_firmware(root, work, repository):
    work.mkdir(parents=True, exist_ok=True)
    keys = work / 'keys'
    receipts = work / 'receipts'
    def run(script, *args):
        subprocess.run([sys.executable, str(root / 'scripts' / script), *(str(a) for a in args)], cwd=root, check=True)
    try:
        run('prepare_model_keys.py', '--directory', keys, '--report', work / 'keys.json', '--repository', repository)
        run('refresh_key_recovery_ledger.py')
        run('decrypt_pending_firmware.py', '--key-directory', keys, '--staging', work / 'sources',
            '--raw-directory', work / 'raw', '--repo', repository, '--receipt-output', receipts, '--summary', work / 'extraction.json')
        new = sorted(receipts.glob('*.json'))
        if not new:
            return
        catalog = root / 'data/catalog.json'
        previous = catalog.read_bytes()
        try:
            run('import_raw_receipts.py', *new)
            run('upload_raw_assets.py', '--directory', work / 'raw', '--repo', repository)
        except Exception:
            # Preserve catalog changes made by earlier source publication steps.
            catalog.write_bytes(previous)
            raise
        destination = root / 'data/raw'
        destination.mkdir(parents=True, exist_ok=True)
        for receipt in new:
            shutil.copyfile(receipt, destination / receipt.name)
    finally:
        shutil.rmtree(keys, ignore_errors=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=ROOT)
    sub = parser.add_subparsers(dest='task', required=True)
    apple = sub.add_parser('store-apple-metadata')
    apple.add_argument('--current', type=Path, required=True)
    google = sub.add_parser('store-google-play-metadata')
    google.add_argument('--current', type=Path, required=True)
    extract = sub.add_parser('extract-firmware')
    extract.add_argument('--work-dir', type=Path, required=True)
    extract.add_argument('--repository', required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('acquisition tasks run in the CI worker; use the platform plan locally')
    if args.task == 'store-apple-metadata':
        store_apple(args.root, args.current)
    elif args.task == 'store-google-play-metadata':
        write_json(args.root / 'data/apps/google-play-listings.json', json.loads(args.current.read_text()))
    elif args.task == 'extract-firmware':
        extract_firmware(args.root, args.work_dir, args.repository)


if __name__ == '__main__':
    main()
