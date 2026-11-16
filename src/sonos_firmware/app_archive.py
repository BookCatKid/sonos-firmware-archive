"""Shared, resumable acquisition and publication of discovered app installers."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import urllib.error
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable
from urllib.parse import urlsplit

from .releases import ensure_release, publish_verified, receipt_matches_repository, verified_asset
from .retry import checkpoint, select
from .storage import atomic_json


@dataclass(frozen=True)
class AppSource:
    """Acquire writes one destination and returns its public fetch URL."""
    acquire: Callable[[dict, Path], str]
    release_for: Callable[[dict], tuple[str, str]]
    notes: str
    default_platform: str = ''
    refresh_hours: float = 168
    current_refresh_hours: float = 6


def receipt_path(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve()))
    except ValueError:
        return str(path)


def file_digest(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def revision(candidate):
    return {key: candidate[key] for key in ('version', 'expected_sha256', 'etag', 'content_length',
                                           'http_status', 'wayback_timestamp', 'wayback_digest') if candidate.get(key) is not None}


def acquisition_id(candidate):
    return candidate.get('acquisition_id') or candidate['url']


def preserve(source: AppSource, discovery: Path, receipt: Path, repository: str, *, root: Path, limit=None,
             paused_hosts=()):
    if limit is not None and limit <= 0:
        raise ValueError('acquisition limit must be positive')
    if source.refresh_hours <= 0 or source.current_refresh_hours <= 0:
        raise ValueError('refresh intervals must be positive')
    document = json.loads(receipt.read_text()) if receipt.exists() else {'schema_version': 1, 'artifacts': [], 'gaps': []}
    records = document['artifacts']
    gaps = {acquisition_id(row): row for row in document.get('gaps', [])}
    scoped = document.setdefault('source_observations', {})
    observations = scoped.setdefault(repository, {})
    by_digest = {(row['family'], row['platform'], row['sha256']): row for row in records
                 if receipt_matches_repository(row, repository)}
    candidates = {url: row['candidate'] for url, row in observations.items() if row.get('candidate')}
    for row in records:
        for url in row.get('source_urls', []):
            fallback = {key: row[key] for key in ('family', 'platform', 'version', 'sources',
                                                  'wayback_timestamp', 'wayback_digest') if key in row}
            fallback['url'] = url
            contexts = [candidate for candidate in row.get('acquisitions', []) if candidate['url'] == url] or [fallback]
            for candidate in contexts:
                identity = acquisition_id(candidate)
                candidates.setdefault(identity, candidate)
                if receipt_matches_repository(row, repository):
                    observations.setdefault(identity, {'candidate': candidate, 'status': 'needs-refresh',
                                              'checked_at': row.get('acquired_at', document.get('updated', ''))})
    for url, gap in gaps.items():
        candidate = {**gap, 'platform': gap.get('platform', source.default_platform)}
        candidates.setdefault(url, candidate)
        observations.setdefault(url, {'candidate': candidate, 'status': 'failed',
                                      'checked_at': gap.get('checked_at', ''), 'error': gap.get('reason')})
    # Never drop an earlier lead when a partial discovery report omits it.
    incoming = json.loads(discovery.read_text()) if discovery.exists() else {
        'status': 'unavailable', 'errors': ['discovery file unavailable; resuming saved observations']}
    discovery_errors = incoming.get('errors', [])
    incomplete = incoming.get('status') in {'partial', 'incomplete', 'error', 'blocked', 'failed', 'unavailable'}
    if incomplete and not discovery_errors:
        discovery_errors = [incoming.get('error') or 'discovery reported incomplete work']
    candidates.update({acquisition_id(row): row for row in incoming.get('candidates', [])})
    for url, candidate in candidates.items():
        observations.setdefault(url, {'status': 'pending'})['candidate'] = candidate
    eligibility = {}
    for url, observation in observations.items():
        if observation.get('checked_at') is not None and observation['status'] != 'pending':
            eligibility[url] = dict(observation)
            if observation['status'] == 'preserved' and observation['candidate'].get('current'):
                checked = datetime.fromisoformat(observation['checked_at'].replace('Z', '+00:00'))
                earlier = (checked + timedelta(hours=source.current_refresh_hours)).isoformat()
                eligibility[url]['next_attempt_at'] = min(earlier, observation.get('next_attempt_at', earlier))
            old = observation.get('revision', {})
            if 'revision' in observation and any(old.get(key) != value for key, value in revision(observation['candidate']).items()):
                # New evidence of changed bytes is new work, even at a known URL.
                eligibility.pop(url)
                observation['status'] = 'needs-refresh'
    ready = set()
    attempted = added = 0

    def save():
        errors = [{'phase': 'discovery', 'error': error} for error in discovery_errors]
        errors.extend({'url': row['candidate']['url'], 'error': row.get('error'),
                       **({'acquisition_id': identity} if identity != row['candidate']['url'] else {})}
                      for identity, row in observations.items() if row['status'] == 'failed')
        outstanding = [row for row in document.get('unpublished_captures', []) if row.get('repository') == repository]
        errors.extend({'url': row['candidate']['url'], 'sha256': row['sha256'], 'error': 'capture awaiting publication reconciliation'} for row in outstanding)
        pending = sum(row['status'] in ('pending', 'needs-refresh') for row in observations.values())
        document.update(updated=datetime.now(timezone.utc).isoformat(), repository=repository,
                        discovery=receipt_path(discovery, root), artifact_total=len(records),
                        artifacts=sorted(records, key=lambda row: (row['family'], row['platform'], row['version'], row['sha256'])),
                        gap_total=len(gaps), gaps=sorted(gaps.values(), key=lambda row: row['url']),
                        status='partial' if errors or pending else 'ok', errors=errors, pending_total=pending,
                        discovery_status=incoming.get('status', 'ok'),
                        attempted_this_run=attempted, added_this_run=added)
        atomic_json(receipt, document)

    def register(capture, remote):
        nonlocal added
        captured = capture['candidate']
        identity = (captured['family'], captured['platform'], capture['sha256'])
        known = by_digest.get(identity)
        if not known:
            known = {key: captured[key] for key in ('family', 'platform', 'version',
                                                  'wayback_timestamp', 'wayback_digest') if key in captured}
            known.update(original_filename=capture['original_filename'], bytes=capture['bytes'],
                         sha256=capture['sha256'], source_urls=[], sources=[],
                         release_tag=capture['release_tag'], release_asset=capture['release_asset'],
                         acquired_at=capture['acquired_at'])
            records.append(known)
            by_digest[identity] = known
            added += 1
        known.update(release_url=remote.get('browser_download_url') or remote['url'], fetched_from=capture['fetched_from'])
        known['source_urls'] = sorted(set(known['source_urls']) | {captured['url']})
        known['sources'] = sorted(set(known.get('sources', [])) | set(captured.get('sources', [])))
        known['versions'] = sorted(set(known.get('versions', [known['version']])) | {captured['version']})
        contexts = {acquisition_id(row): row for row in known.get('acquisitions', [])}
        contexts[acquisition_id(captured)] = captured
        known['acquisitions'] = list(contexts.values())
        document['unpublished_captures'] = [row for row in document.get('unpublished_captures', [])
                                          if not (row.get('repository') == repository and row['sha256'] == capture['sha256']
                                                  and acquisition_id(row['candidate']) == acquisition_id(captured))]
        return known

    def finish(capture, candidate, previous, remote):
        known = register(capture, remote)
        captured = capture['candidate']
        checked_revision = revision(captured)
        changed = any(checked_revision.get(key) != value for key, value in revision(candidate).items())
        hours = source.current_refresh_hours if candidate.get('current') else source.refresh_hours
        observations[acquisition_id(candidate)] = {**checkpoint(previous, success=True, success_hours=hours), 'candidate': candidate,
                                         'revision': checked_revision,
                                         'status': 'needs-refresh' if changed else 'preserved', 'sha256': capture['sha256'],
                                         'release_asset': known['release_asset']}
        gaps.pop(acquisition_id(candidate), None)

    save()
    outstanding = [row for row in document.get('unpublished_captures', []) if row.get('repository') == repository]
    blocked = set(paused_hosts)
    def selectable(candidate):
        host = 'web.archive.org' if candidate.get('archive_capture') is not None else urlsplit(candidate['url']).hostname
        previous = observations[acquisition_id(candidate)]
        return host not in blocked or previous.get('pending_publication') or previous.get('status') == 'preserved'
    key = lambda row: (acquisition_id(row['candidate']), row['sha256'], row['release_asset'])
    jobs = [{'kind': 'recovery', 'row': row, 'key': ('recovery', *key(row))} for row in outstanding]
    jobs.extend({'kind': 'source', 'row': row, 'key': ('source', acquisition_id(row))}
                for row in candidates.values() if selectable(row))
    jobs_due = {('recovery', *key(row)): row for row in outstanding}
    jobs_due.update({('source', identity): row for identity, row in eligibility.items()})
    chosen = select(jobs, jobs_due, key=lambda job: job['key'], limit=limit or len(jobs),
                    state=document, queue=repository)
    recoveries = [job['row'] for job in chosen if job['kind'] == 'recovery']
    queue = [job['row'] for job in chosen if job['kind'] == 'source']
    for capture in recoveries:
        attempted += 1
        try:
            known = by_digest.get((capture['candidate']['family'], capture['candidate']['platform'], capture['sha256']))
            tag, asset = (known['release_tag'], known['release_asset']) if known else (capture['release_tag'], capture['release_asset'])
            remote = verified_asset(repository, tag, asset, capture['bytes'], capture['sha256'])
            if remote:
                register(capture, remote)
            else:
                capture.update(checkpoint(capture))
        except Exception as error:
            capture.update(checkpoint(capture), error=str(error))
            if isinstance(error, urllib.error.HTTPError):
                error.close()
        save()
    with tempfile.TemporaryDirectory(prefix='sonos-app-archive-') as temporary:
        directory = Path(temporary)
        for candidate in queue:
            url = candidate['url']
            identity = acquisition_id(candidate)
            previous = observations[identity]
            pending = previous.get('pending_publication')
            archived = candidate.get('archive_capture') is not None
            host = 'web.archive.org' if archived else urlsplit(url).hostname
            if host in blocked and not pending and previous.get('status') != 'preserved':
                continue
            attempted += 1
            try:
                if pending:
                    remote = verified_asset(repository, pending['release_tag'], pending['release_asset'], pending['bytes'], pending['sha256'])
                    if remote:
                        finish(pending, candidate, previous, remote)
                        save()
                        continue
                if archived and previous.get('status') == 'preserved':
                    known = by_digest.get((candidate['family'], candidate['platform'], previous.get('sha256')))
                    if known and verified_asset(repository, known['release_tag'], known['release_asset'], known['bytes'], known['sha256']):
                        observations[acquisition_id(candidate)] = {**previous, **checkpoint(previous, success=True,
                                                                                       success_hours=source.refresh_hours)}
                        save()
                        continue
                if host in blocked:
                    raise ValueError('source host is paused; exact capture remains queued')
                with tempfile.TemporaryDirectory(dir=directory) as capture:
                    original = Path(urlsplit(url).path).name
                    if not original:
                        raise ValueError('installer URL has no filename')
                    path = Path(capture) / original
                    fetched_from = source.acquire(candidate, path)
                    if not isinstance(fetched_from, str) or not fetched_from:
                        raise ValueError('acquisition did not return its source URL')
                    size, digest = path.stat().st_size, file_digest(path)
                    if size <= 0:
                        raise ValueError('installer capture is empty')
                    if candidate.get('expected_sha256') and candidate['expected_sha256'].lower() != digest:
                        raise ValueError('installer SHA-256 does not match the discovery checksum')
                    identity = (candidate['family'], candidate['platform'], digest)
                    known = by_digest.get(identity)
                    tag, title = source.release_for(candidate)
                    if known:
                        tag = known['release_tag']
                        upload = path.with_name(known['release_asset'])
                    else:
                        upload = path.with_name(re.sub(r'[^A-Za-z0-9._-]+', '_', path.stem) + '--' + digest + path.suffix.lower())
                    if pending and pending['sha256'] != digest:
                        document.setdefault('unpublished_captures', []).append({**pending, **checkpoint()})
                    pending = {'repository': repository, 'candidate': candidate, 'original_filename': original, 'bytes': size, 'sha256': digest,
                               'release_tag': tag, 'release_asset': upload.name, 'fetched_from': fetched_from,
                               'acquired_at': datetime.now(timezone.utc).isoformat()}
                    observations[acquisition_id(candidate)]['pending_publication'] = pending
                    save()
                    if tag not in ready:
                        ensure_release(repository, tag, title, source.notes)
                        ready.add(tag)
                    path.rename(upload)
                    remote = publish_verified(repository, tag, upload, size, digest)
                    finish(pending, candidate, previous, remote)
            except Exception as error:
                if getattr(error, 'pause_replay', False) or getattr(error, 'code', None) in {403, 429}:
                    blocked.add(getattr(error, 'pause_host', host))
                identity = acquisition_id(candidate)
                observations[identity] = {**checkpoint(previous, negative=isinstance(error, urllib.error.HTTPError) and error.code == 404),
                                     'candidate': candidate, 'revision': {**previous.get('revision', {}), **revision(candidate)},
                                     'status': 'failed', 'error': str(error)}
                if pending:
                    observations[identity]['pending_publication'] = pending
                gaps[identity] = {**candidate, 'reason': str(error), 'checked_at': observations[identity]['checked_at']}
                if isinstance(error, urllib.error.HTTPError):
                    error.close()
            save()
    return document
