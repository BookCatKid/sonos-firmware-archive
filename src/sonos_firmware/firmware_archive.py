"""Preserve exact firmware captures independently of filenames and extraction."""
from __future__ import annotations

import json
import struct
import tempfile
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from .discovery import download
from .releases import ensure_release, publish_verified, verified_asset
from .retry import checkpoint, select, due
from .storage import atomic_json, file_sha256
from .upd import parse_file


def catalog_at(path):
    return json.loads(path.read_text()) if path.exists() else {
        'schema_version': 1, 'source_manifests': [], 'packages': [], 'raw_images': []}


def acquisition_path(root):
    return root / 'data/discovery/firmware-acquisition.json'


def acquisition_state(root):
    path = acquisition_path(root)
    return json.loads(path.read_text()) if path.exists() else {'schema_version': 1, 'sources': {}}


def revision(candidate):
    return {key: candidate[key] for key in ('version', 'package_model', 'bytes', 'etag', 'last_modified', 'http_status')
            if candidate.get(key) is not None}


def capture_due(candidate, observation):
    if not observation:
        return True
    if any(observation.get('revision', {}).get(key) != value for key, value in revision(candidate).items()):
        return True
    return due(observation)


def section_inventory(body):
    try:
        return {'sections': [section.to_dict() for section in parse_file(body)]}
    except ValueError as error:
        # An unsupported container must not prevent preservation of its source.
        return {'sections': [], 'inventory_error': str(error)}


def register_capture(root, capture, *, catalog_path=None):
    """Register only captured bytes whose publication was size/hash verified."""
    catalog_path = catalog_path or root / 'data/catalog.json'
    publication = capture.get('publication', {})
    if (publication.get('status') != 'verified' or publication.get('sha256') != capture['sha256']
        or publication.get('bytes') != capture['bytes'] or not publication.get('release_tag')
        or not publication.get('release_asset') or not publication.get('release_url') or not publication.get('repository')):
        raise ValueError('firmware capture has no matching verified publication receipt')
    candidate = capture['candidate']
    filename = candidate['filename']
    if not filename or Path(filename).name != filename or filename in {'.', '..'}:
        raise ValueError('firmware filename must be a basename')
    digest = capture['sha256']
    catalog = catalog_at(catalog_path)
    packages = {row['id']: row for row in catalog.get('packages', [])}
    original_id = Path(filename).stem if filename.endswith('.upd') else filename
    matches = [row for row in packages.values() if row.get('sha256') == digest and
               row.get('filename') == filename and
               row.get('package_model') == candidate['package_model']]
    if matches:
        package_id = matches[0]['id']
    elif original_id in packages and (
        packages[original_id].get('sha256') is not None or
        packages[original_id].get('package_model') != candidate['package_model']
    ):
        package_id = original_id + '--model-' + str(candidate['package_model']) + '--' + digest
    else:
        package_id = original_id
    prior = packages.get(package_id, {})
    models_path = root / 'config/models.json'
    models = json.loads(models_path.read_text()).get('models', []) if models_path.exists() else []
    model = next((row for row in models if row['package_model'] == candidate['package_model']), {})
    is_upd = filename.endswith('.upd')
    if is_upd and not capture.get('sections') and not capture.get('inventory_error'):
        raise ValueError('UPD capture has neither a section inventory nor a parser failure receipt')
    record = {**prior, 'id': package_id, 'version': prior.get('version', candidate['version']),
              'advertised_versions': sorted(set(prior.get('advertised_versions', [prior['version']] if prior else [])) | {candidate['version']}), 'package_model': candidate['package_model'],
              'product': prior.get('product') or model.get('display_name'), 'model_number': prior.get('model_number'),
              'filename': filename, 'artifact_type': Path(filename).suffix.removeprefix('.'),
              'bytes': capture['bytes'], 'sha256': digest, 'artifact_status': 'preserved',
              'raw_status': (prior.get('raw_status', 'pending') if prior.get('artifact_status') == 'preserved' else 'pending') if is_upd else 'not-applicable',
              'source_url': prior.get('source_url') or candidate['url'],
              'source_urls': sorted(set(prior.get('source_urls', [])) | {candidate['url']} |
                                    ({prior['source_url']} if prior.get('source_url') else set())),
              'release_tag': publication['release_tag'], 'release_asset': publication['release_asset'],
              'release_url': publication['release_url'], 'repository': publication['repository']}
    if capture.get('embedded_version'):
        record['embedded_version'] = capture['embedded_version']
    if prior.get('artifact_status') != 'preserved':
        record['extraction_policy'] = 'all-sections'
    if is_upd:
        manifest = root / 'data/upd' / (package_id + '.json')
        if not manifest.exists():
            atomic_json(manifest, {'package_id': package_id, 'bytes': capture['bytes'],
                                   'sha256': digest, 'sections': capture['sections'],
                                   **({'inventory_error': capture['inventory_error']} if capture.get('inventory_error') else {})})
        else:
            prior_inventory = json.loads(manifest.read_text())
            if prior_inventory.get('sha256') != digest:
                raise ValueError('existing section inventory belongs to another source capture')
            if prior_inventory.get('inventory_error') and capture.get('sections'):
                atomic_json(manifest, {'package_id': package_id, 'bytes': capture['bytes'],
                                       'sha256': digest, 'sections': capture['sections']})
    packages[package_id] = record
    catalog.update(generated=datetime.now(timezone.utc).date().isoformat(),
                   packages=sorted(packages.values(), key=lambda row: (row['version'], row['package_model'], row['id'])))
    atomic_json(catalog_path, catalog)
    return record


def publication_receipt(repository, tag, name, capture, remote):
    return {'status': 'verified', 'repository': repository, 'release_tag': tag, 'release_asset': name,
            'release_url': remote.get('browser_download_url') or remote['url'],
            'sha256': capture['sha256'], 'bytes': capture['bytes']}


def archive_candidates(root, repository, candidates, work, *, limit=24, catalog_path=None):
    """Acquire bounded batches, retaining progress and exact interrupted captures."""
    state = acquisition_state(root)
    observations = state.setdefault('sources', {}).setdefault(repository, {})
    catalog_path = catalog_path or root / 'data/catalog.json'
    catalog = catalog_at(catalog_path)
    known_urls = {url for row in catalog.get('packages', []) if row.get('artifact_status') == 'preserved'
                  for url in [row.get('source_url'), *row.get('source_urls', [])] if url}
    current_urls = {row['url'] for row in candidates}
    available = {row['url']: row for row in candidates}
    for url, row in observations.items():
        if row.get('pending_publication') and url not in available:
            available[url] = row['candidate']
    candidates = list(available.values())
    for candidate in candidates:
        url = candidate['url']
        if url in known_urls and url not in observations:
            observations[url] = {'status': 'needs-refresh', 'checked_at': '', 'candidate': candidate}
        observations.setdefault(url, {'status': 'pending'})['candidate'] = candidate
    eligibility = {url: dict(row) for url, row in observations.items() if row.get('status') != 'pending'}
    for url, row in list(eligibility.items()):
        if 'revision' in row and any(row['revision'].get(key) != value for key, value in revision(row['candidate']).items()):
            del eligibility[url]
            observations[url]['status'] = 'needs-refresh'
    queue = select(candidates, eligibility, key=lambda row: row['url'], limit=limit)
    attempted = preserved = 0
    errors = []
    ready = set()

    def save():
        state.update(updated=datetime.now(timezone.utc).isoformat())
        atomic_json(acquisition_path(root), state)

    # Older captures at a mutable source URL remain independently reconcilable.
    backlog = state.setdefault('unpublished_captures', [])
    recovery = select([row for row in backlog if row.get('repository') == repository],
                      {id(row): row for row in backlog}, key=id, limit=max(1, limit // 4)) if limit > 0 else []
    for row in recovery:
        attempted += 1
        capture = row['capture']
        try:
            remote = verified_asset(repository, capture['release_tag'], capture['release_asset'],
                                    capture['bytes'], capture['sha256'])
            if remote is None:
                raise ValueError('older capture is not yet visible in its release')
            capture['publication'] = publication_receipt(repository, capture['release_tag'],
                                                         capture['release_asset'], capture, remote)
            register_capture(root, capture, catalog_path=catalog_path)
            backlog.remove(row)
            preserved += 1
        except Exception as error:
            row.update(checkpoint(row), error=str(error))
        save()
    queue = queue[:max(0, limit - attempted)]
    save()
    work.mkdir(parents=True, exist_ok=True)
    for candidate in queue:
        url = candidate['url']
        previous = observations[url]
        capture = previous.get('pending_publication')
        attempted += 1
        try:
            if capture:
                tag, name = capture['release_tag'], capture['release_asset']
                remote = verified_asset(repository, tag, name, capture['bytes'], capture['sha256'])
            else:
                remote = None
            if remote is None:
                if url not in current_urls:
                    raise ValueError('pending asset not visible; waiting for source availability')
                with tempfile.TemporaryDirectory(dir=work, prefix='firmware-') as directory:
                    artifact = download(candidate, Path(directory))
                    if not artifact.get('downloaded'):
                        raise ValueError(artifact.get('download_error', 'firmware acquisition failed'))
                    body = Path(directory) / artifact['local_file']
                    inventory = section_inventory(body) if candidate['filename'].endswith('.upd') else {'sections': []}
                    sections = inventory['sections']
                    if capture and capture['sha256'] != artifact['sha256']:
                        backlog.append({'repository': repository, 'capture': capture})
                    capture = {**artifact, 'candidate': candidate, **inventory,
                               'release_tag': 'firmware-' + candidate['version'], 'release_asset': artifact['release_asset']}
                    versions = [row for row in sections if row['section_type'] == 1 and row['payload_length'] == 12]
                    if len(versions) == 1:
                        with body.open('rb') as stream:
                            stream.seek(versions[0]['offset'] + 16)
                            major, minor, build = struct.unpack('<III', stream.read(12))
                        capture['embedded_version'] = f'{major}.{minor}-{build}'
                    observations[url]['pending_publication'] = capture
                    save()
                    tag, name = capture['release_tag'], capture['release_asset']
                    if tag not in ready:
                        ensure_release(repository, tag, f"Sonos firmware {candidate['version']}", 'Exact source firmware captures and inventories.')
                        ready.add(tag)
                    remote = publish_verified(repository, tag, body, capture['bytes'], capture['sha256'])
            capture['publication'] = publication_receipt(repository, tag, name, capture, remote)
            observations[url]['pending_publication'] = capture
            save()
            record = register_capture(root, capture, catalog_path=catalog_path)
            checked_revision = revision(capture['candidate'])
            changed = any(checked_revision.get(key) != value for key, value in revision(candidate).items())
            observations[url] = {**checkpoint(previous, success=True, success_hours=168), 'candidate': candidate,
                                 'revision': checked_revision, 'sha256': capture['sha256'], 'package_id': record['id'],
                                 'status': 'needs-refresh' if changed else 'preserved'}
            preserved += 1
        except Exception as error:
            observations[url] = {**checkpoint(previous), 'candidate': candidate, 'revision': revision(candidate),
                                 'status': 'failed', 'error': str(error)}
            if capture:
                observations[url]['pending_publication'] = capture
            errors.append({'url': url, 'error': str(error)})
            if isinstance(error, urllib.error.HTTPError):
                error.close()
        save()
    errors = [{'url': url, 'error': row.get('error')} for url, row in observations.items() if row['status'] == 'failed']
    unresolved = [row for row in backlog if row.get('repository') == repository]
    errors.extend({'url': row['capture']['candidate']['url'], 'error': row['error']}
                  for row in unresolved if row.get('error'))
    pending = sum(row['status'] in ('pending', 'needs-refresh', 'failed') for row in observations.values()) + len(unresolved)
    return {'attempted': attempted, 'preserved': preserved, 'errors': errors, 'pending': pending}


def upload_receipt(path, directory, tag, repository):
    receipt = json.loads(path.read_text())
    for artifact in receipt['artifacts']:
        if not artifact.get('downloaded'):
            continue
        name = artifact.get('release_asset', artifact['local_file'])
        if not name or Path(name).name != name or name in ('.', '..'):
            raise ValueError('release asset must be a basename')
        body = (directory / artifact['local_file']).resolve()
        if not body.is_relative_to(directory.resolve()):
            raise ValueError('capture file escapes the artifact directory')
        remote = verified_asset(repository, tag, name, artifact['bytes'], artifact['sha256'])
        if body.exists():
            if body.stat().st_size != artifact['bytes'] or file_sha256(body) != artifact['sha256']:
                raise ValueError('firmware capture does not match its acquisition receipt')
            if artifact.get('filename', name).endswith('.upd'):
                artifact.update(section_inventory(body))
        if remote is None:
            if not body.exists():
                raise ValueError('unpublished capture has no staged bytes')
            # The publication name belongs to the receipt, not its staging path.
            with tempfile.TemporaryDirectory(dir=directory, prefix='publication-') as temporary:
                upload = Path(temporary) / name
                upload.hardlink_to(body)
                remote = publish_verified(repository, tag, upload, artifact['bytes'], artifact['sha256'])
        artifact['publication'] = publication_receipt(repository, tag, name, artifact, remote)
        atomic_json(path, receipt)
    return receipt


def import_receipt(root, discovery, receipt, directory):
    artifacts = {row['url']: row for row in receipt['artifacts'] if row.get('downloaded')}
    imported = []
    for candidate in discovery['candidates']:
        artifact = artifacts.get(candidate['url'])
        if not artifact:
            continue
        capture = {**artifact, 'candidate': candidate}
        if candidate['filename'].endswith('.upd') and not capture.get('sections') and not capture.get('inventory_error'):
            body = (directory / artifact.get('local_file', candidate['filename'])).resolve()
            if not body.is_relative_to(directory.resolve()):
                raise ValueError('capture file escapes the artifact directory')
            if body.stat().st_size != artifact['bytes'] or file_sha256(body) != artifact['sha256']:
                raise ValueError('firmware capture hash mismatch')
            capture.update(section_inventory(body))
        imported.append(register_capture(root, capture))
    return imported
