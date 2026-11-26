"""Rate-limited exploration of synthetic Sonos update-metadata profiles."""

from __future__ import annotations

from copy import deepcopy
from collections import deque

import hashlib
import re
import time
import urllib.error
import urllib.request

from .metadata import (
    UPDATE_METADATA_URL,
    parse_update_metadata,
    UpdateProfile,
    fetch_update_metadata,
)
from .retry import checkpoint, select, due, error_checkpoint
from .discovery import USER_AGENT
from .deadline import winding_down

PROFILE_FIELDS = ("cmaj", "cmin", "cbld", "subm", "rev", "reg")
BASELINE = {"cmaj": 4, "cmin": 1, "cbld": 1, "subm": 100, "rev": 1, "reg": 2}
# A diagnostic marker worth searching for in public issues. Each entry becomes
# a body-and-comment query; add a marker here to widen the channel.
CLIENT_REPORT_MARKERS = ('"manifestURL"', '"manifestRevision"',
                         '"update-firmware.sonos.com"', '"update.upm"',
                         '"AvailableSoftwareUpdate"')
RELEASE_NOTES_URL = 'https://support.sonos.com/en-us/article/release-notes-sonos-system-updates'


def capture_response(data, evidence, responses):
    """Retain exact UPS observations, including traceable parse failures."""
    digest = hashlib.sha256(data).hexdigest()
    evidence.mkdir(parents=True, exist_ok=True)
    snapshot = evidence / (digest + '.ups')
    snapshot.write_bytes(data)
    captured = {'sha256': digest, 'bytes': len(data), 'snapshot': snapshot.name,
                'source_url': UPDATE_METADATA_URL, 'records': []}
    responses[digest] = captured
    try:
        captured['records'] = parse_update_metadata(data)
        if not captured['records']:
            raise ValueError('UPS metadata contains no records')
    except ValueError as error:
        captured['parse_error'] = str(error)
    return captured


def _fetch_version_hints():
    request = urllib.request.Request(RELEASE_NOTES_URL, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        return response.read()


def discover_version_hints(previous, *, fetch=_fetch_version_hints):
    """Observed support-page versions guide queries, never guessed campaign URLs."""
    if previous and not due(previous):
        return previous
    try:
        body = fetch()
        if not body:
            raise ValueError('release-note response is empty')
        versions = re.findall(r'\b\d{1,3}\.\d{1,3}-\d{1,10}\b', body.decode('utf-8', errors='replace'))
        return {**checkpoint(previous, success=True, success_hours=6), 'status': 'observed',
                'source_url': RELEASE_NOTES_URL, 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                'versions': sorted(set(previous.get('versions', [])) | set(versions))}
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        return {**previous, **error_checkpoint(error, previous), 'status': 'failed',
                'source_url': RELEASE_NOTES_URL, 'error': str(error)}


def _profile_key(profile: dict[str, int]) -> str:
    return ":".join(str(profile[field]) for field in PROFILE_FIELDS)


def _version_profiles(catalog):
    profiles = {_profile_key(BASELINE): dict(BASELINE)}
    versions = {
        item.get("version")
        for group in (catalog.get("packages", []), catalog.get("evidence", []))
        for item in group
        if item.get("version")
    }
    for version in sorted(versions):
        try:
            major_minor, build = version.split("-", 1)
            major, minor = major_minor.split(".", 1)
            values = {"cmaj": int(major), "cmin": int(minor), "cbld": int(build)}
        except (ValueError, TypeError):
            continue
        for submodel in (0, 1, 100):
            profile = {**BASELINE, **values, 'subm': submodel}
            profiles.setdefault(_profile_key(profile), profile)
    return list(profiles.values())


def _profile_at(index):
    """Interleave unbounded axis probes, build scales, and full combinations."""
    if index == 0:
        return dict(BASELINE)
    value, lane = divmod(index-1, len(PROFILE_FIELDS)+2)
    if lane < len(PROFILE_FIELDS):
        return {**BASELINE, PROFILE_FIELDS[lane]: value}
    if lane == len(PROFILE_FIELDS):
        return {**BASELINE, 'cbld': value*value}
    # Interleaved bits enumerate every finite tuple without materializing a
    # Cartesian product or imposing a maximum on any protocol field.
    coordinates = [0]*len(PROFILE_FIELDS)
    bit = 0
    while value:
        for field in range(len(PROFILE_FIELDS)):
            coordinates[field] |= (value & 1) << bit
            value >>= 1
        bit += 1
    return dict(zip(PROFILE_FIELDS, coordinates, strict=True))


def fetch_profile(profile: dict[str, int], timeout: float = 20) -> bytes:
    return fetch_update_metadata(UpdateProfile(*(profile[field] for field in PROFILE_FIELDS)), timeout)[0]


def discover_profiles(previous, evidence, catalog, *, fetch=fetch_profile, on_capture=None, stop=None):
    """Continue observed and synthetic update profiles until the work deadline."""
    observations = dict(previous.get('profiles', {}))
    responses = dict(previous.get('responses', {}))
    pending = {}
    for entry in previous.get('pending_profiles', []):
        profile = entry.get('profile', entry)
        kind = entry.get('kind', 'search') if 'profile' in entry else 'search'
        if kind not in {'hint', 'search'}:
            raise ValueError('unsupported pending metadata profile kind')
        pending[_profile_key(profile)] = {'profile': profile, 'kind': kind}
    exploration = previous.get('profile_exploration', {})
    cursor = exploration.get('next_profile_index', 0) if exploration.get('generator_version') == 1 else 0
    if not isinstance(cursor, int) or isinstance(cursor, bool) or cursor < 0:
        raise ValueError('metadata profile cursor must be a nonnegative integer')
    for profile in _version_profiles(catalog):
        if _profile_key(profile) not in observations:
            pending.setdefault(_profile_key(profile), {'profile': profile, 'kind': 'hint'})
    source_backoff = dict(previous.get('source_backoff', {}))
    paused = bool(source_backoff) and not due(source_backoff)
    errors = [{'source': 'legacy-ups-profile', 'error': 'metadata endpoint is in access-challenge cooldown',
               'http_status': source_backoff.get('http_status')}] if paused else []
    saved = {key: row['profile'] for key, row in observations.items() if 'profile' in row}
    saved.update({key: entry['profile'] for key, entry in pending.items()})
    queue = deque(select(saved.values(), observations, key=_profile_key) if not paused else [])
    next_saved = exploration.get('next_lane', 0) == 0
    attempted = 0
    while not paused and not winding_down() and (stop is None or not stop.is_set()):
        if queue and next_saved:
            profile = queue.popleft()
        else:
            profile = _profile_at(cursor)
            cursor += 1
            if _profile_key(profile) in observations or _profile_key(profile) in pending:
                continue
        next_saved = not next_saved
        key = _profile_key(profile)
        attempted += 1
        captured = None
        try:
            data = fetch(profile)
            source_backoff = {}
            captured = capture_response(data, evidence, responses)
            if captured.get('parse_error'):
                raise ValueError(captured['parse_error'])
            observations[key] = {**checkpoint(observations.get(key), success=True, success_hours=168),
                                 'profile': profile, 'status': 'observed', 'response_sha256': captured['sha256']}
            if on_capture is not None:
                on_capture(profile, captured)
        except Exception as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            negative = getattr(error, 'code', None) in {404, 410}
            observations[key] = {**error_checkpoint(error, observations.get(key), negative=negative), 'profile': profile,
                                 'status': 'not-served' if negative else 'failed', 'error': str(error),
                                 'http_status': getattr(error, 'code', None)}
            if captured:
                observations[key]['response_sha256'] = captured['sha256']
            if not negative:
                errors.append({'source': 'legacy-ups-profile', 'profile': profile, 'error': str(error),
                               'http_status': getattr(error, 'code', None)})
            if getattr(error, 'code', None) in {403, 429, 503}:
                source_backoff = {**error_checkpoint(error, source_backoff),
                                  'http_status': error.code, 'error': str(error)}
                pending.pop(key, None)
                break
        pending.pop(key, None)
        time.sleep(.2)
    unchecked = len(pending)
    failed = sum(row.get('status') == 'failed' for row in observations.values())
    return {'profiles': observations, 'responses': responses, 'attempted_this_run': attempted,
            'source_backoff': source_backoff, 'pending_profiles': list(pending.values()),
            'profile_exploration': {'policy': 'continuing-search', 'generator_version': 1,
                                    'next_profile_index': cursor, 'next_lane': 0 if next_saved else 1},
            'planned_profile_total': len(observations)+unchecked, 'unchecked_profile_total': unchecked,
            'negative_profile_total': sum(row.get('status') == 'not-served' for row in observations.values()),
            'failed_profile_total': failed, 'pending_profile_total': unchecked + failed,
            'status': 'partial' if unchecked or failed or paused else 'ok', 'errors': errors}


def fetch_client_reports(query, page):
    """Read public client diagnostics, retaining no log bodies or household IDs."""
    import json
    import os
    from urllib.parse import urlencode
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer '+token
    url = 'https://api.github.com/search/issues?'+urlencode({
        'q': query, 'sort': 'updated', 'order': 'asc', 'per_page': 20, 'page': page})
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.headers.get('X-RateLimit-Remaining') == '0' and not error.headers.get('Retry-After'):
            try:
                error.headers['Retry-After'] = str(max(0, int(error.headers['X-RateLimit-Reset'])-int(time.time())))
            except (ValueError, KeyError, TypeError):
                pass
        raise
    return json.loads(body)


def client_manifest_urls(body):
    """Extract only public Sonos update locations from actual client traces."""
    import html
    from urllib.parse import urlsplit, urlunsplit
    from .source_urls import sonos_url
    body = html.unescape(body).replace('\\/', '/')
    if not re.search(r'availableSoftwareUpdate|ZoneGroupTopology|manifestRevision', body, re.I):
        return []
    roots = set()
    for raw in re.findall(r'https?://[^\s"\'<>`\\]+', body):
        try:
            parsed = urlsplit(raw.rstrip('),]}'))
            if not parsed.hostname or parsed.port not in {None, 80, 443}:
                continue
            path = parsed.path
            if '^' in path:
                path = path.split('^', 1)[0].rstrip('/')+'/update.upm'
            elif path.lower().endswith(('.upd', '.exe', '.dmg', '.bin', '.apk')):
                path = path.rsplit('/', 1)[0]+'/update.upm'
            epoch = re.fullmatch(r'update_\d+(?:[.]\w+)?', path.rsplit('/', 1)[-1])
            if not epoch and (not path.lower().endswith('.upm') or '/latest/' in path):
                continue
            # Never copy identifying query parameters out of a device log.
            url = urlunsplit(('https', parsed.hostname, path, '', ''))
            if sonos_url(url):
                roots.add(url)
        except ValueError:
            continue
    return sorted(roots)


def fetch_issue_comments(url, page):
    """Read public issue comments, retaining no bodies or household IDs."""
    import json
    import os
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer '+token
    separator = '&' if '?' in url else '?'
    try:
        with urllib.request.urlopen(urllib.request.Request(
                url+separator+'per_page=100&page='+str(page), headers=headers), timeout=30) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        if error.headers.get('X-RateLimit-Remaining') == '0' and not error.headers.get('Retry-After'):
            try:
                error.headers['Retry-After'] = str(max(0, int(error.headers['X-RateLimit-Reset'])-int(time.time())))
            except (ValueError, KeyError, TypeError):
                pass
        raise
    document = json.loads(body)
    if not isinstance(document, list):
        raise ValueError('invalid public issue comment response')
    return document


def discover_client_reports(previous, *, fetch=fetch_client_reports,
                            fetch_comments=fetch_issue_comments):
    """Search primary client reports, subdividing windows before GitHub's cap."""
    from datetime import datetime, timedelta, timezone
    state = deepcopy(previous)
    queries = tuple(query for marker in CLIENT_REPORT_MARKERS
                    for query in (marker+' sonos in:body,comments is:issue is:public',))
    contexts = state.setdefault('queries', {})
    for query in queries:
        legacy = contexts.pop(query.replace('in:body,comments', 'in:body'), None)
        if query not in contexts and legacy:
            contexts[query] = legacy
    contexts = state['queries']
    roots = state.setdefault('roots', {})
    comment_queue = state.setdefault('comment_queue', {})
    comment_scans = state.setdefault('comment_scans', {})
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = now.isoformat().replace('+00:00', 'Z')
    for query in queries:
        record = contexts.setdefault(query, {})
        if not record.get('windows') and due(record):
            record['windows'] = [{'start': record.get('through'), 'end': end, 'page': 1}]
    errors = []
    cursor = state.get('cursor', 0)
    while not winding_down():
        if state.get('source_backoff') and not due(state['source_backoff']):
            break
        active = [q for q in queries if contexts[q].get('windows') and due(contexts[q])]
        if not active:
            break
        query = active[cursor % len(active)]
        cursor += 1
        record = contexts[query]
        window = record['windows'][0]
        span = ((window['start']+'..') if window['start'] else '<=')+window['end']
        scoped = query+' updated:'+span
        if window.get('created_end'):
            created_span = ((window.get('created_start')+'..') if window.get('created_start') else '<=')+window['created_end']
            scoped += ' created:'+created_span
        def absorb(items):
            for item in items:
                body = item.get('body') or ''
                for url in client_manifest_urls(body):
                    proof = {'report_url': item['html_url'], 'updated_at': item.get('updated_at'),
                             'body_sha256': hashlib.sha256(body.encode()).hexdigest()}
                    if proof not in roots.setdefault(url, []):
                        roots[url].append(proof)
                if item.get('comments') and item.get('comments_url') and item.get('html_url'):
                    key = str(item.get('id') or item['html_url'])
                    entry = comment_queue.get(key)
                    scanned = comment_scans.get(key, {})
                    if (not entry or entry.get('updated_at') != item.get('updated_at')) and \
                            scanned.get('updated_at') != item.get('updated_at'):
                        comment_queue[key] = {'comments_url': item['comments_url'],
                                              'issue_url': item['html_url'],
                                              'updated_at': item.get('updated_at'), 'next_page': 1}
        try:
            document = fetch(scoped, 1)
            items, total = document.get('items'), document.get('total_count')
            if not isinstance(items, list) or not isinstance(total, int):
                raise ValueError('invalid public client report search response')
            absorb(items)
            if document.get('incomplete_results'):
                raise ValueError('public client search returned incomplete results; page retained')
            if total > 20:
                epoch = datetime(1970, 1, 1, tzinfo=timezone.utc)
                start_dt = (datetime.fromisoformat(window['start'].replace('Z', '+00:00'))
                            if window['start'] else epoch)
                end_dt = datetime.fromisoformat(window['end'].replace('Z', '+00:00'))
                if not window.get('created_end') and end_dt-start_dt > timedelta(seconds=1):
                    middle = start_dt+(end_dt-start_dt)//2
                    stamp = middle.replace(microsecond=0).isoformat().replace('+00:00', 'Z')
                    record['windows'][:1] = [{'start': window['start'], 'end': stamp, 'page': 1},
                                            {'start': stamp, 'end': window['end'], 'page': 1}]
                else:
                    created_start = window.get('created_start')
                    left = datetime.fromisoformat(created_start.replace('Z', '+00:00')) if created_start else epoch
                    right_text = window.get('created_end', window['end'])
                    right = datetime.fromisoformat(right_text.replace('Z', '+00:00'))
                    if right-left <= timedelta(seconds=1):
                        seen = {item.get('id') or item.get('html_url') for item in items}
                        complete = len(items) < 20
                        scanned = 1
                        while not complete and not winding_down():
                            scanned += 1
                            extra_document = fetch(scoped, scanned)
                            extra = extra_document.get('items')
                            if not isinstance(extra, list):
                                raise ValueError('invalid public client report search response')
                            fresh = [item for item in extra
                                     if (item.get('id') or item.get('html_url')) not in seen]
                            absorb(fresh)
                            seen.update(item.get('id') or item.get('html_url') for item in extra)
                            if len(extra) < 20 or len(seen) >= total:
                                complete = True
                            elif not fresh:
                                # An unchanging page cannot advance a cursor.
                                # Retain the incomplete time bucket for retry.
                                break
                        if complete:
                            record['windows'].pop(0)
                            record['through'] = window['end']
                        else:
                            record['windows'].append({**record['windows'].pop(0), 'saturated': True})
                            errors.append({'source': 'public-client-reports',
                                           'error': 'GitHub search results exceeded its pagination capability; retained for retry'})
                        record.update(checkpoint(record, success=True, success_hours=6))
                        record.pop('error', None)
                        state.pop('source_backoff', None)
                        continue
                    stamp = (left+(right-left)//2).replace(microsecond=0).isoformat().replace('+00:00', 'Z')
                    record['windows'][:1] = [dict(window, created_start=created_start, created_end=stamp, page=1),
                                            dict(window, created_start=stamp, created_end=right_text, page=1)]
            else:
                record['windows'].pop(0)
                record['through'] = window['end']
            record.update(checkpoint(record, success=True, success_hours=0 if record['windows'] else 6))
            record.pop('error', None)
            state.pop('source_backoff', None)
        except Exception as error:
            record.update(error_checkpoint(error, record), error=str(error))
            errors.append({'source': 'public-client-reports', 'error': str(error)})
            if getattr(error, 'code', None) in {403, 429}:
                state['source_backoff'] = {**record, 'windows': []}
            if isinstance(error, urllib.error.HTTPError):
                error.close()
    attempted = set()
    while comment_queue and not winding_down():
        if state.get('source_backoff') and not due(state['source_backoff']):
            break
        keys = [key for key in sorted(comment_queue) if key not in attempted]
        if not keys:
            break
        key = keys[0]
        entry = comment_queue[key]
        try:
            page = entry.get('next_page', 1)
            document = fetch_comments(entry['comments_url'], page)
            for comment in document:
                body = comment.get('body') or ''
                for url in client_manifest_urls(body):
                    proof = {'report_url': comment.get('html_url', entry['issue_url']),
                             'updated_at': comment.get('updated_at'),
                             'body_sha256': hashlib.sha256(body.encode()).hexdigest()}
                    if proof not in roots.setdefault(url, []):
                        roots[url].append(proof)
            if len(document) >= 100:
                entry['next_page'] = page+1
            else:
                comment_scans[key] = {'updated_at': entry.get('updated_at'), 'status': 'scanned',
                                      'issue_url': entry['issue_url']}
                comment_queue.pop(key, None)
            state.pop('source_backoff', None)
            if key in comment_queue:
                continue
        except Exception as error:
            errors.append({'source': 'public-client-comments', 'error': str(error),
                           'issue_url': entry.get('issue_url')})
            if getattr(error, 'code', None) in {403, 429}:
                state['source_backoff'] = {**error_checkpoint(error, {}), 'http_status': error.code,
                                           'error': str(error), 'windows': []}
                if isinstance(error, urllib.error.HTTPError):
                    error.close()
                break
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            entry['error'] = str(error)
        attempted.add(key)
    state.update(cursor=cursor, errors=errors,
                 status='partial' if errors or comment_queue or
                 any(r.get('windows') for r in contexts.values()) else 'ok')
    return state
