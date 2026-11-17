"""Discover timestamped public Sonos captures through a resumable archive index."""
import hashlib
import json
import re
import ssl
import urllib.error
import urllib.request
from dataclasses import asdict
from urllib.parse import urlencode, unquote_plus, urlsplit, urlunsplit

from .crawl import official, PACKAGE
from .discovery import USER_AGENT, Candidate
from .retry import checkpoint

def fetch_index(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=15) as response:
            body = response.read(2_000_001)
    except (urllib.error.URLError, ssl.SSLError) as error:
        # A different TLS implementation/trust store can serve metadata when
        # urllib's certificate chain fails. Keep certificate validation enabled
        # and leave HTTP failures, including rate limits, to the retry ledger.
        if not isinstance(getattr(error, 'reason', error), ssl.SSLError):
            raise
        from curl_cffi import requests
        response = requests.get(url, impersonate='chrome', timeout=15, stream=True)
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
    return json.loads(body)


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
    query_revision = hashlib.sha256(json.dumps([list(roots.items()), suffixes, match_type]).encode()).hexdigest()
    compatible = previous.get('schema_version') == 2 and previous.get('query_revision') == query_revision
    # Query upgrades retain observations without reusing incompatible cursors.
    cursors = dict(previous.get('cursors', {})) if compatible else {}
    entries = _observations(previous.get('entries', {}))
    artifacts = _observations(previous.get('artifacts', {}))
    completed = set(previous.get('completed_hosts', [])) if compatible else set()
    observations = []
    errors = []
    position = previous.get('next_host', 0)
    finished_this_run = set()
    for _ in range(pages):
        host = hosts[position % len(hosts)]
        position += 1
        if host in finished_this_run:
            continue
        query = {'url': roots[host], 'matchType': match_type, 'output': 'json', 'gzip': 'false',
                 'fl': 'timestamp,original,digest',
                 'filter': ['statuscode:200', 'original:.*[.]'+suffixes+'([?].*)?$'],
                 'collapse': 'digest', 'limit': 500, 'showResumeKey': 'true'}
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
            for row in rows[1:]:
                try:
                    observed = _capture(row, url, allowed)
                except (ValueError, TypeError) as error:
                    errors.append({'url': url, 'error': str(error)})
                    continue
                if observed is None:
                    continue
                original, capture = observed
                capture_id = hashlib.sha256(json.dumps([
                    original, capture['timestamp'], capture['digest'], capture['original_url']]).encode()).hexdigest()
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
                    continue
                target.setdefault('captures', {})[capture_id] = capture
                target.update(capture)
            cursors[host] = resume
            if resume:
                completed.discard(host)
            else:
                completed.add(host)
                finished_this_run.add(host)
            observations.append({'url': url, 'records': max(0, len(rows) - 1), 'more_pages': bool(resume)})
        except Exception as error:
            finished_this_run.add(host)
            errors.append({'url': url, 'error': str(error), 'http_status': getattr(error, 'code', None),
                           **checkpoint()})
            if getattr(error, 'code', None) in (403, 429):
                break
    pending_hosts = sorted(set(hosts) - completed)
    return {'schema_version': 2, 'query_revision': query_revision,
            'entries': entries, 'artifacts': artifacts, 'cursors': cursors, 'next_host': position % len(hosts),
            'completed_hosts': sorted(completed), 'pending_hosts': pending_hosts,
            'status': 'partial' if errors or pending_hosts or any(cursors.values()) else 'ok',
            'observations': observations, 'errors': errors}
