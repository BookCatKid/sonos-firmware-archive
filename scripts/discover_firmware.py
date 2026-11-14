#!/usr/bin/env python3
"""Discover Sonos firmware from public protocol roots and our saved frontier."""
import argparse
import json
import sys
import hashlib
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from sonos_firmware.discovery import write_json
from sonos_firmware.frontier import discover, monitor_report
from sonos_firmware.archive_index import manifest_roots
from sonos_firmware.firmware_archive import acquisition_state, archive_candidates


def preserve(result, catalog_path, state_path, evidence, work, repository, max_packages=24):
    from archive_monitor_firmware import ensure_release, upload_small_verified, manifest_name
    if catalog_path.exists():
        catalog = json.loads(catalog_path.read_text())
    else:
        # The worker creates bookkeeping; adding a target needs no prior run.
        catalog = {'schema_version': 1, 'generated': datetime.now(timezone.utc).date().isoformat(),
                   'source_manifests': [], 'packages': [], 'raw_images': []}
        write_json(catalog_path, catalog)
    errors = []
    tag = 'manifest-snapshots-' + datetime.now(timezone.utc).date().isoformat()
    prepared = False
    for manifest in result['manifests']:
        known = next((m for m in catalog.get('source_manifests', [])
                      if m.get('sha256') == manifest['sha256'] and m.get('source_url') == manifest['url']), None)
        if known:
            continue
        try:
            # Cached observations move between workers; their old absolute
            # temporary paths are not acquisition prerequisites.
            path = (evidence / Path(manifest['snapshot']).name).resolve()
            if not path.is_relative_to(evidence.resolve()):
                raise ValueError('manifest capture not in the discovery evidence directory')
            data = path.read_bytes()
            if len(data) != manifest['bytes'] or hashlib.sha256(data).hexdigest() != manifest['sha256']:
                raise ValueError('manifest capture hash mismatch')
            if not prepared:
                ensure_release(repository, tag, 'Sonos manifest snapshots', 'Directly discovered public Sonos update manifests.')
                prepared = True
            asset = work / manifest_name(manifest)
            asset.write_bytes(data)
            upload_small_verified(repository, tag, asset, manifest['sha256'])
            meta = manifest['metadata']
            catalog.setdefault('source_manifests', []).append({
                'version': meta.get('system_version') or meta.get('default_version') or 'unknown',
                'default_version': meta.get('default_version'), 'revision': meta.get('revision'),
                'filename': asset.name, 'bytes': len(data), 'sha256': manifest['sha256'],
                'source_url': manifest['url'], 'provenance': 'independent-public-protocol-discovery',
                'release_tag': tag, 'release_asset': asset.name,
            })
            write_json(catalog_path, catalog)
        except Exception as error:
            errors.append({'source': manifest['url'], 'error': str(error)})
    archive_root = catalog_path.parent.parent if catalog_path.parent.name == 'data' else catalog_path.parent
    report = monitor_report(result, catalog, acquisition_state(archive_root), repository)
    candidates = report['available_uncatalogued_candidates'] + report['available_refresh_candidates']
    archived = archive_candidates(archive_root, repository, candidates, work,
                                  limit=max_packages, catalog_path=catalog_path)
    errors.extend(archived['errors'])
    result['preservation_pending'] = archived['pending']
    result['preservation_errors'] = errors
    write_json(state_path, result)
    return bool(errors)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=ROOT / 'data/discovery/firmware-frontier.json')
    parser.add_argument('--evidence-dir', type=Path, required=True)
    parser.add_argument('--report', type=Path, required=True)
    parser.add_argument('--catalog', type=Path, default=ROOT / 'data/catalog.json')
    parser.add_argument('--cold-start', action='store_true', help='ignore all saved frontier and catalog knowledge during discovery')
    parser.add_argument('--seconds', type=float, default=900)
    parser.add_argument('--max-manifests', type=int, default=1024)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--max-packages', type=int, default=24)
    parser.add_argument('--artifact-root', type=json.loads, action='append', default=[],
                        help='protocol artifact root JSON with url and package_model; version is read from captured bytes')
    parser.add_argument('--repository', default='BookCatKid/sonos-firmware-archive')
    parser.add_argument('--work-dir', type=Path)
    parser.add_argument('--no-history-index', action='store_true', help='restrict discovery to live Sonos protocol roots and saved observations')
    args = parser.parse_args()
    if args.max_packages <= 0:
        parser.error('--max-packages must be positive')
    old = json.loads(args.state.read_text()) if args.state.exists() and not args.cold_start else {}
    catalog = json.loads(args.catalog.read_text()) if args.catalog.exists() else {}
    known = []
    if not args.cold_start:
        for package in catalog.get('packages', []):
            for url in dict.fromkeys([package.get('source_url'), *package.get('source_urls', [])]):
                if url and urlsplit(url).path.endswith('.upd') and package.get('package_model') is not None:
                    known.append({'version': package['version'], 'package_model': package['package_model'],
                        'url': url, 'filename': urlsplit(url).path.rsplit('/', 1)[-1], 'source': 'saved-archive-observation'})
    result = discover(old, args.evidence_dir, seconds=args.seconds, max_manifests=args.max_manifests,
                      index_fetch=None if args.no_history_index else manifest_roots, artifact_roots=args.artifact_root,
                      known_artifacts=known)
    write_json(args.state, result)
    write_json(args.report, monitor_report(result, catalog))
    failed = False
    if args.publish:
        work = args.work_dir or args.evidence_dir.parent / 'publish'
        work.mkdir(parents=True, exist_ok=True)
        failed = preserve(result, args.catalog, args.state, args.evidence_dir, work, args.repository, args.max_packages)
        archive_root = args.catalog.parent.parent if args.catalog.parent.name == 'data' else args.catalog.parent
        catalog = json.loads(args.catalog.read_text())
        write_json(args.report, monitor_report(result, catalog, acquisition_state(archive_root), args.repository))
    print(f"manifests={len(result['manifests'])} candidates={len(result['candidates'])} checked={result['checked_this_run']} pending={len(result['pending_manifest_urls'])}")
    return 1 if failed else 0


if __name__ == '__main__':
    raise SystemExit(main())
