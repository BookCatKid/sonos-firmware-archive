"""Independent, resumable discovery from public Sonos update protocols."""
from __future__ import annotations

import time
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlencode, urlsplit

from .crawl import crawl_manifests, official
from .discovery import Candidate, probe
from .monitor import candidate_is_preserved
from .firmware_archive import capture_due
from .metadata import fetch_update_metadata, parse_update_metadata


def protocol_roots():
    # Public anonymous controller requests, not release/campaign URLs.
    profiles = ((1, 25, 34), (2, 81, 52))
    return [f'https://{host}/firmware/swgen/{generation}/latest/update.upm?' + urlencode({
        'sonosid': 'Anonymous', 'householdid': 'nohhid', 'cmaj': major, 'cmin': 0, 'cbld': build,
    }) for host in ('update.sonos.com', 'update-stage.sonos.com', 'update-perf.sonos.com', 'update-test.sonos.com')
        for generation, major, build in profiles]


def discover(state: dict, evidence: Path, *, seconds=900, max_manifests=1024, fetch=None, probe_fn=None,
             metadata_fetch=fetch_update_metadata, index_fetch=None):
    start = time.monotonic()
    roots = protocol_roots()
    archive = state.get('archive_index', {})
    if index_fetch:
        archive = index_fetch(archive)
        roots.extend(archive['entries'])
    bootstrap_errors = []
    try:
        ups, _ = metadata_fetch()
        evidence.mkdir(parents=True, exist_ok=True)
        (evidence / 'legacy.ups').write_bytes(ups)
        for record in parse_update_metadata(ups):
            url = record.get('uri', '').replace('http://', 'https://', 1)
            if record['type'] == 14 and official(url):
                roots.append(url)
            elif record['type'] == 7 and official(url):
                roots.append(url.rsplit('/', 1)[0] + '/update.upm')
    except Exception as error:
        bootstrap_errors.append({'source': 'legacy-ups', 'error': str(error)})
    # Resume our own observed frontier; never read a third-party URL inventory.
    pending = state.get('pending_manifest_urls', [])
    known_urls = [m['url'] for m in state.get('manifests', [])]
    initial = [{'url': url} for url in dict.fromkeys(roots + pending + known_urls) if official(url)]
    graph = crawl_manifests({}, initial, [], evidence, max_manifests=max_manifests,
                            deadline=start + seconds * .7, explore_channels=True, fetch=fetch)
    manifests = {(m['url'], m['sha256']): m for m in state.get('manifests', [])}
    for manifest in graph['manifests']:
        key = manifest['url'], manifest['sha256']
        manifests[key] = {**manifests.get(key, {}), **manifest}
    candidates = {c['url']: c for c in state.get('candidates', []) if official(c['url'])}
    candidates.update({c.url: asdict(c) for c in graph.pop('candidates')})
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
        result = (probe_fn or probe)(Candidate(**fields))
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
              'blocked_hosts': sorted(blocked)}
    result['bootstrap_errors'] = bootstrap_errors
    result['archive_index'] = archive
    result['status'] = 'partial' if (
        bootstrap_errors or archive.get('errors') or blocked or deferred or result['unchecked_this_run']
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
            'available_uncatalogued_candidates': [c for c in current if not known(c)],
            'available_refresh_candidates': [c for c in current if known(c) and capture_due(c, observations.get(c['url']))],
            'known_missing_now_available': [], 'preservation_pending': pending, 'errors': errors}
