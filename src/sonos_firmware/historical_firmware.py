"""Acquire independently indexed firmware captures through the common publisher."""
from dataclasses import asdict
import hashlib
import json
import tempfile
import urllib.error
from pathlib import Path

from .archive_replay import replay_url, open_capture, canonical_source
from .crawl import PACKAGE, landing_url
from .discovery import Candidate, official_firmware_url, parse_manifest
from .firmware_archive import acquisition_state, archive_candidates, document_name, register_document
from .releases import ensure_release, publish_verified, verified_asset, publication_receipt
from .retry import checkpoint, select, due
from .storage import atomic_json


def remember_manifest(frontier, manifest):
    """Offer historical leads to live discovery without claiming current availability."""
    pending = frontier.setdefault('pending_manifest_urls', [])
    rows = {}
    for row in frontier.get('candidates', []):
        if landing_url(row['url']):
            if row['url'] not in pending:
                pending.append(row['url'])
        else:
            rows[row['url']] = row
    unresolved = []
    metadata = manifest.setdefault('metadata', {})

    def accepted(url):
        try:
            return canonical_source(url)
        except ValueError:
            unresolved.append({'url': url, 'manifest_url': manifest['url'],
                               'error': 'historical manifest link uses an unsupported URL'})
            return None

    for candidate in manifest.get('candidates', []):
        url = accepted(candidate['url'])
        if url is None:
            continue
        if landing_url(url):
            if url not in pending:
                pending.append(url)
            continue
        lead = candidate.copy()
        lead['url'] = url
        if not PACKAGE.fullmatch(lead['filename']):
            lead['version'] = 'unknown'
        row = rows.setdefault(url, lead)
        if row.get('package_model') is None:
            row['package_model'] = candidate['package_model']
        evidence = {'manifest_url': manifest['url'], 'sha256': manifest['sha256'],
                    'archive_capture': manifest['archive_capture'], 'advertised_version': candidate['version']}
        if evidence not in row.setdefault('historical_manifests', []):
            row['historical_manifests'].append(evidence)
    frontier['candidates'] = list(rows.values())
    for link in [*metadata.get('manifest_links', []), *metadata.get('landing_links', [])]:
        url = accepted(link)
        if url is None:
            continue
        if url not in pending:
            pending.append(url)
    metadata['unresolved_links'] = unresolved


def archive_manifests(root, work, repository, frontier, *, limit, paused=False):
    path = root/'data/discovery/historical-manifest-acquisition.json'
    state = acquisition_state(root, path)
    observations = state.setdefault('sources', {}).setdefault(repository, {})
    errors = []
    for url, entry in frontier.get('archive_index', {}).get('entries', {}).items():
        for identity, capture in entry.get('captures', {}).items():
            try:
                replay_url(url, capture)
                observations.setdefault(identity, {'status': 'pending', 'url': url, 'archive_capture': capture})
            except (ValueError, TypeError) as error:
                errors.append({'url': url, 'error': str(error)})
    # Reinterpret saved metadata after parser/routing changes without requiring
    # another replay acquisition or waiting for the ordinary refresh interval.
    for row in observations.values():
        if row.get('status') == 'preserved' and row.get('manifest'):
            remember_manifest(frontier, row['manifest'])
    atomic_json(path, state)
    queue = select(list(observations), observations, key=lambda identity: identity, limit=limit,
                   state=state, queue=repository)
    attempted = preserved = 0
    work.mkdir(parents=True, exist_ok=True)
    for identity in queue:
        prior = observations[identity]
        manifest = prior.get('manifest')
        try:
            remote = verified_asset(repository, manifest['release_tag'], document_name(manifest),
                                    manifest['bytes'], manifest['sha256']) if manifest else None
            if remote is None:
                if paused:
                    continue
                attempted += 1
                with tempfile.TemporaryDirectory(dir=work, prefix='manifest-') as directory:
                    with open_capture(prior['url'], prior['archive_capture']) as response:
                        data = response.read(2_000_001)
                        length = response.headers.get('Content-Length')
                        if len(data) > 2_000_000 or (length is not None and int(length) != len(data)):
                            raise ValueError('manifest exceeds 2 MB or has a truncated Content-Length')
                        fetched_from = response.geturl()
                    body = Path(directory)/'capture.upm'
                    body.write_bytes(data)
                    metadata, candidates, parse_error = {}, [], None
                    try:
                        metadata, candidates = parse_manifest(body, prior['url'])
                    except Exception as error:
                        parse_error = str(error)
                    # Preserve undecodable snapshots too; their discovery remains incomplete.
                    manifest = {'url': prior['url'], 'archive_capture': prior['archive_capture'],
                        'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                        'metadata': metadata, 'candidates': [asdict(row) for row in candidates],
                        'parse_error': parse_error, 'fetched_from': fetched_from,
                        'provenance': 'independent-public-archive-replay',
                        'release_tag': 'manifest-history-'+prior['archive_capture']['timestamp'][:8]}
                    prior['manifest'] = manifest
                    atomic_json(path, state)
                    asset = body.with_name(document_name(manifest))
                    body.rename(asset)
                    ensure_release(repository, manifest['release_tag'], 'Sonos historical manifests',
                                   'Exact independently discovered public update manifest captures.')
                    remote = publish_verified(repository, manifest['release_tag'], asset,
                                              manifest['bytes'], manifest['sha256'])
            proof = publication_receipt(repository, manifest['release_tag'], document_name(manifest), manifest, remote)
            register_document(root/'data/catalog.json', manifest, proof)
            remember_manifest(frontier, manifest)
            observations[identity] = {**prior, **checkpoint(prior, success=True, success_hours=168), 'status': 'preserved'}
            preserved += 1
        except Exception as error:
            observations[identity] = {**prior, **checkpoint(prior), 'status': 'failed', 'error': str(error)}
            paused = paused or bool(getattr(error, 'pause_replay', False))
            if isinstance(error, urllib.error.HTTPError):
                paused = paused or error.code in {403, 429}
                error.close()
        atomic_json(path, state)
    errors.extend({'url': row['url'], 'error': row['error']} for row in observations.values() if row['status'] == 'failed')
    errors.extend({'url': row['url'], 'error': row['manifest'].get('parse_error') or 'manifest has parser warnings'}
                  for row in observations.values() if row.get('manifest') and
                  (row['manifest'].get('parse_error') or row['manifest']['metadata'].get('warnings')))
    errors.extend(link for row in observations.values() for link in
                  row.get('manifest', {}).get('metadata', {}).get('unresolved_links', []))
    return {'attempted': attempted, 'preserved': preserved, 'errors': errors,
            'pending': sum(row['status'] != 'preserved' for row in observations.values()), 'paused': paused}


def archive_history(root, work, repository, *, limit=8, manifest_limit=8):
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in (limit, manifest_limit)):
        raise ValueError('historical acquisition limit must be positive')
    frontier_path = root/'data/discovery/firmware-frontier.json'
    frontier = json.loads(frontier_path.read_text()) if frontier_path.exists() else {}
    index = frontier.get('archive_index', {})
    paused = any(row.get('http_status') in {403, 429} and not due(row) for row in index.get('errors', []))
    manifests = archive_manifests(root, work, repository, frontier, limit=manifest_limit, paused=paused)
    atomic_json(frontier_path, frontier)
    state_path = root/'data/discovery/historical-firmware-acquisition.json'
    previous = acquisition_state(root, state_path).get('sources', {}).get(repository, {})
    candidates = {identity: row['candidate'] for identity, row in previous.items() if row.get('candidate')}
    contexts = {row['url']: row for row in frontier.get('candidates', [])}
    errors = []
    for url, artifact in index.get('artifacts', {}).items():
        if not official_firmware_url(url):
            errors.append({'url': url, 'error': 'indexed replay uses an unsupported firmware URL'})
            continue
        for identity, capture in artifact.get('captures', {}).items():
            try:
                replay_url(url, capture)
                model = artifact.get('package_model')
                if model is None:
                    model = contexts.get(url, {}).get('package_model')
                row = Candidate(artifact.get('version', 'unknown'), model, url,
                                'public-archive-replay', url.split('?', 1)[0].rsplit('/', 1)[-1])
                source_id = 'wayback:'+identity
                candidates[source_id] = {**asdict(row), 'acquisition_id': source_id, 'available': True,
                    'archive_capture': capture, 'archive_captures': {identity: capture}}
            except (ValueError, TypeError) as error:
                errors.append({'url': url, 'capture_id': identity, 'error': str(error)})
    rows = sorted(candidates.values(), key=lambda row: (row['archive_capture']['timestamp'], row['url'], row['acquisition_id']))
    paused = manifests.pop('paused')
    result = archive_candidates(root, repository, rows, work, limit=limit, state_path=state_path,
                                paused_hosts={'web.archive.org'} if paused else ())
    result['errors'].extend(errors)
    result['manifests'] = manifests
    if paused:
        result['errors'].append({'source': 'web.archive.org', 'error': 'replay deferred after an archive access challenge'})
    result['status'] = 'partial' if result['pending'] or result['errors'] or manifests['pending'] or manifests['errors'] else 'ok'
    return result
