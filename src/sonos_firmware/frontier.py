"""Independent, resumable discovery from public Sonos update protocols."""
from __future__ import annotations

import time
import math
import hashlib
from dataclasses import asdict
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urlsplit

from .crawl import crawl_manifests, official
from .discovery import Candidate, probe, expand_metadata_base
from .monitor import candidate_is_preserved
from .firmware_archive import capture_due
from .metadata import fetch_update_metadata, parse_update_metadata, UPDATE_METADATA_URL
from .metadata_sweep import discover_profiles


def protocol_roots():
    # Public anonymous controller requests, not release/campaign URLs.
    profiles = ((1, 25, 34), (2, 81, 52))
    return [f'https://{host}/firmware/swgen/{generation}/latest/update.upm?' + urlencode({
        'sonosid': 'Anonymous', 'householdid': 'nohhid', 'cmaj': major, 'cmin': 0, 'cbld': build,
    }) for host in ('update.sonos.com', 'update-stage.sonos.com', 'update-perf.sonos.com', 'update-test.sonos.com')
        for generation, major, build in profiles]


def discover(state: dict, evidence: Path, *, seconds=900, max_manifests=1024, fetch=None, probe_fn=None,
             metadata_fetch=fetch_update_metadata, index_fetch=None, artifact_roots=(), known_artifacts=(),
             profile_limit=0, profile_count=1200, profile_fetch=None, model_max=255):
    start = time.monotonic()
    if not isinstance(model_max, int) or isinstance(model_max, bool) or not 0 <= model_max <= 4095:
        raise ValueError('metadata model maximum must be between 0 and 4095')
    if (not isinstance(profile_limit, int) or isinstance(profile_limit, bool) or
        not isinstance(profile_count, int) or isinstance(profile_count, bool) or
        not 0 <= profile_limit <= profile_count <= 10000):
        raise ValueError('invalid metadata profile limits')
    static = {}
    for root in artifact_roots:
        if not isinstance(root, dict):
            raise ValueError('artifact roots must be objects')
        url, model, hours = root.get('url'), root.get('package_model'), root.get('refresh_hours', 6)
        if (not isinstance(url, str) or not official(url) or
            not urlsplit(url).path.endswith('.upd') or
            not isinstance(model, int) or isinstance(model, bool) or not 0 <= model <= 0xffffffff or
            not isinstance(hours, (int, float)) or isinstance(hours, bool) or not math.isfinite(hours) or hours <= 0):
            raise ValueError('artifact roots require an official UPD URL, model and positive refresh cadence')
        static[url] = {'version': 'unknown', 'package_model': model, 'url': url,
                       'filename': PurePosixPath(urlsplit(url).path).name,
                       'source': 'protocol-static', 'refresh_hours': hours}
    live_roots = protocol_roots()
    roots = list(live_roots)
    roots.extend(row['url'].rsplit('/', 1)[0] + '/update.upm' for row in known_artifacts if official(row['url']))
    roots.extend(url.rsplit('/', 1)[0] + '/update.upm' for url in static)
    archive = state.get('archive_index', {})
    if index_fetch:
        archive = index_fetch(archive)
        roots.extend(archive['entries'])
    bootstrap_errors = []
    metadata = discover_profiles(state.get('metadata_profiles', {}), evidence,
        {'packages': known_artifacts}, limit=profile_limit, count=profile_count,
        deadline=start + seconds * .2, **({'fetch': profile_fetch} if profile_fetch else {}))
    bootstrap_errors.extend(metadata['errors'])
    try:
        if any(row.get('http_status') in {403, 429} for row in metadata['errors']):
            raise ValueError('baseline UPS request deferred after a profile access challenge')
        ups, _ = metadata_fetch()
        if len(ups) > 2_000_000:
            raise ValueError('UPS metadata exceeds 2 MB')
        evidence.mkdir(parents=True, exist_ok=True)
        digest = hashlib.sha256(ups).hexdigest()
        snapshot = evidence / (digest + '.ups')
        snapshot.write_bytes(ups)
        records = parse_update_metadata(ups)
        metadata['responses'][digest] = {'sha256': digest, 'bytes': len(ups), 'snapshot': snapshot.name,
                                         'source_url': UPDATE_METADATA_URL, 'records': records}
    except Exception as error:
        bootstrap_errors.append({'source': 'legacy-ups', 'error': str(error), 'http_status': getattr(error, 'code', None)})
    metadata_candidates = {}
    for response in metadata['responses'].values():
        for record in response['records']:
            url = record.get('uri', '').replace('http://', 'https://', 1)
            if record['type'] == 14 and official(url):
                roots.append(url)
            elif record['type'] == 7 and official(url):
                folder = url.split('^', 1)[0].rstrip('/') if '^' in url else url.rsplit('/', 1)[0]
                roots.append(folder + '/update.upm')
                for candidate in expand_metadata_base(url, range(model_max + 1)):
                    metadata_candidates[candidate.url] = {**asdict(candidate), 'metadata_sha256': response['sha256']}
    # Resume our own observed frontier; never read a third-party URL inventory.
    pending = state.get('pending_manifest_urls', [])
    known_urls = [m['url'] for m in state.get('manifests', [])]
    # Reserve a small rotating slice for current feeds, then resume the FIFO.
    # A one-request budget reserves nothing, so it still drains deferred work.
    cursor = state.get('protocol_cursor', 0)
    reserved = min(len(live_roots), max_manifests // 4)
    priority = [live_roots[(cursor + offset) % len(live_roots)] for offset in range(reserved)]
    cursor = (cursor + reserved) % len(live_roots) if live_roots else 0
    initial = [{'url': url} for url in dict.fromkeys(priority + pending + roots + known_urls) if official(url)]
    paused_hosts = {urlsplit(UPDATE_METADATA_URL).hostname} if any(
        error.get('http_status') in {403, 429} for error in bootstrap_errors) else set()
    paused = [row['url'] for row in initial if urlsplit(row['url']).hostname in paused_hosts]
    initial = [row for row in initial if urlsplit(row['url']).hostname not in paused_hosts]
    graph = crawl_manifests({}, initial, [], evidence, max_manifests=max_manifests,
                            deadline=start + seconds * .7, explore_channels=True, fetch=fetch)
    graph['pending_manifest_urls'].extend(paused)
    graph['blocked_hosts'] = sorted(set(graph.get('blocked_hosts', [])) | paused_hosts)
    graph['blocked'] = bool(graph['blocked_hosts'])
    manifests = {(m['url'], m['sha256']): m for m in state.get('manifests', [])}
    for manifest in graph['manifests']:
        key = manifest['url'], manifest['sha256']
        manifests[key] = {**manifests.get(key, {}), **manifest}
    candidates = {c['url']: c for c in state.get('candidates', []) if official(c['url'])}
    candidates.update({row['url']: row for row in known_artifacts if official(row['url'])})
    candidates.update(metadata_candidates)
    candidates.update({c.url: asdict(c) for c in graph.pop('candidates')})
    candidates.update(static)
    prior_probes = state.get('probes', {})
    checks = {}
    blocked = set(graph.get('blocked_hosts', []))
    # Unchecked endpoints take precedence over repeatedly checking known ones.
    rows = sorted(candidates.values(), key=lambda c: (
        c['url'] in prior_probes, prior_probes.get(c['url'], {}).get('attempt_sequence', 0)))
    sequence = state.get('probe_sequence', 0)
    for row in rows:
        host = urlsplit(row['url']).hostname
        if host in blocked:
            continue
        if time.monotonic() >= start + seconds:
            break
        fields = {key: row[key] for key in Candidate.__dataclass_fields__}
        result = {**row, **(probe_fn or probe)(Candidate(**fields))}
        sequence += 1
        result['attempt_sequence'] = sequence
        checks[row['url']] = result
        if result.get('http_status') in {403, 429}:
            blocked.add(host)
        time.sleep(.1)
    probes = {**prior_probes, **checks}
    deferred = list(dict.fromkeys(graph['pending_manifest_urls'] + [f['url'] for f in graph['failures']
                        if f.get('attempted') and official(f['url'])]))
    result = {'schema_version': 1, 'roots': roots, 'manifests': list(manifests.values()),
              'candidates': list(candidates.values()), 'probes': probes,
              'pending_manifest_urls': deferred, 'graph': graph,
              'checked_this_run': len(checks), 'unchecked_this_run': len(candidates) - len(checks),
              'checked_urls': list(checks),
              'probe_sequence': sequence,
              'protocol_cursor': cursor,
              'blocked_hosts': sorted(blocked)}
    result['bootstrap_errors'] = bootstrap_errors
    result['archive_index'] = archive
    result['metadata_profiles'] = metadata
    result['status'] = 'partial' if (
        bootstrap_errors or metadata['status'] == 'partial' or archive.get('errors') or blocked or deferred or result['unchecked_this_run']
        or any(f['kind'] not in {'not-found', 'out-of-scope'} for f in graph['failures'])
    ) else 'ok'
    return result


def monitor_report(state, catalog, acquisition=None, repository=None):
    # Saved HEAD successes do not authorize new acquisition; pending publications
    # can independently reconcile their already-captured bytes in the worker.
    current = [p for url, p in state['probes'].items() if url in state.get('checked_urls', []) and p.get('available')]
    observations = (acquisition or {}).get('sources', {}).get(repository, {})
    known = lambda row: candidate_is_preserved(row, catalog.get('packages', []))
    errors = state.get('preservation_errors', [])
    pending = state.get('preservation_pending', 0)
    return {'schema_version': 1, 'manifests': state['manifests'],
            'status': 'partial' if errors or pending else state.get('status', 'partial'),
            'metadata_discovery': {key: value for key, value in state.get('metadata_profiles', {}).items()
                                   if key not in {'profiles', 'responses', 'errors'}},
            'available_uncatalogued_candidates': [c for c in current if not known(c)],
            'available_refresh_candidates': [c for c in current if known(c) and capture_due(c, observations.get(c['url']))],
            'known_missing_now_available': [], 'preservation_pending': pending, 'errors': errors}
