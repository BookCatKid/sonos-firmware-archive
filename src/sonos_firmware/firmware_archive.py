"""Preserve exact firmware captures independently of filenames and extraction."""
from __future__ import annotations

import json
import hashlib
import struct
import tempfile
import urllib.error
from urllib.parse import urlsplit
from datetime import datetime, timezone
from pathlib import Path

from .discovery import download, DownloadFailure
from .releases import ensure_release, publish_verified, verified_asset, publication_receipt
from .retry import checkpoint, select, due, error_checkpoint, RequestBackoff
from .storage import atomic_json, file_sha256
from .upd import parse_file
from .archive_replay import capture_host


def document_name(item):
    """Immutable source document identity includes captured bytes and origin."""
    origin = hashlib.sha256(item['url'].encode()).hexdigest()
    if item.get('document_type') == 'landing-page':
        suffix = item.get('metadata', {}).get('format', 'html')
        suffix = suffix if suffix in {'js', 'mjs', 'json', 'html'} else 'html'
        return f"page-{item['sha256']}-{origin}.{suffix}"
    return f"manifest-{item['sha256']}-{origin}.upm"


def candidate_is_preserved(candidate, packages):
    def secure(url):
        return 'https://' + url[7:] if url.startswith('http://') else url
    url = secure(candidate['url'])
    return any(package.get('artifact_status') == 'preserved' and url in
               {secure(source) for source in [package.get('source_url', ''), *package.get('source_urls', [])]}
               for package in packages)


def catalog_at(path):
    return json.loads(path.read_text()) if path.exists() else {
        'schema_version': 1, 'source_manifests': [], 'packages': [], 'raw_images': []}


def document_publication(record, repository):
    proof = record.get('publications', {}).get(repository) or record.get('publication', {})
    return proof if (proof.get('status') == 'verified' and proof.get('repository') == repository and
                     proof.get('sha256') == record['sha256'] and proof.get('bytes') == record['bytes']) else None


def register_document(path, manifest, publication):
    if (publication.get('status') != 'verified' or not publication.get('repository') or
            publication.get('sha256') != manifest['sha256'] or publication.get('bytes') != manifest['bytes']):
        raise ValueError('manifest has no matching verified publication')
    catalog = catalog_at(path)
    collection = 'source_documents' if manifest.get('document_type') == 'landing-page' else 'source_manifests'
    rows = catalog.setdefault(collection, [])
    record = next((row for row in rows if row.get('sha256') == manifest['sha256']
                   and row.get('source_url') == manifest['url']), None)
    if record is None:
        record = {'sha256': manifest['sha256'], 'bytes': manifest['bytes'], 'source_url': manifest['url']}
        rows.append(record)
    if manifest.get('advertised_by'):
        record['advertised_by'] = manifest['advertised_by']
    meta = manifest.get('metadata', {})
    if manifest.get('document_type'):
        record.update(document_type=manifest['document_type'], metadata=meta)
    publications = record.setdefault('publications', {})
    prior = record.get('publication', {})
    if prior.get('repository'):
        publications[prior['repository']] = prior
    publications[publication['repository']] = publication
    record.update(version=meta.get('system_version') or meta.get('default_version') or 'unknown',
                  default_version=meta.get('default_version'), revision=meta.get('revision'),
                  filename=publication['release_asset'], release_tag=publication['release_tag'],
                  release_asset=publication['release_asset'], publication=publication)
    record.setdefault('provenance', manifest.get('provenance', 'independent-public-protocol-discovery'))
    if manifest.get('fetched_from'):
        retrieval = {'url': manifest['fetched_from'], **({'archive_capture': manifest['archive_capture']}
                     if manifest.get('archive_capture') else {})}
        if retrieval not in record.setdefault('retrievals', []):
            record['retrievals'].append(retrieval)
    atomic_json(path, catalog)
    return record


def acquisition_path(root):
    return root / 'data/discovery/firmware-acquisition.json'


def acquisition_state(root, path=None):
    path = path or acquisition_path(root)
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
               (row.get('filename') == filename or candidate['url'] in
                [row.get('source_url'), *row.get('source_urls', [])]) and
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
              'filename': prior.get('filename', filename),
              'advertised_filenames': sorted(set(prior.get('advertised_filenames', [prior['filename']] if prior.get('filename') else [])) | {filename}),
              'artifact_type': Path(filename).suffix.removeprefix('.'),
              'bytes': capture['bytes'], 'sha256': digest, 'artifact_status': 'preserved',
              'raw_status': (prior.get('raw_status', 'pending') if prior.get('artifact_status') == 'preserved' else 'pending') if is_upd else 'not-applicable',
              'source_url': prior.get('source_url') or candidate['url'],
              'source_urls': sorted(set(prior.get('source_urls', [])) | {candidate['url']} |
                                    ({prior['source_url']} if prior.get('source_url') else set())),
              'release_tag': publication['release_tag'], 'release_asset': publication['release_asset'],
              'release_url': publication['release_url'], 'repository': publication['repository']}
    if candidate.get('advertised_by'):
        record.setdefault('advertised_sources', {})[candidate['url']] = candidate['advertised_by']
    if candidate.get('archive_captures'):
        record['discovery_evidence'] = {**prior.get('discovery_evidence', {}), **candidate['archive_captures']}
    if capture.get('fetched_from'):
        retrieval = {'url': capture['fetched_from'], **({'archive_capture': candidate['archive_capture']}
                     if candidate.get('archive_capture') else {})}
        if capture.get('memento_datetime'):
            retrieval['memento_datetime'] = capture['memento_datetime']
        record['retrievals'] = list(prior.get('retrievals', []))
        if retrieval not in record['retrievals']:
            record['retrievals'].append(retrieval)
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
                   packages=sorted(packages.values(), key=lambda row: (
                       row['version'], row['package_model'] is None, row['package_model'] or 0, row['id'])))
    atomic_json(catalog_path, catalog)
    return record


def archive_candidates(root, repository, candidates, work, *, limit=24, catalog_path=None, state_path=None,
                       paused_hosts=()):
    """Acquire bounded batches, retaining progress and exact interrupted captures."""
    state_path = state_path or acquisition_path(root)
    state = acquisition_state(root, state_path)
    source_id = lambda row: row.get('acquisition_id') or row['url']
    observations = state.setdefault('sources', {}).setdefault(repository, {})
    catalog_path = catalog_path or root / 'data/catalog.json'
    catalog = catalog_at(catalog_path)
    known_urls = {url for row in catalog.get('packages', []) if row.get('artifact_status') == 'preserved'
                  for url in [row.get('source_url'), *row.get('source_urls', [])] if url}
    current_sources = {source_id(row) for row in candidates}
    available = {source_id(row): row for row in candidates}
    for url, row in observations.items():
        if row.get('pending_publication') and url not in available:
            available[url] = row['candidate']
    candidates = list(available.values())
    # Mutable protocol roots can change while versioned backlog is draining.
    # Prioritize their first capture; the shared selector still reserves retries
    # and excludes successful captures until their refresh cadence is due.
    candidates.sort(key=lambda row: row.get('source') != 'protocol-static')
    for candidate in candidates:
        url = source_id(candidate)
        if url in known_urls and url not in observations:
            observations[url] = {'status': 'needs-refresh', 'checked_at': '', 'candidate': candidate}
        observations.setdefault(url, {'status': 'pending'})['candidate'] = candidate
    eligibility = {url: dict(row) for url, row in observations.items() if row.get('status') != 'pending'}
    for url, row in list(eligibility.items()):
        if 'revision' in row and any(row['revision'].get(key) != value for key, value in revision(row['candidate']).items()):
            del eligibility[url]
            observations[url]['status'] = 'needs-refresh'
    attempted = preserved = 0
    errors = []
    ready = set()
    blocked = set(paused_hosts)
    backoff = RequestBackoff(state, repository)

    def save():
        state.update(updated=datetime.now(timezone.utc).isoformat())
        atomic_json(state_path, state)

    # Older captures at a mutable source URL remain independently reconcilable.
    backlog = state.setdefault('unpublished_captures', [])
    pending = [row for row in backlog if row.get('repository') == repository]
    def selectable(candidate):
        host = capture_host(candidate['archive_capture']) if candidate.get('archive_capture') is not None else urlsplit(candidate['url']).hostname
        previous = observations[source_id(candidate)]
        return ((host not in blocked and not backoff.paused(host)) or previous.get('pending_publication')
                or (previous.get('status') == 'preserved' and candidate.get('archive_capture') is not None))
    jobs = [{'kind': 'recovery', 'row': row, 'key': ('recovery', id(row))} for row in pending]
    jobs.extend({'kind': 'source', 'row': row, 'key': ('source', source_id(row))}
                for row in candidates if selectable(row))
    jobs_due = {('recovery', id(row)): row for row in pending}
    jobs_due.update({('source', identity): row for identity, row in eligibility.items()})
    chosen = select(jobs, jobs_due, key=lambda job: job['key'], limit=0 if backoff.paused('publication') else limit,
                    state=state, queue=repository)
    recovery = [job['row'] for job in chosen if job['kind'] == 'recovery']
    queue = [job['row'] for job in chosen if job['kind'] == 'source']
    for row in recovery:
        if backoff.paused('publication'):
            break
        attempted += 1
        capture = row['capture']
        try:
            remote = verified_asset(repository, capture['release_tag'], capture['release_asset'],
                                    capture['bytes'], capture['sha256'])
            backoff.clear('publication')
            if remote is None:
                raise ValueError('older capture is not yet visible in its release')
            capture['publication'] = publication_receipt(repository, capture['release_tag'],
                                                         capture['release_asset'], capture, remote)
            register_capture(root, capture, catalog_path=catalog_path)
            backlog.remove(row)
            preserved += 1
            backoff.clear('publication')
        except Exception as error:
            row.update(error_checkpoint(error, row), error=str(error))
            backoff.failed('publication', error)
        save()
    save()
    work.mkdir(parents=True, exist_ok=True)
    for candidate in queue:
        if backoff.paused('publication'):
            break
        url = source_id(candidate)
        host = capture_host(candidate['archive_capture']) if candidate.get('archive_capture') is not None else urlsplit(candidate['url']).hostname
        previous = observations[url]
        capture = previous.get('pending_publication')
        if (host in blocked or backoff.paused(host)) and not capture and previous.get('status') != 'preserved':
            continue
        attempted += 1
        phase = 'publication'
        try:
            if previous.get('status') == 'preserved' and candidate.get('archive_capture') is not None:
                known = next((row for row in catalog.get('packages', [])
                              if row['id'] == previous.get('package_id') and row.get('sha256') == previous.get('sha256')), None)
                remote = verified_asset(repository, known['release_tag'], known['release_asset'],
                                        known['bytes'], known['sha256']) if known else None
                if remote:
                    observations[url] = {**previous, **checkpoint(previous, success=True, success_hours=168), 'candidate': candidate}
                    preserved += 1
                    save()
                    continue
            if capture:
                tag, name = capture['release_tag'], capture['release_asset']
                remote = verified_asset(repository, tag, name, capture['bytes'], capture['sha256'])
                backoff.clear('publication')
            else:
                remote = None
            if remote is None:
                if host in blocked or backoff.paused(host):
                    continue
                if url not in current_sources:
                    raise ValueError('pending asset not visible; waiting for source availability')
                with tempfile.TemporaryDirectory(dir=work, prefix='firmware-') as directory:
                    phase = 'acquisition'
                    artifact = download(candidate, Path(directory))
                    if not artifact.get('downloaded'):
                        if artifact.get('download_paused') or artifact.get('download_http_status') in {403, 429}:
                            blocked.add(host)
                        raise DownloadFailure(artifact)
                    backoff.clear(host)
                    phase = 'publication'
                    body = Path(directory) / artifact['local_file']
                    inventory = section_inventory(body) if candidate['filename'].endswith('.upd') else {'sections': []}
                    sections = inventory['sections']
                    if capture and capture['sha256'] != artifact['sha256']:
                        backlog.append({'repository': repository, 'capture': capture})
                    capture = {**artifact, 'candidate': candidate, **inventory,
                               'observation_revision': revision(candidate),
                               'release_tag': 'firmware-' + candidate['version'], 'release_asset': artifact['release_asset']}
                    versions = [row for row in sections if row['section_type'] == 1 and row['payload_length'] == 12]
                    if len(versions) == 1:
                        with body.open('rb') as stream:
                            stream.seek(versions[0]['offset'] + 16)
                            major, minor, build = struct.unpack('<III', stream.read(12))
                        capture['embedded_version'] = f'{major}.{minor}-{build}'
                        if candidate['version'] == 'unknown':
                            capture['candidate'] = {**candidate, 'version': capture['embedded_version']}
                            capture['release_tag'] = 'firmware-' + capture['embedded_version']
                    observations[url]['pending_publication'] = capture
                    save()
                    tag, name = capture['release_tag'], capture['release_asset']
                    if tag not in ready:
                        ensure_release(repository, tag, f"Sonos firmware {capture['candidate']['version']}", 'Exact source firmware captures and inventories.')
                        ready.add(tag)
                    remote = publish_verified(repository, tag, body, capture['bytes'], capture['sha256'])
            capture['publication'] = publication_receipt(repository, tag, name, capture, remote)
            observations[url]['pending_publication'] = capture
            save()
            record = register_capture(root, capture, catalog_path=catalog_path)
            checked_revision = capture.get('observation_revision', revision(capture['candidate']))
            changed = any(checked_revision.get(key) != value for key, value in revision(candidate).items())
            observations[url] = {**checkpoint(previous, success=True, success_hours=candidate.get('refresh_hours', 168)), 'candidate': candidate,
                                 'revision': checked_revision, 'sha256': capture['sha256'], 'package_id': record['id'],
                                 'status': 'needs-refresh' if changed else 'preserved'}
            preserved += 1
            backoff.clear('publication')
        except Exception as error:
            backoff.failed(host if phase == 'acquisition' else 'publication', error)
            observations[url] = {**error_checkpoint(error, previous), 'candidate': candidate, 'revision': revision(candidate),
                                 'status': 'failed', 'error': str(error)}
            if capture:
                observations[url]['pending_publication'] = capture
            errors.append({'url': url, 'error': str(error)})
            if isinstance(error, urllib.error.HTTPError):
                error.close()
        save()
    errors = [{'url': row['candidate']['url'], 'error': row.get('error'),
               **({'acquisition_id': url} if url != row['candidate']['url'] else {})}
              for url, row in observations.items() if row['status'] == 'failed']
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
