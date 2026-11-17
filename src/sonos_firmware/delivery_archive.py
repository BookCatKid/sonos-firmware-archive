"""Checkpoint and reconcile verified multi-file app deliveries."""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from .releases import ensure_release, publish_collection, publication_receipt, receipt_matches_repository
from .retry import checkpoint, select
from .storage import atomic_json


def _load(path):
    document = json.loads(path.read_text()) if path.exists() else {'schema_version': 1, 'artifacts': []}
    if document.get('schema_version') != 1 or not isinstance(document.get('artifacts'), list):
        raise ValueError('unsupported delivery archive schema')
    return document


def _save(path, document, repository, source_kind):
    pending = [row for row in document.get('pending_captures', []) if row['repository'] == repository
               and row['artifact']['source_kind'] == source_kind]
    document.update(updated=datetime.now(timezone.utc).isoformat(), repository=repository,
                    source_kind=source_kind, pending_total=len(pending),
                    status='partial' if pending else 'ok',
                    errors=[{'capture_id': row['capture_id'], 'error': row.get('error', 'publication pending')}
                            for row in pending])
    atomic_json(path, document)


def _assets(artifact):
    return [*artifact['components'], *([artifact['complete_wrapper']] if artifact.get('complete_wrapper') else [])]


def _identity(artifact):
    value = {'package': artifact['package'], 'source_kind': artifact['source_kind'],
             'version_code': str(artifact['version_code']),
             'assets': sorted((row['release_asset'], row['bytes'], row['sha256']) for row in _assets(artifact))}
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def _body_key(asset):
    return Path(asset['release_asset']).suffix.lower(), asset['bytes'], asset['sha256']


def _content_key(artifact):
    wrapper = artifact.get('complete_wrapper')
    parts = sorted(('wrapper' if row is wrapper else 'component', *_body_key(row)) for row in _assets(artifact))
    return artifact['package'], artifact['source_kind'], str(artifact['version_code']), tuple(parts)


def _reuse_publication_names(document, repository, artifact, files):
    """Keep existing publication/journal names when a downloader renames files.

    Matching the complete content set preserves old partial uploads and avoids
    another asset copy for an identical delivery from a different source URL.
    """
    candidates = [row['artifact'] for row in document.get('pending_captures', []) if row['repository'] == repository]
    candidates += [row for row in document['artifacts'] if receipt_matches_repository(row, repository)]
    key = _content_key(artifact)
    prior = next((row for row in candidates if row['release_tag'] == artifact['release_tag']
                  and _content_key(row) == key), None)
    if prior is None:
        return files
    names, paths = {}, {}
    for record in _assets(prior):
        names.setdefault(_body_key(record), []).append(record['release_asset'])
    for path, record in files:
        paths.setdefault(_body_key(record), []).append(path)
    remapped = []
    for record in _assets(artifact):
        body = _body_key(record)
        record['release_asset'] = names[body].pop()
        remapped.append((paths[body].pop(), record))
    return remapped


def delivery_profiles(artifact):
    profiles = list(artifact.get('delivery_profiles', []))
    if 'delivery_profile' in artifact:
        # An unprofiled selector capture is evidence of its own. Do not lose
        # it when the same bytes are later observed as a structured variant.
        profile = artifact['delivery_profile'] or {}
        if profile not in profiles:
            profiles.append(profile)
    return profiles


def _merge_observations(target, incoming):
    profiles = delivery_profiles(target)
    for profile in delivery_profiles(incoming):
        if profile not in profiles:
            profiles.append(profile)
    if profiles:
        target['delivery_profiles'] = profiles
    urls = set(target.get('source_urls', [])) | set(incoming.get('source_urls', []))
    urls.update(row['source_url'] for row in (target, incoming) if row.get('source_url'))
    if urls:
        target['source_urls'] = sorted(urls)


def _promote(document, capture, repository, observed):
    artifact = capture['artifact']
    for asset in _assets(artifact):
        asset.update(publication_receipt(repository, artifact['release_tag'], asset['release_asset'],
                                         asset, observed[asset['release_asset']]))
    identity = _identity(artifact)
    artifact.update(repository=repository, delivery_sha256=identity)
    prior = next((row for row in document['artifacts'] if receipt_matches_repository(row, repository)
                  and _identity(row) == identity), None)
    if prior:
        selectors = set(prior.get('source_version_selectors', [])) | set(artifact.get('source_version_selectors', []))
        selectors.update(row['source_version_selector'] for row in (prior, artifact) if row.get('source_version_selector'))
        _merge_observations(artifact, prior)
        prior.update(artifact, source_version_selectors=sorted(selectors))
    else:
        document['artifacts'].append(artifact)
    document['pending_captures'] = [row for row in document.get('pending_captures', [])
        if not (row['repository'] == repository and row['capture_id'] == capture['capture_id'])]
    document.update(schema_version=1, artifact_total=len(document['artifacts']))


def publish_delivery(receipt, repository, artifact, files, *, title, notes):
    """Stage exact signed-capture metadata before any release mutation."""
    apks = [row for row in artifact['components'] if row['release_asset'].lower().endswith('.apk')]
    if not apks or any(row.get('verified') is not True for row in apks):
        raise ValueError('delivery requires at least one signature-verified APK')
    document = _load(receipt)
    files = _reuse_publication_names(document, repository, artifact, files)
    capture = {'repository': repository, 'capture_id': _identity(artifact),
               'artifact': artifact, 'title': title, 'notes': notes}
    pending = document.setdefault('pending_captures', [])
    prior = next((row for row in pending if row['repository'] == repository
                  and row['capture_id'] == capture['capture_id']), None)
    if prior:
        # Preserve the original observation after a mutable source changes.
        capture = prior
        _merge_observations(capture['artifact'], artifact)
        selectors = set(capture['artifact'].get('source_version_selectors', []))
        selectors.update(row['source_version_selector'] for row in (capture['artifact'], artifact)
                         if row.get('source_version_selector'))
        capture['artifact']['source_version_selectors'] = sorted(selectors)
    else:
        pending.append(capture)
    _save(receipt, document, repository, artifact['source_kind'])
    try:
        ensure_release(repository, artifact['release_tag'], title, notes)
        observed = publish_collection(repository, artifact['release_tag'], files)
        _promote(document, capture, repository, observed)
    except Exception as error:
        capture.update(checkpoint(capture), error=str(error))
        _save(receipt, document, repository, artifact['source_kind'])
        raise
    _save(receipt, document, repository, artifact['source_kind'])
    return capture['artifact']


def recover_deliveries(receipt, repository, source_kind, packages, *, selector=None, limit=4):
    """Recover exact prior uploads without acquiring or executing a delivery."""
    document = _load(receipt)
    pending = [row for row in document.get('pending_captures', []) if row['repository'] == repository
        and row['artifact']['source_kind'] == source_kind and row['artifact']['package'] in packages
        and (selector is None or selector in {row['artifact'].get('source_version_selector'),
                                             *row['artifact'].get('source_version_selectors', [])})]
    queue = select(pending, {row['capture_id']: row for row in pending}, key=lambda row: row['capture_id'], limit=limit,
                   state=document, queue=f'{repository}/{source_kind}/{",".join(sorted(packages))}/{selector or "*"}')
    recovered = []
    for capture in queue:
        artifact = capture['artifact']
        try:
            # These intentionally absent paths allow remote-only reconciliation.
            # Missing uploads require a later real acquisition, never a claim
            # that a URL or successful command alone preserved the delivery.
            with tempfile.TemporaryDirectory(prefix='delivery-reconcile-') as temporary:
                observed = publish_collection(repository, artifact['release_tag'],
                    [(Path(temporary)/asset['release_asset'], asset) for asset in _assets(artifact)])
            _promote(document, capture, repository, observed)
            recovered.append(artifact)
        except Exception as error:
            capture.update(checkpoint(capture), error=str(error))
        _save(receipt, document, repository, source_kind)
    return recovered
