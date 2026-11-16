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
from .retry import checkpoint, select, due
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
        return {**previous, **checkpoint(previous), 'status': 'failed',
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
                                    state=scheduling)
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
            observations[key] = {**checkpoint(observations.get(key), negative=negative), 'profile': profile,
                                 'status': 'not-served' if negative else 'failed', 'error': str(error),
                                 'http_status': getattr(error, 'code', None)}
            if captured:
                observations[key]['response_sha256'] = captured['sha256']
            if not negative:
                errors.append({'source': 'legacy-ups-profile', 'profile': profile, 'error': str(error),
                               'http_status': getattr(error, 'code', None)})
            pending.pop(key, None)
            if getattr(error, 'code', None) in {403, 429}:
                source_backoff = {**checkpoint(source_backoff), 'http_status': error.code, 'error': str(error)}
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
