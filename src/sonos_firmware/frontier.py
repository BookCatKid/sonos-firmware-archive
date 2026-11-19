"""Independent, resumable discovery from public Sonos update protocols."""
from __future__ import annotations

import time
import math
import urllib.error
from copy import deepcopy
from dataclasses import asdict
from collections import defaultdict, deque
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urlsplit

from .crawl import crawl_manifests, official, discovery_incomplete, landing_url, normalize
from .discovery import Candidate, probe, expand_metadata_base, host_counterpart, parse_manifest
from .firmware_archive import capture_due, candidate_is_preserved
from .metadata import fetch_update_metadata, UPDATE_METADATA_URL
from .metadata_sweep import discover_profiles, capture_response
from .source_urls import advertised_url
from .source_links import distribution_url, distribution_urls
from .retry import checkpoint, select, due


def _distribution_scope(state, evidence):
    """Rebuild scope from protocol edges, including older broad crawl queues."""
    documents, manifests = defaultdict(list), defaultdict(list)
    for row in state.get('documents', []):
        documents[row['url']].append(row)
    for row in state.get('manifests', []):
        manifests[row['url']].append(row)
    reachable = set(state.get('roots', [])) | set(manifests)
    reachable.update(url for url in state.get('pending_manifest_urls', [])
                     if urlsplit(url).path.lower().endswith('.upm'))
    reachable.update(url for url, revisions in documents.items()
                     if any(row.get('metadata', {}).get('link_scope') == 'software-distribution' for row in revisions))
    edges = defaultdict(list)
    for edge in state.get('graph', {}).get('edges', []):
        edges[edge['parent']].append((edge['url'], edge.get('reason')))
    for url, revisions in manifests.items():
        for manifest in revisions:
            metadata = manifest.get('metadata', {})
            reachable.update(normalize(link) for field in ('landing_links', 'document_links', 'manifest_links')
                             for link in metadata.get(field, []))
            if 'document_links' not in metadata:
                snapshot = evidence / Path(manifest.get('snapshot', '')).name
                try:
                    _, items = parse_manifest(snapshot, source_url=url)
                    reachable.update(normalize(c.url) for c in items if landing_url(c.url))
                except (OSError, ValueError, SyntaxError):
                    pass  # Retain recorded links even if an older body cannot be reinterpreted.
    for url, revisions in documents.items():
        for document in revisions:
            edges[url].extend((link, 'landing-page-link') for link in document.get('metadata', {}).get('links', []))
    queue = deque(reachable)
    while queue:
        parent = queue.popleft()
        allowed = None
        revisions = documents.get(parent, [])
        if revisions:
            allowed = set()
        for document in revisions:
            metadata = document.get('metadata', {})
            if metadata.get('link_scope') == 'software-distribution':
                allowed.update(metadata.get('links', []))
            else:
                snapshot = evidence / Path(document.get('snapshot', '')).name
                body = None
                try:
                    body = snapshot.read_bytes()
                except OSError:
                    pass
                if body is None:
                    allowed.update(link for link in metadata.get('links', []) if distribution_url(parent, link))
                else:
                    allowed.update(distribution_urls(parent, body))
        for url, reason in edges[parent]:
            if reason == 'landing-page-link':
                follows = url in allowed if allowed is not None else distribution_url(parent, url)
                if not follows:
                    continue
            if url not in reachable:
                reachable.add(url)
                queue.append(url)
    return reachable


def _configure_namespace(namespaces, base, model_max):
    row = namespaces.setdefault(base, {'next_model': 0})
    if 'initial_model_max' not in row and row.get('status') == 'complete':
        # Old completed ranges waited a week before repeating the same IDs.
        row['next_attempt_at'] = ''
    row['initial_model_max'] = model_max
    row['model_max'] = max(row.get('model_max', model_max), model_max)
    return row


def _add_namespace(namespaces, base, source, model_max, **evidence):
    if not expand_metadata_base(base, [0], advertised_by=evidence.get('advertised_by')):
        return
    row = _configure_namespace(namespaces, base, model_max)
    row.update(source=source, **evidence)
    row.pop('parent_namespace', None)
    peer = host_counterpart(Candidate('', 0, base, source, ''))
    if peer:
        other = _configure_namespace(namespaces, peer.url, model_max)
        other.update(**evidence)
        other.setdefault('source', 'host-counterpart')
        if other['source'] == 'host-counterpart':
            other['parent_namespace'] = base


def _namespace_candidates(namespaces, limit, blocked_hosts, scheduling):
    for row in namespaces.values():
        if row['next_model'] > row['model_max'] and due(row):
            row['model_max'] = 2*row['model_max'] + 1
            row['next_model'] = 0
            row['pass'] = row.get('pass', 0) + 1
    records = {key: row for key, row in namespaces.items() if row.get('status') == 'failed'}
    active = sorted((base for base, row in namespaces.items() if due(row) and
                     urlsplit(base).hostname not in blocked_hosts),
                    key=lambda base: namespaces[base].get('checked_at', ''))
    queue = select(active, records, key=lambda key: key, limit=limit, state=scheduling, queue='inference')
    batch = max(1, limit // len(queue)) if queue else 0
    rows = []
    for base in queue:
        record = namespaces[base]
        models = range(record['next_model'], min(record['next_model'] + batch, record['model_max'] + 1))
        for candidate in expand_metadata_base(base, models, advertised_by=record.get('advertised_by')):
            rows.append({**asdict(candidate), 'source': record['source'], 'inferred_namespace': base,
                         'advance_inference': True,
                         **({'metadata_sha256': record['metadata_sha256']} if record.get('metadata_sha256') else {})})
    return rows


def _advance_namespace(namespaces, row, result):
    record = namespaces.get(row.get('inferred_namespace'))
    if record is None or record['next_model'] != row['package_model']:
        return
    if result.get('available') or result.get('http_status') in {404, 410}:
        record['next_model'] += 1
        complete = record['next_model'] > record['model_max']
        record.update(checkpoint(record, success=True, success_hours=0),
                      status='range-complete' if complete else 'scanning')
        record.pop('error', None)
    else:
        record.update(checkpoint(record), status='failed',
                      error=result.get('error') or f"HTTP {result.get('http_status')}")


def protocol_roots():
    # Public anonymous controller requests, not release/campaign URLs.
    profiles = ((1, 25, 34), (2, 81, 52))
    return [f'https://{host}/firmware/swgen/{generation}/latest/update.upm?' + urlencode({
        'sonosid': 'Anonymous', 'householdid': 'nohhid', 'cmaj': major, 'cmin': 0, 'cbld': build,
    }) for host in ('update.sonos.com', 'update-stage.sonos.com', 'update-perf.sonos.com', 'update-test.sonos.com')
        for generation, major, build in profiles]


def discover(state: dict, evidence: Path, *, seconds=900, max_manifests=1024, fetch=None, probe_fn=None,
             metadata_fetch=fetch_update_metadata, index_fetch=None, artifact_roots=(), known_artifacts=(),
             profile_limit=0, profile_count=1200, profile_fetch=None, model_max=255, version_hints=(), inference_limit=128):
    start = time.monotonic()
    scheduling = {'retry_rotation': deepcopy(state.get('retry_rotation', {}))}
    if not isinstance(model_max, int) or isinstance(model_max, bool) or model_max < 0:
        raise ValueError('initial metadata model maximum must be a nonnegative integer')
    if not isinstance(inference_limit, int) or isinstance(inference_limit, bool) or not 1 <= inference_limit <= 4096:
        raise ValueError('inferred candidate limit must be between 1 and 4096')
    namespaces = {key: dict(row) for key, row in state.get('inferred_namespaces', {}).items()}
    for base in namespaces:
        _configure_namespace(namespaces, base, model_max)
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
            not isinstance(model, int) or isinstance(model, bool) or model < 0 or
            not isinstance(hours, (int, float)) or isinstance(hours, bool) or not math.isfinite(hours) or hours <= 0):
            raise ValueError('artifact roots require an official UPD URL, model and positive refresh cadence')
        static[url] = {'version': 'unknown', 'package_model': model, 'url': url,
                       'filename': PurePosixPath(urlsplit(url).path).name,
                       'source': 'protocol-static', 'refresh_hours': hours}
    live_roots = protocol_roots()
    roots = list(live_roots)
    scoped_urls = _distribution_scope(state, evidence)
    saved_documents = [row for row in state.get('documents', []) if row['url'] in scoped_urls]
    authorities = {url: witness for url, witness in state.get('advertised_sources', {}).items() if url in scoped_urls}
    for row in [*state.get('manifests', []), *saved_documents,
                *state.get('candidates', []), *known_artifacts]:
        if row.get('advertised_by'):
            authorities[row['url']] = row['advertised_by']
    roots.extend(row['url'].rsplit('/', 1)[0] + '/update.upm' for row in known_artifacts if official(row['url']))
    roots.extend(url.rsplit('/', 1)[0] + '/update.upm' for url in static)
    archive = state.get('archive_index', {})
    if index_fetch:
        archive = index_fetch(archive)
        roots.extend(archive['entries'])
        roots.extend(url.rsplit('/', 1)[0] + '/update.upm' for url in archive.get('artifacts', {}) if official(url))
    bootstrap_errors = []
    metadata = discover_profiles(state.get('metadata_profiles', {}), evidence,
        {'packages': known_artifacts, 'evidence': [{'version': version} for version in version_hints]},
        limit=profile_limit, count=profile_count,
        deadline=start + seconds * .2, **({'fetch': profile_fetch} if profile_fetch else {}))
    bootstrap_errors.extend(metadata['errors'])
    try:
        if any(row.get('http_status') in {403, 429} for row in metadata['errors']):
            raise ValueError('baseline UPS request deferred after a profile access challenge')
        ups, _ = metadata_fetch()
        captured = capture_response(ups, evidence, metadata['responses'])
        if captured.get('parse_error'):
            raise ValueError(captured['parse_error'])
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        bootstrap_errors.append({'source': 'legacy-ups', 'error': str(error), 'http_status': getattr(error, 'code', None)})
    for response in metadata['responses'].values():
        for record in response['records']:
            url = record.get('uri', '').replace('http://', 'https://', 1)
            if record['type'] in {7, 14} and not advertised_url(url, UPDATE_METADATA_URL):
                bootstrap_errors.append({'source': 'legacy-ups-uri', 'url': url,
                                         'error': 'UPS URI uses an unsupported host or path'})
                continue
            if record['type'] == 14:
                roots.append(url)
                authorities[url] = UPDATE_METADATA_URL
            elif record['type'] == 7:
                folder = url.split('^', 1)[0].rstrip('/') if '^' in url else url.rsplit('/', 1)[0]
                roots.append(folder + '/update.upm')
                authorities[folder + '/update.upm'] = UPDATE_METADATA_URL
                _add_namespace(namespaces, url, 'metadata-base', model_max, metadata_sha256=response['sha256'],
                               advertised_by=UPDATE_METADATA_URL)
    # Resume our own observed frontier; never read a third-party URL inventory.
    pending = [url for url in state.get('pending_manifest_urls', []) if url in scoped_urls]
    excluded_pending = len(state.get('pending_manifest_urls', []))-len(pending)
    pending.extend(row['url'] for row in state.get('candidates', []) if landing_url(row['url']))
    known_urls = [m['url'] for m in state.get('manifests', [])]
    known_urls.extend(m['url'] for m in saved_documents)
    # Reserve a small rotating slice for current feeds, then resume the FIFO.
    # A one-request budget reserves nothing, so it still drains deferred work.
    cursor = state.get('protocol_cursor', 0)
    reserved = min(len(live_roots), max_manifests // 4)
    priority = [live_roots[(cursor + offset) % len(live_roots)] for offset in range(reserved)]
    cursor = (cursor + reserved) % len(live_roots) if live_roots else 0
    initial = [{'url': url, **({'advertised_by': authorities[url]} if url in authorities else {})}
               for url in dict.fromkeys(priority + pending + roots + known_urls)
               if advertised_url(url, authorities.get(url))]
    paused_hosts = {urlsplit(UPDATE_METADATA_URL).hostname} if any(
        error.get('http_status') in {403, 429} for error in bootstrap_errors) else set()
    observed = {'packages': [{'source_url': row['url'], 'version': row['version']} for row in known_artifacts]}
    graph = crawl_manifests(observed, initial, [], evidence, max_manifests=max_manifests,
                            deadline=start + seconds * .7, explore_channels=True, fetch=fetch, paused_hosts=paused_hosts)
    manifests = {(m['url'], m['sha256']): m for m in state.get('manifests', [])}
    for manifest in graph['manifests']:
        key = manifest['url'], manifest['sha256']
        manifests[key] = {**manifests.get(key, {}), **manifest}
    documents = {(m['url'], m['sha256']): m for m in saved_documents}
    for document in graph.get('documents', []):
        key = document['url'], document['sha256']
        documents[key] = {**documents.get(key, {}), **document}
    candidates = {c['url']: c for c in state.get('candidates', [])
                  if advertised_url(c['url'], c.get('advertised_by')) and not landing_url(c['url']) and
                  (official(c['url']) or c['url'] in scoped_urls or c.get('inferred_namespace') in namespaces)}
    candidates.update({row['url']: row for row in known_artifacts if advertised_url(row['url'], row.get('advertised_by'))})
    candidates.update({c.url: asdict(c) for c in graph.pop('candidates')})
    for url, artifact in archive.get('artifacts', {}).items():
        if official(url):
            candidates[url] = {**artifact, **candidates.get(url, {}), 'archive_captures': artifact.get('captures', {})}
            candidates[url].pop('captures', None)
            if artifact.get('version') != 'unknown':
                base = url.rsplit('/', 1)[0] + '/^' + artifact['version']
                _add_namespace(namespaces, base, 'archive-directory', model_max)
    for directory in graph.get('directories', []):
        base = directory['directory'].rstrip('/') + '/^' + directory['version']
        _add_namespace(namespaces, base, 'observed-directory', model_max)
    for url, row in static.items():
        candidates[url] = {**candidates.get(url, {}), **row}
    for row in list(candidates.values()):
        if row.get('inferred_only'):
            continue
        fields = {key: row.get(key) for key in Candidate.__dataclass_fields__}
        counterpart = host_counterpart(Candidate(**fields))
        if counterpart:
            candidates.setdefault(counterpart.url, {**row, **asdict(counterpart), 'parent_url': row['url']})
    inference = _namespace_candidates(namespaces, inference_limit, graph.get('blocked_hosts', []), scheduling)
    for row in inference:
        prior = candidates.get(row['url'])
        if not prior or prior.get('inferred_only'):
            candidates[row['url']] = {**(prior or {}), **row, 'inferred_only': True}
        else:
            prior.update(inferred_namespace=row['inferred_namespace'], advance_inference=True)
    prior_probes = state.get('probes', {})
    checks = {}
    blocked = set(graph.get('blocked_hosts', []))
    # Unchecked endpoints take precedence over repeatedly checking known ones.
    inference_order = {row['url']: index for index, row in enumerate(inference)}
    rows = sorted(candidates.values(), key=lambda c: (
        c['url'] in prior_probes and not c.get('advance_inference'),
        inference_order.get(c['url'], prior_probes.get(c['url'], {}).get('attempt_sequence', 0))))
    sequence = state.get('probe_sequence', 0)
    for row in rows:
        host = urlsplit(row['url']).hostname
        if host in blocked:
            continue
        namespace = namespaces.get(row.get('inferred_namespace'))
        if row.get('advance_inference') and (namespace['next_model'] != row['package_model'] or not due(namespace)):
            continue
        if time.monotonic() >= start + seconds:
            break
        fields = {key: row.get(key) for key in Candidate.__dataclass_fields__}
        result = {**row, **(probe_fn or probe)(Candidate(**fields))}
        sequence += 1
        result['attempt_sequence'] = sequence
        checks[row['url']] = result
        _advance_namespace(namespaces, row, result)
        if result.get('available'):
            row['discovered_available'] = True
        if result.get('http_status') in {403, 429}:
            blocked.add(host)
        time.sleep(.1)
    retained = {url: {key: value for key, value in row.items() if key != 'advance_inference'}
                for url, row in candidates.items() if not row.get('inferred_only') or row.get('discovered_available')}
    probes = {url: row for url, row in {**prior_probes, **checks}.items() if url in retained}
    inference_pending = sum(max(0, row['model_max'] + 1 - row['next_model']) for row in namespaces.values())
    authorities.update(graph.get('advertised_sources', {}))
    deferred = list(dict.fromkeys(graph['pending_manifest_urls'] + [f['url'] for f in graph['failures']
                        if f.get('attempted') and advertised_url(f['url'], authorities.get(f['url']))]))
    result = {'schema_version': 1, 'roots': roots, 'manifests': list(manifests.values()),
              **scheduling,
              'documents': list(documents.values()),
              'candidates': list(retained.values()), 'probes': probes,
              'inferred_namespaces': namespaces, 'inference_pending': inference_pending,
              'model_exploration': {'policy': 'expanding-passes', 'namespaces': len(namespaces),
                                    'pending_in_current_ranges': inference_pending},
              'pending_manifest_urls': deferred, 'graph': graph, 'advertised_sources': authorities,
              'checked_this_run': len(checks), 'unchecked_this_run': len(candidates) - len(checks),
              'checked_urls': list(checks),
              'probe_sequence': sequence,
              'protocol_cursor': cursor,
              'scope_exclusions': {'documents': len(state.get('documents', []))-len(saved_documents),
                                   'queued_urls': excluded_pending},
              'blocked_hosts': sorted(blocked)}
    result['bootstrap_errors'] = bootstrap_errors
    result['archive_index'] = archive
    result['metadata_profiles'] = metadata
    result['status'] = 'partial' if (
        bootstrap_errors or metadata['status'] == 'partial' or inference_pending or archive.get('errors')
        or archive.get('status') == 'partial' or blocked or deferred or result['unchecked_this_run']
        or discovery_incomplete(graph, graph['manifests'], bool(blocked))
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
    return {'schema_version': 1, 'manifests': state['manifests'], 'documents': state.get('documents', []),
            'status': 'partial' if errors or pending else state.get('status', 'partial'),
            'model_exploration': state.get('model_exploration', {}),
            'metadata_discovery': {key: value for key, value in state.get('metadata_profiles', {}).items()
                                   if key not in {'profiles', 'pending_profiles', 'responses', 'errors'}},
            'archive_index_discovery': {
                'status': state.get('archive_index', {}).get('status', 'not-requested'),
                'manifest_urls': len(state.get('archive_index', {}).get('entries', {})),
                'artifact_urls': len(state.get('archive_index', {}).get('artifacts', {})),
                'pending_hosts': state.get('archive_index', {}).get('pending_hosts', []),
            },
            'available_uncatalogued_candidates': [c for c in current if not known(c)],
            'available_refresh_candidates': [c for c in current if known(c) and capture_due(c, observations.get(c['url']))],
            'known_missing_now_available': [], 'preservation_pending': pending, 'errors': errors}
