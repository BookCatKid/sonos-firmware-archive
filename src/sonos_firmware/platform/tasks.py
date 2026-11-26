"""Worker-only source tasks shared by the target adapters."""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from ..discovery import write_json
from ..inventory_archive import inventory_preserved
from ..binary_discovery import discover_preserved

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
    report_path = work / 'extraction.json'
    errors = []
    def run(script, *args):
        subprocess.run([sys.executable, str(root / 'scripts' / script), *(str(a) for a in args)], cwd=root, check=True)
    try:
        for script, arguments in [
            ('recovery/refresh_key_recovery_ledger.py', ()),
            ('recovery/prepare_model_keys.py', ('--directory', keys, '--report', work / 'keys.json', '--repository', repository)),
            ('firmware/decrypt_pending_firmware.py', ('--key-directory', keys, '--staging', work / 'sources',
             '--raw-directory', work / 'raw', '--repo', repository, '--receipt-output', receipts,
             '--summary', report_path))]:
            try:
                run(script, *arguments)
            except (OSError, subprocess.CalledProcessError) as error:
                errors.append({'step': script, 'error': str(error)})
        new = sorted(receipts.glob('*.json'))
        catalog = root / 'data/catalog.json'
        destination = root / 'data/raw'
        published = []
        for receipt in new:
            previous = catalog.read_bytes()
            staged_catalog = work / 'raw-catalog.json'
            staged_catalog.write_bytes(previous)
            promoted = False
            try:
                document = json.loads(receipt.read_text())
                if any(component.get('package_id') != receipt.stem for component in document['components']):
                    raise ValueError('extraction receipt contains another package')
                run('firmware/import_raw_receipts.py', receipt, '--catalog', staged_catalog)
                run('firmware/upload_raw_assets.py', '--directory', work / 'raw', '--catalog', staged_catalog,
                    '--package-id', receipt.stem, '--repo', repository)
                write_json(catalog, json.loads(staged_catalog.read_text()))
                promoted = True
                destination.mkdir(parents=True, exist_ok=True)
                write_json(destination / receipt.name, document)
                published.append(receipt.stem)
            except Exception as error:
                # Roll back only this package; retain earlier successful sources
                # and extracted packages so another failure cannot strand them.
                if promoted:
                    write_json(catalog, json.loads(previous))
                errors.append({'package_id': receipt.stem, 'step': 'publish', 'error': str(error)})
        report = json.loads(report_path.read_text()) if report_path.exists() else {'schema_version': 1}
        try:
            inventory = inventory_preserved(root, work / 'inventories', repository, keys)
            report['inventory'] = inventory
            errors.extend(inventory['errors'])
            if inventory['pending']:
                report['status'] = 'partial'
        except Exception as error:
            errors.append({'step': 'inventory', 'error': str(error)})
        report['published'] = published
        report['errors'] = report.get('errors', []) + errors
        if errors:
            report['status'] = 'partial'
        write_json(report_path, report)
        if errors:
            raise RuntimeError(f'firmware extraction has {len(errors)} failed operations; verified progress retained')
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
    history = sub.add_parser('archive-historical-firmware')
    history.add_argument('--work-dir', type=Path, required=True)
    history.add_argument('--repository', required=True)
    installers = sub.add_parser('archive-installer-history')
    installers.add_argument('--kind', choices=('desktop', 'mobile'), required=True)
    installers.add_argument('--work-dir', type=Path, required=True)
    installers.add_argument('--repository', required=True)
    installers.add_argument('--refresh-hours', type=float, default=6)
    installers.add_argument('--historical-refresh-hours', type=float, default=168)
    extract = sub.add_parser('extract-firmware')
    extract.add_argument('--work-dir', type=Path, required=True)
    extract.add_argument('--repository', required=True)
    binaries = sub.add_parser('scan-preserved-binaries')
    binaries.add_argument('--work-dir', type=Path, required=True)
    binaries.add_argument('--repository', required=True)
    args = parser.parse_args()
    if os.environ.get('GITHUB_ACTIONS') != 'true':
        parser.error('acquisition tasks run in the CI worker; use the platform plan locally')
    if args.task == 'store-apple-metadata':
        store_apple(args.root, args.current)
    elif args.task == 'store-google-play-metadata':
        write_json(args.root / 'data/apps/google-play-listings.json', json.loads(args.current.read_text()))
    elif args.task == 'extract-firmware':
        extract_firmware(args.root, args.work_dir, args.repository)
    elif args.task == 'archive-installer-history':
        from ..installers import archive_history
        report = archive_history(args.root, args.work_dir, args.repository, args.kind,
                                 refresh_hours=args.historical_refresh_hours,
                                 current_refresh_hours=args.refresh_hours)
        write_json(args.work_dir/'replay.json', report)
        print(json.dumps(report, indent=2))
    elif args.task == 'archive-historical-firmware':
        from ..historical_firmware import archive_history
        report = archive_history(args.root, args.work_dir, args.repository)
        write_json(args.work_dir / 'replay.json', report)
        print(json.dumps(report, indent=2))
    elif args.task == 'scan-preserved-binaries':
        report = discover_preserved(args.root, args.work_dir, args.repository)
        print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
