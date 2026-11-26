"""Independent, resumable discovery from public Sonos update protocols."""
from __future__ import annotations

import time
import math
import json
import re
import hashlib
import urllib.error
from copy import deepcopy
from dataclasses import asdict
from collections import Counter, defaultdict, deque
from concurrent.futures import ThreadPoolExecutor
from queue import SimpleQueue, Empty
from threading import Event
from pathlib import Path, PurePosixPath
from urllib.parse import urlencode, urlsplit

from .crawl import crawl_manifests, official, discovery_incomplete, landing_url, normalize
from .discovery import Candidate, probe, expand_metadata_base, host_counterpart, parse_manifest
from .firmware_archive import capture_due, candidate_is_preserved
from .metadata import fetch_update_metadata, UPDATE_METADATA_URL
from .metadata_sweep import discover_profiles, capture_response, _profile_at, _version_profiles
from .source_urls import advertised_url
from .source_links import distribution_url, distribution_urls
from .retry import checkpoint, select, due
from .deadline import winding_down


def observed_artifacts(root):
    """Feed first-party URLs found by other targets back into firmware discovery."""
    paths = set((root/'data/apps').glob('*.json'))
    if (root/'config/targets.json').exists():
        from .platform.registry import targets
        for target in targets(root).values():
            if target.provider == 'firmware':
                continue
            for relative in target.metadata:
                path = root/relative
                paths.update(path.rglob('*.json') if path.is_dir() else [path])
    candidates, errors, pivots = {}, [], {}
    def visit(value, source):
        from .installers import installer_pivot_contexts

        if isinstance(value, dict):
            for pivot in installer_pivot_contexts(value):
                pivots[(pivot['build'], pivot['app_version'], pivot['token'])] = pivot
            for key, child in value.items():
                visit(key, source)
                visit(child, source)
        elif isinstance(value, list):
            for child in value:
                visit(child, source)
        elif isinstance(value, str):
            url = normalize(value)
            if not official(url) or not urlsplit(url).path.lower().endswith(('.upd', '.exe', '.dmg', '.apk', '.bin')):
                return
            name = PurePosixPath(urlsplit(url).path).name
            match = re.fullmatch(r'(?:Sonos_)?(\d+\.\d+-\d+)(?:-1-(\d+))?\.(?:upd|exe|dmg|apk|bin)', name, re.I)
            candidates.setdefault(url, {'url': url, 'filename': name,
                'version': match[1] if match else 'unknown',
                'package_model': int(match[2]) if match and match[2] else None,
                'source': 'archive-observation:'+source})
    for path in sorted(paths):
        if not path.is_file() or path.suffix != '.json':
            continue
        try:
            visit(json.loads(path.read_text()), str(path.relative_to(root)))
        except (OSError, ValueError) as error:
            errors.append({'source': str(path.relative_to(root)), 'error': str(error)})
    return {'candidates': list(candidates.values()), 'installer_pivots': list(pivots.values()), 'errors': errors}


def _distribution_scope(state, evidence):
    """Rebuild scope from protocol edges, including older broad crawl queues."""
    documents, manifests = defaultdict(list), defaultdict(list)
    for row in state.get('documents', []):
        documents[row['url']].append(row)
    for row in state.get('manifests', []):
        manifests[row['url']].append(row)
    reachable = set(state.get('roots', [])) | set(manifests)
    reachable.update(url for url in state.get('pending_manifest_urls', [])
                     if urlsplit(url).path.lower().endswith('.upm') or
                     re.fullmatch(r'update_\d+(?:[.]\w+)?', urlsplit(url).path.lower().rsplit('/', 1)[-1]))
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


def _release_version_candidates(observed, build, majors):
    """Observed release versions that suit the build, plus adjacent patches."""
    versions = list(observed)
    allowed = majors.get(build.split('.')[0])
    if allowed:
        versions = [version for version in versions if version.split('.')[0] in allowed] or versions
    candidates = list(versions)
    for version in versions:
        parts = version.split('.')
        try:
            patch = int(parts[-1])
        except ValueError:
            continue
        for delta in (-2, -1, 1, 2):
            if patch + delta < 0:
                continue
            candidate = '.'.join([*parts[:-1], str(patch+delta)])
            if candidate not in candidates:
                candidates.append(candidate)
    return candidates


def _installer_root_stream(state, contexts, observed_urls):
    """Learn native campaign shapes and enumerate their numeric fields fairly.

    Build/token/app-version come from our installer captures. Release shapes
    come from our Sonos observations; neither versions nor URL lists are imported.
    """
    templates = state.setdefault('templates', {})
    saved = state.setdefault('contexts', {})
    jobs = state.setdefault('jobs', {})
    for context in contexts:
        app = context.get('app_version')
        if (not isinstance(context.get('build'), str) or not isinstance(context.get('token'), str) or
                not re.fullmatch(r'\d+[.]\d+-\d+', context['build']) or
                not re.fullmatch(r'[A-Za-z0-9_-]+', context['token']) or
                (app is not None and (not isinstance(app, str) or not re.fullmatch(r'\d+(?:[.]\d+)+', app)))):
            continue
        identity = hashlib.sha256(json.dumps([context['build'], app, context['token']]).encode()).hexdigest()
        saved[identity] = context
    for url in observed_urls:
        if not official(url):
            continue
        parsed = urlsplit(url)
        match = re.search(r'(/firmware/[^/]+)/\d+[.]\d+-\d+-v\d+(?:[.]\d+)+-[A-Za-z0-9_]+-([A-Za-z0-9_-]+)/', parsed.path)
        if not match or not re.search(r'\d', match[2]):
            continue
        parts = re.split(r'(\d+)', match[2])
        shape = [None if part.isdecimal() else part for part in parts]
        prefix = parsed._replace(path=match[1]+'/', query='', fragment='').geturl()
        identity = hashlib.sha256(json.dumps([prefix, shape]).encode()).hexdigest()
        template = templates.setdefault(identity, {'prefix': prefix, 'shape': shape, 'examples': []})
        if match[2] not in template['examples']:
            template['examples'].append(match[2])
        version = re.search(r'-v(\d+(?:[.]\d+)+)-', parsed.path)
        campaign = re.search(r'/(\d+)[.]\d+-\d+-v(\d+(?:[.]\d+)+)-[A-Za-z0-9_]+-([A-Za-z0-9_-]+)/', parsed.path)
        if version:
            release = state.setdefault('versions', [])
            if version[1] not in release:
                release.append(version[1])
            if campaign:
                generation, release_version, suffix = campaign.groups()
                majors = state.setdefault('version_majors', {}).setdefault(generation, [])
                if release_version.split('.')[0] not in majors:
                    majors.append(release_version.split('.')[0])
                pairs = state.setdefault('campaign_pairs', {}).setdefault(generation, [])
                pair = {'version': release_version, 'suffix': suffix}
                if pair not in pairs:
                    pairs.append(pair)
    for context_id in saved:
        for template_id in templates:
            jobs.setdefault(context_id+':'+template_id, {'ordinal': 0, 'last_queued': 0})
    state.setdefault('sequence', 0)
    counts = Counter(template['prefix'] for template in templates.values())
    prefixes = sorted(counts, key=lambda prefix: (-counts[prefix], prefix))
    known = {normalize(url) for url in observed_urls}
    pair_queue = deque()
    if prefixes:
        for context in saved.values():
            if context.get('app_version'):
                continue
            pairs = state.get('campaign_pairs', {}).get(context['build'].split('.')[0], [])
            for pair in pairs:
                candidate = (prefixes[0]+context['build']+'-v'+pair['version']+'-'+
                             context['token']+'-'+pair['suffix']+'/update.upm')
                if candidate not in known:
                    pair_queue.append(candidate)
    step = 0
    while (pair_queue or jobs) and not winding_down():
        step += 1
        if pair_queue and (step % 2 or not jobs):
            candidate = pair_queue.popleft()
            if candidate not in known:
                known.add(candidate)
                yield candidate
            continue
        if not jobs:
            break
        identity = min(jobs, key=lambda key: (jobs[key]['last_queued'], key))
        context_id, template_id = identity.split(':')
        job, context, template = jobs[identity], saved[context_id], templates[template_id]
        versions = ([context['app_version']] if context.get('app_version')
                    else _release_version_candidates(state.get('versions', []), context['build'],
                                                     state.get('version_majors', {})))
        if not versions:
            state['sequence'] = state.setdefault('sequence', 0)+1
            job['last_queued'] = state['sequence']
            jobs.pop(identity)
            continue
        # Each version is combined with every observed suffix before a wider
        # suffix range is generated.
        examples = template['examples']
        index = job['ordinal']
        job['ordinal'] += 1
        width = len(versions)
        observed_span = width*max(1, len(examples))
        if index < observed_span:
            version = versions[index % width]
            suffix = examples[(index//width) % len(examples)]
        else:
            version = versions[(index-observed_span) % width]
            # Bit interleaving visits every finite tuple, including compound
            # RC/service-pack shapes and zero ordinals, with no fixed maximum.
            count = template['shape'].count(None)
            coordinates, value, bit = [0]*count, (index-observed_span)//width, 0
            while value:
                for i in range(count):
                    coordinates[i] |= (value & 1) << bit
                    value >>= 1
                bit += 1
            numbers = iter(coordinates)
            suffix = ''.join(str(next(numbers)) if p is None else p for p in template['shape'])
        candidate = template['prefix']+context['build']+'-v'+version+'-'+context['token']+'-'+suffix+'/update.upm'
        state['sequence'] += 1
        job['last_queued'] = state['sequence']
        if candidate not in known:
            known.add(candidate)
            yield candidate



def installer_manifest_roots(previous, contexts, observed_urls):
    """Drain installer-derived candidates, without an item or time allocation."""
    state = deepcopy(previous)
    return state, list(_installer_root_stream(state, contexts, observed_urls))


def _configure_namespace(namespaces, base):
    row = namespaces.setdefault(base, {'next_model': 0})
    if row.get('status') in {'complete', 'range-complete'}:
        row.pop('next_attempt_at', None)
    for field in ('model_max', 'initial_model_max', 'pass'):
        row.pop(field, None)
    return row


def _add_namespace(namespaces, base, source, **evidence):
    if not expand_metadata_base(base, [0], advertised_by=evidence.get('advertised_by')):
        return
    row = _configure_namespace(namespaces, base)
    row.update(source=source, **evidence)
    row.pop('parent_namespace', None)
    peer = host_counterpart(Candidate('', 0, base, source, ''))
    if peer:
        other = _configure_namespace(namespaces, peer.url)
        other.update(**evidence)
        other.setdefault('source', 'host-counterpart')
        if other['source'] == 'host-counterpart':
            other['parent_namespace'] = base


def _namespace_candidates(namespaces, blocked_hosts):
    """Yield candidates round-robin, advancing model cursors after each probe."""
    while not winding_down():
        active = sorted((base for base, row in namespaces.items()
                         if due(row) and urlsplit(base).hostname not in blocked_hosts),
                        key=lambda base: namespaces[base].get('checked_at', ''))
        if not active:
            return
        for base in active:
            record = namespaces[base]
            if not due(record) or urlsplit(base).hostname in blocked_hosts:
                continue
            candidates = expand_metadata_base(base, [record['next_model']],
                                              advertised_by=record.get('advertised_by'))
            for candidate in candidates:
                yield {**asdict(candidate), 'source': record['source'],
                       'inferred_namespace': base, 'advance_inference': True,
                       **({'metadata_sha256': record['metadata_sha256']}
                          if record.get('metadata_sha256') else {})}


def _advance_namespace(namespaces, row, result):
    record = namespaces.get(row.get('inferred_namespace'))
    if record is None or record['next_model'] != row['package_model']:
        return
    if result.get('available') or result.get('http_status') in {404, 410}:
        record['next_model'] += 1
        record.update(checkpoint(record, success=True, success_hours=0), status='scanning')
        record.pop('error', None)
    else:
        record.update(checkpoint(record), status='failed',
                      error=result.get('error') or f"HTTP {result.get('http_status')}")


def protocol_roots(profile=None):
    # Public anonymous controller requests, not release/campaign URLs.
    profiles = ((1, 25, 34), (2, 81, 52))
    return [f'https://{host}/firmware/swgen/{generation}/latest/update.upm?' + urlencode({
        'sonosid': 'Anonymous', 'householdid': 'nohhid',
        **(profile if profile is not None else {'cmaj': major, 'cmin': 0, 'cbld': build}),
    }) for host in ('update.sonos.com', 'update-stage.sonos.com', 'update-perf.sonos.com', 'update-test.sonos.com')
        for generation, major, build in profiles]


def discover(state: dict, evidence: Path, *, fetch=None, probe_fn=None,
             metadata_fetch=fetch_update_metadata, index_fetch=None, artifact_roots=(), known_artifacts=(),
             profile_fetch=None, version_hints=(), installer_pivots=()):
    # Every source cooperatively follows the same workflow wind-down signal.
    # There is no independent local or per-phase runtime budget.
    namespaces = {key: dict(row) for key, row in state.get('inferred_namespaces', {}).items()}
    for base in namespaces:
        _configure_namespace(namespaces, base)
    # A reported manifest can disappear while its versioned payloads survive.
    # Its native campaign path supplies a build to probe independently.
    for url in state.get('public_client_reports', {}).get('roots', {}):
        if not official(url) or not urlsplit(url).path.endswith('.upm'):
            continue
        folder = url.rsplit('/', 1)[0]
        build = re.match(r'(\d+[.]\d+-\d+)-v', urlsplit(folder).path.rsplit('/', 1)[-1])
        if build:
            _add_namespace(namespaces, folder+'/^'+build[1], 'observed-manifest-path')
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
    observed = [row['url'] for row in state.get('manifests', [])]
    observed.extend(row.get('metadata', {}).get('base_url') for row in state.get('manifests', [])
                    if row.get('metadata', {}).get('base_url'))
    observed.extend(row['url'] for row in state.get('candidates', []) if row.get('source') == 'image')
    pivot_state = deepcopy(state.get('installer_pivot_search', {}))
    pivot_stream = _installer_root_stream(pivot_state, installer_pivots, observed)
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
        archive = index_fetch({**archive, 'query_seeds': [*roots, *static,
            *(url for url in state.get('pending_manifest_urls', []) if url in scoped_urls),
            *(row.get('url') for row in [*state.get('manifests', []),
              *state.get('candidates', []), *saved_documents, *known_artifacts])]})
        roots.extend(archive['entries'])
        roots.extend(url.rsplit('/', 1)[0] + '/update.upm' for url in archive.get('artifacts', {}) if official(url))
    bootstrap_errors = []
    prior_metadata = state.get('metadata_profiles', {})
    metadata = {**prior_metadata,
                'profiles': dict(prior_metadata.get('profiles', {})),
                'responses': dict(prior_metadata.get('responses', {})),
                'errors': []}
    roots.extend(url for row in metadata['profiles'].values() if row.get('status') == 'observed'
                 for url in protocol_roots(row['profile']))
    try:
        if prior_metadata.get('source_backoff') and not due(prior_metadata['source_backoff']):
            raise ValueError('baseline UPS request deferred until its recorded source cooldown')
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
                _add_namespace(namespaces, url, 'metadata-base', metadata_sha256=response['sha256'],
                               advertised_by=UPDATE_METADATA_URL)
    # Resume our own observed frontier; never read a third-party URL inventory.
    pending = [url for url in state.get('pending_manifest_urls', []) if url in scoped_urls]
    excluded_pending = len(state.get('pending_manifest_urls', []))-len(pending)
    pending.extend(row['url'] for row in state.get('candidates', []) if landing_url(row['url']))
    known_urls = [m['url'] for m in state.get('manifests', [])]
    known_urls.extend(m['url'] for m in saved_documents)
    # Probe current feeds before saved backlog to capture mutable responses promptly.
    priority = live_roots
    initial = [{'url': url, **({'advertised_by': authorities[url]} if url in authorities else {})}
               for url in dict.fromkeys(priority + pending + roots + known_urls)
               if advertised_url(url, authorities.get(url))]
    paused_hosts = {urlsplit(UPDATE_METADATA_URL).hostname} if any(
        error.get('http_status') in {403, 429} for error in bootstrap_errors) else set()
    observed = {'packages': [{'source_url': row['url'], 'version': row['version']} for row in known_artifacts]}
    graph = crawl_manifests(observed, initial, [], evidence,
                            explore_channels=True, fetch=fetch, paused_hosts=paused_hosts)
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
                _add_namespace(namespaces, base, 'archive-directory')
    for directory in graph.get('directories', []):
        base = directory['directory'].rstrip('/') + '/^' + directory['version']
        _add_namespace(namespaces, base, 'observed-directory')
    for url, row in static.items():
        candidates[url] = {**candidates.get(url, {}), **row}
    for row in list(candidates.values()):
        if row.get('inferred_only'):
            continue
        fields = {key: row.get(key) for key in Candidate.__dataclass_fields__}
        counterpart = host_counterpart(Candidate(**fields))
        if counterpart:
            candidates.setdefault(counterpart.url, {**row, **asdict(counterpart), 'parent_url': row['url']})
    prior_probes = state.get('probes', {})
    checks = {}
    blocked = set(graph.get('blocked_hosts', []))
    rows = deque(sorted(candidates.values(), key=lambda row: (row['url'] in prior_probes,
                    prior_probes.get(row['url'], {}).get('attempt_sequence', 0))))
    sequence = state.get('probe_sequence', 0)

    def check(row):
        nonlocal sequence
        host = urlsplit(row['url']).hostname
        if host in blocked or row.get('source') == 'forum-archive':
            return
        namespace = namespaces.get(row.get('inferred_namespace'))
        if row.get('advance_inference') and (not namespace or
                namespace['next_model'] != row['package_model'] or not due(namespace)):
            return
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

    # Round-robin the open-ended sources. One work unit per source per turn
    # is fairness, not a quota: all sources can run for the entire shared
    # four-hour window, with no fixed number of attempts or phase percentages.
    attempted_roots = {item['url'] for item in initial}
    model_stream = _namespace_candidates(namespaces, blocked)

    def ingest_root(url, witness=None):
        if url in attempted_roots or not advertised_url(url, witness):
            return
        attempted_roots.add(url)
        roots.append(url)
        if witness:
            authorities[url] = witness
        if winding_down():
            graph['pending_manifest_urls'].append(url)
            return
        batch = crawl_manifests(
            observed,
            [{'url': url, **({'advertised_by': witness} if witness else {})}],
            [], evidence, explore_channels=True, fetch=fetch,
            paused_hosts=blocked)
        for record in batch['manifests']:
            identity = record['url'], record['sha256']
            manifests[identity] = {**manifests.get(identity, {}), **record}
        for record in batch.get('documents', []):
            identity = record['url'], record['sha256']
            documents[identity] = {**documents.get(identity, {}), **record}
        for found in batch.get('candidates', []):
            if found.url not in candidates:
                candidates[found.url] = asdict(found)
                rows.append(candidates[found.url])
                counterpart = host_counterpart(found)
                if counterpart and counterpart.url not in candidates:
                    candidates[counterpart.url] = {**asdict(counterpart),
                                                    'parent_url': found.url}
                    rows.append(candidates[counterpart.url])
        for directory in batch.get('directories', []):
            _add_namespace(namespaces, directory['directory'].rstrip('/') + '/^' + directory['version'],
                           'observed-directory')
        for key in ('manifests', 'documents', 'edges', 'failures', 'directories', 'pending_manifest_urls'):
            graph.setdefault(key, []).extend(batch.get(key, []))
        graph.setdefault('advertised_sources', {}).update(batch.get('advertised_sources', {}))
        graph['blocked'] = graph.get('blocked', False) or batch.get('blocked', False)
        graph['deadline_reached'] = graph.get('deadline_reached', False) or batch.get('deadline_reached', False)
        blocked.update(batch.get('blocked_hosts', []))
        graph['blocked_hosts'] = sorted(blocked)

    def ingest_profile(profile, response):
        for url in protocol_roots(profile):
            ingest_root(url)
        for record in response.get('records', []):
            url = record.get('uri', '').replace('http://', 'https://', 1)
            if record['type'] in {7, 14} and not advertised_url(url, UPDATE_METADATA_URL):
                bootstrap_errors.append({'source': 'legacy-ups-uri', 'url': url,
                                         'error': 'UPS URI uses an unsupported host or path'})
                continue
            if record['type'] == 14:
                ingest_root(url, UPDATE_METADATA_URL)
            elif record['type'] == 7:
                _add_namespace(namespaces, url, 'metadata-base',
                               metadata_sha256=response['sha256'], advertised_by=UPDATE_METADATA_URL)
                folder = url.split('^', 1)[0].rstrip('/') if '^' in url else url.rsplit('/', 1)[0]
                ingest_root(folder + '/update.upm', UPDATE_METADATA_URL)

    profile_events = SimpleQueue()
    profile_catalog = {'packages': known_artifacts,
                       'evidence': [{'version': version} for version in version_hints]}
    # The profile source runs independently. An endless numeric search cannot
    # prevent discovered manifests, pivot candidates, or package probes from
    # using the rest of the runner.
    profile_pool = ThreadPoolExecutor(max_workers=1)
    profile_stop = Event()
    profile_future = profile_pool.submit(discover_profiles, metadata, evidence, profile_catalog,
        stop=profile_stop,
        on_capture=lambda profile, captured: profile_events.put((profile, captured)),
        **({'fetch': profile_fetch} if profile_fetch else {}))
    try:
        while not winding_down():
            progress = False
            if rows:
                check(rows.popleft())
                progress = True
            if winding_down():
                break
            try:
                profile, response = profile_events.get_nowait()
            except Empty:
                pass
            else:
                ingest_profile(profile, response)
                progress = True
            if winding_down():
                break
            if pivot_stream is not None:
                try:
                    pivot = next(pivot_stream)
                except StopIteration:
                    pivot_stream = None
                else:
                    ingest_root(pivot)
                    progress = True
            if winding_down():
                break
            if model_stream is None and namespaces:
                model_stream = _namespace_candidates(namespaces, blocked)
            if model_stream is not None:
                try:
                    row = next(model_stream)
                except StopIteration:
                    model_stream = None
                else:
                    prior = candidates.get(row['url'])
                    if prior and not prior.get('inferred_only'):
                        row = {**prior, 'advance_inference': True,
                               'inferred_namespace': row['inferred_namespace']}
                    check(row)
                    if row.get('discovered_available'):
                        candidates[row['url']] = {**row, 'inferred_only': True}
                    progress = True
            if not progress:
                if profile_future.done() and pivot_stream is None and not rows and model_stream is None:
                    break
                time.sleep(.05)
    finally:
        # Stop only between requests; an in-flight metadata request is allowed
        # to finish before retaining the checkpoint.
        profile_stop.set()
        profile_pool.shutdown(wait=True)
    try:
        metadata = profile_future.result()
    except Exception as error:
        bootstrap_errors.append({'source': 'legacy-ups-profile', 'error': str(error)})
    else:
        bootstrap_errors.extend(metadata.get('errors', []))
    while True:
        try:
            profile, response = profile_events.get_nowait()
        except Empty:
            break
        # A profile completed after the soft signal. Keep every discovered URL
        # pending for the next worker rather than silently dropping its evidence.
        for url in protocol_roots(profile):
            if url not in attempted_roots:
                roots.append(url)
                graph['pending_manifest_urls'].append(url)
                attempted_roots.add(url)
        for record in response.get('records', []):
            url = record.get('uri', '').replace('http://', 'https://', 1)
            if record['type'] == 7 and advertised_url(url, UPDATE_METADATA_URL):
                _add_namespace(namespaces, url, 'metadata-base',
                               metadata_sha256=response['sha256'], advertised_by=UPDATE_METADATA_URL)
    retained = {url: {key: value for key, value in row.items() if key != 'advance_inference'}
                for url, row in candidates.items() if not row.get('inferred_only') or row.get('discovered_available')}
    probes = {url: row for url, row in {**prior_probes, **checks}.items() if url in retained}
    inference_pending = len(namespaces)
    authorities.update(graph.get('advertised_sources', {}))
    deferred = list(dict.fromkeys(graph['pending_manifest_urls'] + [f['url'] for f in graph['failures']
                        if f.get('attempted') and advertised_url(f['url'], authorities.get(f['url']))]))
    result = {'schema_version': 1, 'roots': roots, 'manifests': list(manifests.values()),
              'documents': list(documents.values()),
              'candidates': list(retained.values()), 'probes': probes,
              'inferred_namespaces': namespaces, 'inference_pending': inference_pending,
              'model_exploration': {'policy': 'unbounded-model-cursors', 'namespaces': len(namespaces),
                                    'pending_namespaces': inference_pending},
              'pending_manifest_urls': deferred, 'graph': graph, 'advertised_sources': authorities,
              'checked_this_run': len(checks), 'unchecked_this_run': sum(url not in checks for url in candidates),
              'checked_urls': list(checks),
              'probe_sequence': sequence,
              'installer_pivot_search': pivot_state,
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
