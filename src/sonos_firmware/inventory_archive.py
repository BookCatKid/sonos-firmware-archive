"""Publish model-independent inventories from exact preserved source captures."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import tempfile
from dataclasses import asdict

from . import firmware_inventory, extract, cramfs, upd, catalog
from .keyring import recipient_key_path
from .releases import publish_verified, verified_asset, publication_receipt, download_verified
from .retry import checkpoint, select
from .storage import atomic_json, file_sha256


def decoder_revision():
    digest = hashlib.sha256()
    digest.update(Path(__file__).read_bytes())
    for module in (firmware_inventory, extract, cramfs, upd, catalog):
        digest.update(Path(module.__file__).read_bytes())
    return digest.hexdigest()


def _context(root, package, directory, limits):
    path = root / 'data/upd' / (package['id'] + '.json')
    sections = json.loads(path.read_text()).get('sections', []) if path.exists() else []
    keys = {fp: key for row in sections if (fp := row.get('recipient_id')) and row.get('encrypted')
            if (key := recipient_key_path(directory, package['package_model'], fp)).is_file()}
    return keys, {'source_sha256': package['sha256'], 'ready_recipients': sorted(keys),
                  'decoder_revision': decoder_revision(), 'limits': asdict(limits)}


def _promote(root, capture, current_revision):
    path = root / 'data/catalog.json'
    catalog = json.loads(path.read_text())
    package = next((row for row in catalog['packages'] if row['id'] == capture['package_id']), None)
    if package is None or package['sha256'] != capture['revision']['source_sha256']:
        return
    prior = package.get('inventory')
    if prior:
        if prior['complete'] and (not capture['complete'] or capture['revision'] != current_revision):
            return
        if capture['revision'] != current_revision and not capture['complete']:
            return
    package['inventory'] = {'source_sha256': package['sha256'], 'complete': capture['complete'],
        'revision': capture['revision'], 'publications': capture['publications']}
    atomic_json(path, catalog)


def inventory_preserved(root, work, repository, key_directory, *, limit=4, acquire=None, build=None, limits=None):
    if limit <= 0:
        raise ValueError('inventory batch limit must be positive')
    limits = limits or firmware_inventory.InventoryLimits()
    work.mkdir(parents=True, exist_ok=True)
    state_path = root / 'data/discovery/firmware-inventories.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'schema_version': 1, 'repositories': {}}
    journal = state['repositories'].setdefault(repository, {'packages': {}, 'captures': {}})
    observations, captures = journal['packages'], journal['captures']
    catalog_path = root / 'data/catalog.json'
    catalog = json.loads(catalog_path.read_text()) if catalog_path.exists() else {'packages': []}
    packages = [row for row in catalog['packages'] if isinstance(row, dict) and
                row.get('artifact_status') == 'preserved' and row.get('artifact_type') == 'upd']
    contexts = {}
    errors = []
    for package in packages:
        identity = package['id']
        try:
            if not identity or Path(identity).name != identity or identity in {'.', '..'}:
                raise ValueError('inventory package ID must be a basename')
            contexts[identity] = _context(root, package, key_directory, limits)
        except Exception as error:
            errors.append({'package_id': identity, 'error': str(error)})
    packages = [row for row in packages if row['id'] in contexts]
    eligible = dict(observations)
    for package in packages:
        prior = observations.get(package['id'], {})
        revision = contexts[package['id']][1]
        if prior.get('revision') != revision:
            eligible.pop(package['id'], None)
        elif prior.get('status') == 'published':
            eligible[package['id']] = {**prior, 'next_attempt_at': '9999-01-01T00:00:00+00:00'}
    attempted = published = 0
    recovery_attempted = 0
    resolved = set()

    def save():
        atomic_json(state_path, state)

    # Reconcile exact interrupted captures before requiring source bytes or keys.
    pending = [key for key, value in captures.items() if value['status'] == 'pending']
    recover = select(pending, captures, key=lambda key: key, limit=max(1, limit // 4),
                     state=journal, queue='publication')
    for identity in recover:
        capture = captures[identity]
        recovery_attempted += 1
        try:
            for name, asset in capture['assets'].items():
                remote = verified_asset(repository, asset['release_tag'], asset['release_asset'], asset['bytes'], asset['sha256'])
                if remote is None:
                    # The worker is ephemeral: rebuild from the preserved UPD
                    # when an upload never reached GitHub. Retain this capture's
                    # hashes and successful receipts, including old revisions.
                    capture['status'] = 'incomplete'
                    package_id = capture['package_id']
                    if package_id in contexts:
                        observations.pop(package_id, None)
                        eligible.pop(package_id, None)
                    raise ValueError('inventory upload is incomplete; source queued for rebuilding')
                capture.setdefault('publications', {})[name] = publication_receipt(repository,
                    asset['release_tag'], asset['release_asset'], asset, remote)
                save()
            current = contexts.get(capture['package_id'], ({}, None))[1]
            _promote(root, capture, current)
            capture['status'] = 'published'
            if capture['revision'] == current:
                resolved.add(capture['package_id'])
                observations[capture['package_id']] = {'status': 'published', 'revision': current}
                if capture.get('analysis_errors'):
                    observations[capture['package_id']] = {**checkpoint(capture), 'status': 'failed', 'revision': current}
            published += 1
            attempted += 1
        except Exception as error:
            capture.update(checkpoint(capture), error=str(error))
            errors.append({'package_id': capture['package_id'], 'error': str(error)})
        save()
    queue = select([row for row in packages if row['id'] not in resolved], eligible,
                   key=lambda row: row['id'], limit=max(0, limit - attempted), state=journal, queue='inventory')
    for package in queue:
        identity = package['id']
        keys, revision = contexts[identity]
        attempted += 1
        try:
            with tempfile.TemporaryDirectory(dir=work, prefix='inventory-') as temporary:
                staging = Path(temporary)
                source = staging / (identity + '.upd')
                if acquire:
                    acquire(package, source, repository)
                else:
                    download_verified(package, source, repository)
                if source.stat().st_size != package['bytes'] or file_sha256(source) != package['sha256']:
                    raise ValueError('inventory source does not match its preservation receipt')
                directory = staging / 'files'
                report = (build or firmware_inventory.inventory_package)(source, directory, keys, limits=limits)
                report.update(package_id=identity, advertised_version=package.get('version'), revision=revision,
                              source_publication={key: package.get(key) for key in ('release_tag', 'release_asset', 'sha256', 'bytes')})
                atomic_json(directory / 'inventory.json', report)
                archive = staging / 'inventory.tar.xz'
                firmware_inventory.bundle(directory, archive)
                files = {'manifest': directory / 'inventory.json', 'bundle': archive}
                assets = {}
                for name, path in files.items():
                    digest = file_sha256(path)
                    suffix = '.json' if name == 'manifest' else '.tar.xz'
                    asset = staging / ('inventory-' + package['sha256'] + '-' + digest + suffix)
                    asset.hardlink_to(path)
                    files[name] = asset
                    assets[name] = {'sha256': digest, 'bytes': asset.stat().st_size,
                                    'release_tag': package['release_tag'], 'release_asset': asset.name}
                capture_id = assets['manifest']['sha256']
                capture = {'package_id': identity, 'revision': revision, 'complete': report['complete'],
                           'analysis_errors': report.get('errors', []),
                           'assets': assets, 'status': 'pending', 'publications': {}}
                captures[capture_id] = capture
                save()
                for name, asset in assets.items():
                    remote = publish_verified(repository, asset['release_tag'], files[name], asset['bytes'], asset['sha256'])
                    capture['publications'][name] = publication_receipt(repository, asset['release_tag'], asset['release_asset'], asset, remote)
                    save()
                _promote(root, capture, revision)
                capture['status'] = 'published'
                observations[identity] = {'status': 'published', 'revision': revision}
                if report.get('errors'):
                    observations[identity] = {**checkpoint(observations.get(identity)), 'status': 'failed', 'revision': revision}
                    errors.extend({'package_id': identity, **row} for row in report['errors'])
                published += 1
        except Exception as error:
            observations[identity] = {**checkpoint(observations.get(identity)), 'status': 'failed', 'revision': revision}
            errors.append({'package_id': identity, 'error': str(error)})
        save()
    remaining = sum(observations.get(row['id'], {}).get('status') != 'published' or
                    observations.get(row['id'], {}).get('revision') != contexts[row['id']][1] for row in packages)
    pending = sum(row['status'] == 'pending' for row in captures.values())
    result = {'attempted': attempted, 'recovery_attempted': recovery_attempted,
              'published': published, 'pending': remaining + pending, 'errors': errors}
    result['status'] = 'partial' if errors or result['pending'] else 'ok'
    return result
