"""Rate-limited exploration of synthetic Sonos update-metadata profiles."""

from __future__ import annotations

from copy import deepcopy

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

PROFILE_FIELDS = ("cmaj", "cmin", "cbld", "subm", "rev", "reg")
BASELINE = {"cmaj": 4, "cmin": 1, "cbld": 1, "subm": 100, "rev": 1, "reg": 2}
RELEASE_NOTES_URL = 'https://support.sonos.com/en-us/article/release-notes-sonos-system-updates'


def capture_response(data, evidence, responses):
    """Retain exact UPS observations, including traceable parse failures."""
    if len(data) > 2_000_000:
        raise ValueError('UPS metadata exceeds 2 MB')
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
        return response.read(2_000_001)


def discover_version_hints(previous, *, fetch=_fetch_version_hints):
    """Observed support-page versions guide queries, never guessed campaign URLs."""
    if previous and not due(previous):
        return previous
    try:
        body = fetch()
        if not body or len(body) > 2_000_000:
            raise ValueError('release-note response is empty or exceeds 2 MB')
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
    return dict(zip(PROFILE_FIELDS, coordinates))


def build_profiles(catalog: dict, limit: int = 1200) -> list[dict[str, int]]:
    """Preview a bounded prefix of the continuing profile search."""
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 0:
        raise ValueError('profile preview limit must be a nonnegative integer')
    profiles = {_profile_key(profile): profile for profile in _version_profiles(catalog)} if limit else {}
    index = 1
    while len(profiles) < limit:
        profile = _profile_at(index)
        profiles.setdefault(_profile_key(profile), profile)
        index += 1
    return list(profiles.values())[:limit]


def fetch_profile(profile: dict[str, int], timeout: float = 20) -> bytes:
    return fetch_update_metadata(UpdateProfile(*(profile[field] for field in PROFILE_FIELDS)), timeout)[0]


def discover_profiles(previous, evidence, catalog, *, limit=8, count=1200, deadline=None, fetch=fetch_profile):
    """Resume bounded windows over an open-ended profile search."""
    if (not isinstance(limit, int) or isinstance(limit, bool) or not isinstance(count, int) or
        isinstance(count, bool) or not 0 <= limit <= count <= 10000):
        raise ValueError('metadata profile budgets must satisfy 0 <= limit <= count <= 10000')
    scheduling = {'retry_rotation': deepcopy(previous.get('retry_rotation', {}))}
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
    next_lane = exploration.get('next_fresh_lane', 0)
    if next_lane not in (0, 1):
        raise ValueError('metadata profile lane must be zero or one')
    seeds = _version_profiles(catalog) if limit else []
    if limit and not any(entry['kind'] == 'search' for entry in pending.values()):
        for index in range(cursor, cursor+count):
            profile = _profile_at(index)
            if _profile_key(profile) not in observations:
                pending[_profile_key(profile)] = {'profile': profile, 'kind': 'search'}
        cursor += count
    for profile in seeds:
        if _profile_key(profile) not in observations:
            pending.setdefault(_profile_key(profile), {'profile': profile, 'kind': 'hint'})
    baseline = _profile_key(BASELINE)
    ordered = {baseline: pending[baseline]['profile']} if baseline in pending else {}
    lanes = [[key for key, entry in pending.items() if key != baseline and entry['kind'] == kind]
             for kind in ('hint', 'search')]
    for offset in range(max(map(len, lanes))):
        for lane in (next_lane, 1-next_lane):
            if offset < len(lanes[lane]):
                key = lanes[lane][offset]
                ordered[key] = pending[key]['profile']
    ordered.update({key: row['profile'] for key, row in observations.items()})
    source_backoff = dict(previous.get('source_backoff', {}))
    paused = bool(source_backoff) and not due(source_backoff)
    queue = [] if paused else select(list(ordered.values()), observations, key=_profile_key, limit=limit,
                                    state=scheduling, queue='legacy-ups-profiles')
    errors = [{'source': 'legacy-ups-profile', 'error': 'metadata endpoint is in access-challenge cooldown',
               'http_status': source_backoff.get('http_status')}] if paused else []
    attempted = 0
    for profile in queue:
        if deadline is not None and time.monotonic() >= deadline:
            break
        key = _profile_key(profile)
        if key in pending and key not in observations and key != baseline:
            next_lane = 1 if pending[key]['kind'] == 'hint' else 0
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
            pending.pop(key, None)
            if getattr(error, 'code', None) in {403, 429, 503}:
                source_backoff = {**error_checkpoint(error, source_backoff), 'http_status': error.code, 'error': str(error)}
                break
        pending.pop(key, None)
        time.sleep(.2)
    unchecked = len(pending)
    failed = sum(row.get('status') == 'failed' for row in observations.values())
    return {'profiles': observations, 'responses': responses, 'attempted_this_run': attempted, **scheduling,
            'source_backoff': source_backoff,
            'pending_profiles': list(pending.values()),
            'profile_exploration': {'policy': 'continuing-windows', 'generator_version': 1,
                                    'next_profile_index': cursor, 'window_size': count, 'next_fresh_lane': next_lane},
            'planned_profile_total': len(observations)+unchecked, 'unchecked_profile_total': unchecked,
            'negative_profile_total': sum(row.get('status') == 'not-served' for row in observations.values()),
            'failed_profile_total': failed, 'pending_profile_total': unchecked + failed,
            'status': 'disabled' if not limit else 'partial' if unchecked or failed else 'ok', 'errors': errors}


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
            body = response.read(16*1024*1024+1)
    except urllib.error.HTTPError as error:
        if error.headers.get('X-RateLimit-Remaining') == '0' and not error.headers.get('Retry-After'):
            try:
                error.headers['Retry-After'] = str(max(0, int(error.headers['X-RateLimit-Reset'])-int(time.time())))
            except (ValueError, KeyError, TypeError):
                pass
        raise
    if len(body) > 16*1024*1024:
        raise ValueError('public client report page exceeds the metadata budget')
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
            if not path.lower().endswith('.upm') or '/latest/' in path:
                continue
            # Never copy identifying query parameters out of a device log.
            url = urlunsplit(('https', parsed.hostname, path, '', ''))
            if sonos_url(url):
                roots.add(url)
        except ValueError:
            continue
    return sorted(roots)


def discover_client_reports(previous, *, pages=2, fetch=fetch_client_reports):
    """Search primary client reports, subdividing windows before GitHub's cap."""
    from datetime import datetime, timedelta, timezone
    state = deepcopy(previous)
    queries = ('"manifestURL" sonos in:body is:issue is:public',
               '"manifestRevision" sonos in:body is:issue is:public',
               '"update-firmware.sonos.com" sonos in:body is:issue is:public')
    contexts = state.setdefault('queries', {})
    roots = state.setdefault('roots', {})
    now = datetime.now(timezone.utc).replace(microsecond=0)
    end = now.isoformat().replace('+00:00', 'Z')
    for query in queries:
        record = contexts.setdefault(query, {})
        if not record.get('windows') and due(record):
            record['windows'] = [{'start': record.get('through'), 'end': end, 'page': 1}]
    errors = []
    cursor = state.get('cursor', 0)
    for _ in range(pages):
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
        try:
            document = fetch(query+' updated:'+span, window['page'])
            items, total = document.get('items'), document.get('total_count')
            if not isinstance(items, list) or not isinstance(total, int):
                raise ValueError('invalid public client report search response')
            for item in items:
                repository = item.get('repository_url', '').rsplit('/', 1)[-1].lower()
                if 'manifest' in repository:
                    continue  # Reference manifest collections are not runtime sources.
                body = item.get('body') or ''
                for url in client_manifest_urls(body):
                    proof = {'report_url': item['html_url'], 'updated_at': item.get('updated_at'),
                             'body_sha256': hashlib.sha256(body.encode()).hexdigest()}
                    if proof not in roots.setdefault(url, []):
                        roots[url].append(proof)
            if document.get('incomplete_results'):
                raise ValueError('public client search returned incomplete results; page retained')
            if total > 1000:
                earliest = window['start'] or (items[0]['updated_at'] if items else window['end'])
                start_dt = datetime.fromisoformat(earliest.replace('Z', '+00:00'))
                end_dt = datetime.fromisoformat(window['end'].replace('Z', '+00:00'))
                if end_dt-start_dt <= timedelta(seconds=1):
                    raise ValueError('public client search saturated a timestamp; window retained')
                middle = start_dt+(end_dt-start_dt)//2
                stamp = middle.replace(microsecond=0).isoformat().replace('+00:00', 'Z')
                record['windows'][:1] = [{'start': earliest, 'end': stamp, 'page': 1},
                    {'start': stamp, 'end': window['end'], 'page': 1}]
            elif len(items) < 20 or window['page']*20 >= total:
                record['windows'].pop(0)
                record['through'] = window['end']
            else:
                window['page'] += 1
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
    state.update(cursor=cursor, errors=errors,
                 status='partial' if errors or any(r.get('windows') for r in contexts.values()) else 'ok')
    return state
