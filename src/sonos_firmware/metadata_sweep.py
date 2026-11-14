"""Rate-limited exploration of synthetic Sonos update-metadata profiles."""

from __future__ import annotations

import hashlib
import time
import urllib.error

from .metadata import (
    UPDATE_METADATA_URL,
    parse_update_metadata,
    UpdateProfile,
    fetch_update_metadata,
)
from .retry import checkpoint, select

PROFILE_FIELDS = ("cmaj", "cmin", "cbld", "subm", "rev", "reg")
BASELINE = {"cmaj": 4, "cmin": 1, "cbld": 1, "subm": 100, "rev": 1, "reg": 2}


def _profile_key(profile: dict[str, int]) -> str:
    return ":".join(str(profile[field]) for field in PROFILE_FIELDS)


def build_profiles(catalog: dict, limit: int = 1200) -> list[dict[str, int]]:
    """Build a deterministic mix of known-version, axis, and pairwise profiles."""
    profiles: dict[str, dict[str, int]] = {}

    def add(**changes: int) -> None:
        profile = {**BASELINE, **changes}
        profiles.setdefault(_profile_key(profile), profile)

    add()
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
            add(**values, subm=submodel)

    for value in range(128):
        add(cmaj=value)
    for value in range(64):
        add(cmin=value)
    for value in range(256):
        add(subm=value)
    for value in range(32):
        add(rev=value)
        add(reg=value)
    for value in (0, 1, 10, 100, 1000, 10000, 99999):
        add(cbld=value)

    index = 0
    while len(profiles) < limit:
        add(
            cmaj=(index * 37) % 128,
            cmin=(index * 17) % 64,
            cbld=(index * 7919) % 100000,
            subm=(index * 73) % 256,
            rev=(index * 7) % 32,
            reg=(index * 11) % 32,
        )
        index += 1
    return list(profiles.values())[:limit]


def fetch_profile(profile: dict[str, int], timeout: float = 20) -> bytes:
    return fetch_update_metadata(UpdateProfile(*(profile[field] for field in PROFILE_FIELDS)), timeout)[0]


def discover_profiles(previous, evidence, catalog, *, limit=8, count=1200, deadline=None, fetch=fetch_profile):
    """Resume bounded profile exploration with retry timing and retained variants."""
    if (not isinstance(limit, int) or isinstance(limit, bool) or not isinstance(count, int) or
        isinstance(count, bool) or not 0 <= limit <= count <= 10000):
        raise ValueError('metadata profile limits must satisfy 0 <= limit <= count <= 10000')
    observations = dict(previous.get('profiles', {}))
    responses = dict(previous.get('responses', {}))
    profiles = build_profiles(catalog, count) if limit else []
    queue = select(profiles, observations, key=_profile_key, limit=limit)
    errors = []
    attempted = 0
    for profile in queue:
        if deadline is not None and time.monotonic() >= deadline:
            break
        key = _profile_key(profile)
        attempted += 1
        try:
            data = fetch(profile)
            if len(data) > 2_000_000:
                raise ValueError('UPS metadata exceeds 2 MB')
            digest = hashlib.sha256(data).hexdigest()
            evidence.mkdir(parents=True, exist_ok=True)
            snapshot = evidence / (digest + '.ups')
            snapshot.write_bytes(data)
            records = parse_update_metadata(data)
            if not records:
                raise ValueError('UPS metadata contains no records')
            responses[digest] = {'sha256': digest, 'bytes': len(data), 'snapshot': snapshot.name,
                                 'source_url': UPDATE_METADATA_URL, 'records': records}
            observations[key] = {**checkpoint(observations.get(key), success=True, success_hours=168),
                                 'profile': profile, 'status': 'observed', 'response_sha256': digest}
        except Exception as error:
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            observations[key] = {**checkpoint(observations.get(key)), 'profile': profile,
                                 'status': 'failed', 'error': str(error)}
            errors.append({'source': 'legacy-ups-profile', 'profile': profile, 'error': str(error),
                           'http_status': getattr(error, 'code', None)})
            if getattr(error, 'code', None) in {403, 429}:
                break
        time.sleep(.2)
    active = [observations.get(_profile_key(profile), {}) for profile in profiles]
    unchecked = sum(not row for row in active)
    failed = sum(row.get('status') == 'failed' for row in active)
    return {'profiles': observations, 'responses': responses, 'attempted_this_run': attempted,
            'planned_profile_total': len(profiles), 'unchecked_profile_total': unchecked,
            'failed_profile_total': failed, 'pending_profile_total': unchecked + failed,
            'status': 'partial' if unchecked or failed else 'ok', 'errors': errors}
