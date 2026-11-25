"""Discover timestamped public Sonos captures through a resumable archive index."""
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict
from urllib.parse import urlencode, unquote_plus, urlsplit, urlunsplit, parse_qs

from .crawl import official, PACKAGE
from .discovery import USER_AGENT, Candidate
from .retry import error_checkpoint, due, retry_after_seconds

def fetch_index(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=45) as response:
            body = response.read(2_000_001)
    except (urllib.error.URLError, ssl.SSLError) as error:
        # A different TLS implementation/trust store can serve metadata when
        # urllib's certificate chain fails. Keep certificate validation enabled
        # and leave HTTP failures, including rate limits, to the retry ledger.
        if not isinstance(getattr(error, 'reason', error), ssl.SSLError):
            raise
        from curl_cffi import requests
        response = requests.get(url, impersonate='chrome', timeout=45, stream=True)
        try:
            if response.status_code != 200:
                raise urllib.error.HTTPError(url, response.status_code, 'archive index request failed',
                                             response.headers, None)
            chunks = bytearray()
            for chunk in response.iter_content():
                if len(chunks) + len(chunk) > 2_000_000:
                    raise ValueError('archive index page exceeds 2 MB')
                chunks.extend(chunk)
            body = bytes(chunks)
        finally:
            response.close()
    if len(body) > 2_000_000:
        raise ValueError('archive index page exceeds 2 MB')
    if not body.strip():
        return []
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return [json.loads(line) for line in body.splitlines() if line.strip()]


def _capture(row, index_url, allowed=official):
    if not isinstance(row, list) or len(row) != 3 or any(not isinstance(value, str) for value in row):
        raise ValueError('archive index capture must have timestamp, URL and digest fields')
    timestamp, original, digest = row
    if not re.fullmatch(r'\d{14}', timestamp):
        raise ValueError('archive index capture has an invalid timestamp')
    parsed = urlsplit(original)
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or
            parsed.port not in {None, 80 if parsed.scheme == 'http' else 443}):
        return None
    canonical = urlunsplit(('https', parsed.hostname, parsed.path, parsed.query, ''))
    if not allowed(canonical):
        return None
    capture = {'timestamp': timestamp, 'digest': digest, 'index_url': index_url,
               'original_url': urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ''))}
    return canonical, capture


def _observations(records):
    result = {}
    for url, row in records.items():
        captures = dict(row.get('captures', {}))
        if not captures and row.get('timestamp') and row.get('digest'):
            legacy = {key: row[key] for key in ('timestamp', 'digest', 'index_url') if key in row}
            identity = hashlib.sha256(json.dumps([url, legacy], sort_keys=True).encode()).hexdigest()
            captures[identity] = legacy
        result[url] = {**row, 'captures': captures}
    return result


def _remember(entries, artifacts, original, capture, extensions):
    identity_fields = [original, capture['timestamp'], capture['digest'], capture['original_url']]
    if capture.get('provider'):
        identity_fields.extend([capture['provider'], capture['filename'], capture['offset'], capture['length']])
    identity = hashlib.sha256(json.dumps(identity_fields).encode()).hexdigest()
    path = urlsplit(original).path
    if path.lower().endswith('.upm') and 'upm' in extensions:
        target = entries.setdefault(original, {'captures': {}})
    elif path.lower().endswith(tuple('.'+ext for ext in extensions)):
        filename = path.rsplit('/', 1)[-1]
        match = PACKAGE.fullmatch(filename)
        candidate = Candidate(match[1] if match else 'unknown', int(match[2]) if match else None,
                              original, 'public-archive-index', filename)
        target = artifacts.setdefault(original, {**asdict(candidate), 'captures': {}})
    else:
        return
    target.setdefault('captures', {})[identity] = capture
    # Native archive object filenames are replay metadata, never payload names.
    target.update({key: capture[key] for key in ('timestamp', 'digest', 'index_url', 'original_url') if key in capture})


def paused_index_hosts(index):
    # Common Crawl's index and payload storage are separate hosts. A failed
    # index request must not pause working replay storage or another provider.
    return {'web.archive.org'} if any(row.get('http_status') in {403, 429} and not due(row)
        and urlsplit(row.get('url') or 'https://web.archive.org').hostname == 'web.archive.org' for row in index.get('errors', [])) else set()


def discover_archives(previous, fetch=fetch_index, pages=2, *, seed_urls=(), **options):
    """Combine independent indexes behind one capture/publication contract."""
    from .commoncrawl import discover
    from .source_urls import sonos_url
    if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
        raise ValueError('archive index page budget must be positive')
    retained = previous.get('wayback_directories', previous.get('wayback_exact', {})).get('query_roots', {})
    urls = [*seed_urls, *previous.get('query_seeds', []),
            *previous.get('entries', {}), *previous.get('artifacts', {}),
            *retained]
    roots = options.get('roots')
    if roots is None:
        # Keep the domain-wide search while learning narrower host searches
        # from our own first-party observations. No version URL list is used.
        roots = {'sonos.com': 'sonos.com/'}
        for url in [*urls, *('https://'+root for root in previous.get('query_roots', {}).values())]:
            if isinstance(url, str) and url.startswith('http://'):
                url = 'https://'+url[7:]
            if sonos_url(url):
                host = urlsplit(url).hostname
                roots.setdefault(host, host+'/')
        options = {**options, 'roots': roots, 'match_type': options.get('match_type') or 'domain'}
    directory_roots = {}
    extensions = options.get('extensions', ('upm', 'upd'))
    for url in [*retained, *urls]:
        if isinstance(url, str) and url.startswith('http://'):
            url = 'https://'+url[7:]
        if (isinstance(url, str) and sonos_url(url) and
                (urlsplit(url).path.endswith('/') or
                 urlsplit(url).path.lower().endswith(tuple('.'+extension for extension in extensions)))):
            parsed = urlsplit(url)
            directory = parsed.path.rsplit('/', 1)[0]+'/'
            source = urlunsplit(('https', parsed.hostname, directory, '', ''))
            directory_roots.setdefault(source, source.removeprefix('https://'))
    def provider_state(name, provider):
        fallback = previous if name == 'wayback' else previous.get(name.replace('_directories', '_exact'), {})
        state = dict(previous.get(name, fallback))
        if name.endswith('_directories') and name not in previous:
            state.pop('next_host', None)
            state.pop('next_query', None)
        for field in ('entries', 'artifacts'):
            records = {}
            for url, row in _observations(previous.get(field, {})).items():
                captures = {identity: capture for identity, capture in row.get('captures', {}).items()
                            if capture.get('provider', 'wayback') == provider}
                if captures or (provider == 'wayback' and not row.get('captures')):
                    records[url] = {**row, 'captures': captures}
            state[field] = records
        return state
    # Divide each provider's existing page budget fairly between broad traversal
    # and observed directories. A one-page budget alternates across worker restarts.
    lane = previous.get('index_query_lane', 0) % 2
    budgets = [pages, 0] if not directory_roots else [pages//2, pages//2]
    if directory_roots and pages % 2:
        budgets[lane] += 1
    configuration = {'extensions': list(extensions), 'match_type': options.get('match_type') or 'prefix',
                     'source_match_type': 'prefix'}
    def traverse(provider, implementation, cooldown, **arguments):
        states = [provider_state(provider, provider), provider_state(provider+'_directories', provider)]
        for index, budget in enumerate(budgets):
            deadlines = [state.get(cooldown, {}) for state in states]
            if provider == 'wayback':
                deadlines.append(previous.get('wayback_exact', {}).get('index_retry', {}))
            shared = max(deadlines,
                         key=lambda row: row.get('next_attempt_at', ''))
            if shared:
                for state in states:
                    state[cooldown] = shared
            if provider == 'commoncrawl':
                collections = {key: value for state in states for key, value in state.get('collections', {}).items()}
                registry = max((state.get('registry', {}) for state in states),
                               key=lambda row: row.get('checked_at', ''))
                for state in states:
                    state.update(collections=collections, registry=registry)
            if budget:
                settings = {**arguments, 'roots': directory_roots, 'match_type': 'prefix'} if index else arguments
                states[index] = implementation(states[index], pages=budget, **settings)
            else:
                pending = (states[index].get('query_roots') != (directory_roots if index else roots) or
                           previous.get('query_configuration') != configuration)
                states[index] = {**states[index], 'observations': [],
                    'errors': states[index].get('errors', []),
                    'status': 'partial' if pending else states[index].get('status', 'partial')}
                states[index]['query_roots'] = directory_roots if index else roots
        return states
    wayback, wayback_directories = traverse('wayback', lambda state, **kw: discover_sources(state, fetch=fetch, **kw),
                                     'index_retry', **options)
    commoncrawl, commoncrawl_directories = traverse('commoncrawl', lambda state, **kw: discover(state, fetch, **kw),
        'host_retry', roots=roots, extensions=extensions, allowed=options.get('allowed', official),
        match_type=options.get('match_type') or 'prefix')
    # Directory discovery can time out while an exact URL query still works.
    # Retain that independent route over our own observations, with its own
    # continuation and retry state. A server cooldown still applies to both.
    normalized = [url.replace('http://', 'https://', 1) for url in urls if isinstance(url, str)]
    exact_roots = {url: url.removeprefix('https://') for url in normalized
                   if sonos_url(url) and
                   urlsplit(url).path.lower().endswith(tuple('.'+ext for ext in extensions))}
    exact = provider_state('wayback_exact', 'wayback')
    cooldown = max((s.get('index_retry', {}) for s in (wayback, wayback_directories)),
                   key=lambda row: row.get('next_attempt_at', ''))
    if cooldown:
        exact['index_retry'] = cooldown
    if exact_roots and (wayback.get('errors') or wayback_directories.get('errors')):
        exact = discover_sources(exact, fetch=fetch, pages=1, roots=exact_roots,
                                 extensions=extensions, allowed=options.get('allowed', official), match_type='exact')
    else:
        exact = {**exact, 'observations': [], 'errors': [], 'status': 'ok'}
    results = (wayback, wayback_directories, commoncrawl, commoncrawl_directories, exact)
    merged = {}
    for field in ('entries', 'artifacts'):
        records = _observations(previous.get(field, {}))
        for provider in results:
            for url, row in provider.get(field, {}).items():
                prior = records.get(url, {})
                records[url] = {**prior, **row, 'captures': {**prior.get('captures', {}), **row.get('captures', {})}}
        merged[field] = records
    cursors = lambda state: {key: value for key, value in state.items() if key not in {'entries', 'artifacts'}}
    return {**wayback, **merged, 'query_roots': roots,
            'wayback': cursors(wayback), 'commoncrawl': cursors(commoncrawl),
            'wayback_directories': cursors(wayback_directories), 'commoncrawl_directories': cursors(commoncrawl_directories),
            'wayback_exact': cursors(exact),
            'index_query_lane': (lane+pages) % 2 if directory_roots else lane,
            'query_configuration': configuration,
            'observations': [row for result in results for row in result['observations']],
            'errors': [row for result in results for row in result['errors']],
            'status': 'ok' if all(result['status'] == 'ok' for result in
                                (results if directory_roots else (wayback, commoncrawl))) else 'partial'}


def discover_sources(previous, fetch=fetch_index, pages=2, *, roots=None,
                     extensions=('upm', 'upd'), allowed=official, match_type=None):
    if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
        raise ValueError('archive index page budget must be positive')
    if previous.get('schema_version') not in {None, 1, 2}:
        raise ValueError('unsupported archive index checkpoint schema')
    match_type = match_type or ('domain' if roots is None else 'prefix')
    if match_type not in {'domain', 'host', 'prefix', 'exact'}:
        raise ValueError('unsupported archive index match scope')
    defaults = {'sonos.com': 'sonos.com/'}
    roots = defaults if roots is None else roots
    if not roots or not extensions or any(not re.fullmatch(r'[a-z0-9]+(?:\.[a-z0-9]+)*', ext) for ext in extensions):
        raise ValueError('archive index requires roots and literal filename extensions')
    hosts = list(roots)
    suffixes = '(?i:'+'|'.join(re.escape(ext) for ext in extensions)+')'
    # Adjacent digest collapsing can erase a different URL with identical
    # bytes. Keep all index observations; immutable publication identities
    # handle body reuse after origins and timestamp evidence are retained.
    query_policy = 'all-origins-and-captures'
    query_revision = hashlib.sha256(json.dumps([list(roots.items()), suffixes, match_type, query_policy]).encode()).hexdigest()
    compatible = previous.get('schema_version') == 2 and previous.get('query_revision') == query_revision
    query_specs = {host: hashlib.sha256(json.dumps([root, suffixes, match_type, query_policy]).encode()).hexdigest()
                   for host, root in roots.items()}
    legacy_hosts = (set(previous.get('cursors', {})) | set(previous.get('completed_hosts', [])) |
                    set(previous.get('pending_hosts', [])))
    legacy_roots = previous.get('query_roots') or {
        host: root for host, root in roots.items() if host in legacy_hosts}
    legacy_compatible = previous.get('schema_version') == 2 and previous.get('query_revision') == hashlib.sha256(
        json.dumps([list(legacy_roots.items()), suffixes, match_type, query_policy]).encode()).hexdigest()
    unchanged = {host for host in roots if compatible or
                 (legacy_compatible and legacy_roots.get(host) == roots[host]) or
                 previous.get('query_specs', {}).get(host) == query_specs[host]}
    # New roots must not restart unrelated pagination. Changed query semantics
    # invalidate only the affected cursor; observations remain available.
    cursors = {host: value for host, value in previous.get('cursors', {}).items() if host in unchanged}
    entries = _observations(previous.get('entries', {}))
    artifacts = _observations(previous.get('artifacts', {}))
    completed = set(previous.get('completed_hosts', [])) & unchanged
    # Reset continuation tokens after filter changes, but a retry deadline for
    # the same literal source query remains binding across those changes.
    retry_compatible = unchanged | {host for host, root in roots.items() if legacy_roots.get(host) == root}
    retries = {host: value for host, value in previous.get('retries', {}).items() if host in retry_compatible}
    # Provider throttles apply even when the set of source queries changes.
    index_retry = previous.get('index_retry', {})
    for error in previous.get('errors', []):
        root = parse_qs(urlsplit(error.get('url', '')).query).get('url', [None])[0]
        for host, value in roots.items():
            if host in retry_compatible and root == value and error.get('next_attempt_at'):
                retries.setdefault(host, error)
        if error.get('http_status') in {403, 429} and error.get('next_attempt_at') and not index_retry:
            index_retry = error
    observations = []
    errors = []
    position = previous.get('next_host', 0)
    finished_this_run = set()
    attempted = 0
    for _ in range(pages * len(hosts)):
        if attempted >= pages or not due(index_retry) or len(finished_this_run) == len(hosts):
            break
        host = hosts[position % len(hosts)]
        position += 1
        if host in finished_this_run:
            continue
        if not due(retries.get(host)):
            finished_this_run.add(host)
            continue
        attempted += 1
        query = {'url': roots[host], 'matchType': match_type, 'output': 'json', 'gzip': 'false',
                 'fl': 'timestamp,original,digest',
                 'filter': ['statuscode:200', 'original:.*[.]'+suffixes+'([?].*)?$'],
                 'limit': 500, 'showResumeKey': 'true'}
        if cursors.get(host):
            query['resumeKey'] = unquote_plus(cursors[host])
        url = 'https://web.archive.org/cdx/search/cdx?' + urlencode(query, doseq=True)
        try:
            rows = fetch(url)
            if not isinstance(rows, list):
                raise ValueError('archive index response must be a JSON list')
            resume = None
            if len(rows) >= 2 and rows[-2] == []:
                resume = rows[-1][0]
                rows = rows[:-2]
            if rows and rows[0] != ['timestamp', 'original', 'digest']:
                raise ValueError('unexpected archive index fields')
            invalid = 0
            for row in rows[1:]:
                try:
                    observed = _capture(row, url, allowed)
                except (ValueError, TypeError):
                    invalid += 1
                    continue
                if observed is None:
                    continue
                original, capture = observed
                _remember(entries, artifacts, original, capture, extensions)
            if invalid:
                raise ValueError(f'archive index page contains {invalid} malformed capture records; page retained for retry')
            cursors[host] = resume
            retries.pop(host, None)
            index_retry = {}
            if resume:
                completed.discard(host)
            else:
                completed.add(host)
                finished_this_run.add(host)
            observations.append({'url': url, 'records': max(0, len(rows) - 1), 'more_pages': bool(resume)})
        except Exception as error:
            finished_this_run.add(host)
            completed.discard(host)
            retries[host] = {'url': url, 'error': str(error), 'http_status': getattr(error, 'code', None),
                             **error_checkpoint(error, retries.get(host))}
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            if getattr(error, 'code', None) in (403, 429) or retry_after_seconds(error) is not None:
                index_retry = retries[host]
                break
    errors.extend(retries.values())
    pending_hosts = sorted(set(hosts) - completed)
    return {'schema_version': 2, 'query_revision': query_revision, 'query_specs': query_specs,
            'query_roots': dict(roots),
            'entries': entries, 'artifacts': artifacts, 'cursors': cursors, 'next_host': position % len(hosts),
            'retries': retries, 'index_retry': index_retry,
            'completed_hosts': sorted(completed), 'pending_hosts': pending_hosts,
            'status': 'partial' if errors or pending_hosts or any(cursors.values()) else 'ok',
            'observations': observations, 'errors': errors}
