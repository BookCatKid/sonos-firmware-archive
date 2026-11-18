"""Common Crawl collection discovery and exact WARC/ARC capture retrieval."""
from __future__ import annotations

import hashlib
import json
import re
import tempfile
import urllib.error
import urllib.request
from datetime import datetime, timezone
from email.message import Message
from urllib.parse import urlencode, urlsplit
from urllib.response import addinfourl

from .discovery import USER_AGENT
from .retry import checkpoint, due, error_checkpoint

REGISTRY = 'https://index.commoncrawl.org/collinfo.json'


def _query(fetch, url, *, count=False):
    try:
        return fetch(url)
    except urllib.error.HTTPError as error:
        try:
            if error.code == 404 and error.fp is not None:
                body = error.read(4097)
                try:
                    message = json.loads(body).get('message') if len(body) <= 4096 else None
                except (ValueError, AttributeError):
                    message = None
                # pywb 0.33 (used by Common Crawl) distinguishes an empty
                # query from a missing collection using this response message.
                if isinstance(message, str) and message.startswith('No Captures found for:'):
                    return {'pages': 0} if count else []
        finally:
            error.close()
        raise


def capture_url(capture):
    filename = capture.get('filename', '')
    if (not isinstance(filename, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9/._-]*', filename)
            or any(part in {'.', '..'} for part in filename.split('/'))):
        raise ValueError('invalid Common Crawl archive filename')
    for field in ('offset', 'length'):
        if isinstance(capture.get(field), bool) or not str(capture.get(field, '')).isdecimal():
            raise ValueError('Common Crawl capture requires an exact byte range')
    if int(capture['length']) <= 0:
        raise ValueError('Common Crawl capture has an empty byte range')
    return 'https://data.commoncrawl.org/' + filename


class _ExactRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if newurl != req.full_url:
            raise ValueError('Common Crawl range redirected to another archive object')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_capture(original, capture, *, allowed, timeout=60):
    from .archive_replay import canonical_source
    from warcio.archiveiterator import ArchiveIterator
    url = capture_url(capture)
    source = capture.get('original_url') or original
    if canonical_source(source, allowed) != canonical_source(original, allowed):
        raise ValueError('Common Crawl capture belongs to another source URL')
    timestamp = capture.get('timestamp')
    if not isinstance(timestamp, str) or not re.fullmatch(r'\d{14}', timestamp):
        raise ValueError('Common Crawl capture requires an exact timestamp')
    offset, length = int(capture['offset']), int(capture['length'])
    request = urllib.request.Request(url, headers={'User-Agent': USER_AGENT,
        'Range': f'bytes={offset}-{offset+length-1}', 'Accept-Encoding': 'identity'})
    output = tempfile.SpooledTemporaryFile(max_size=1024*1024)
    try:
        with tempfile.SpooledTemporaryFile(max_size=1024*1024) as compressed:
            opener = urllib.request.build_opener(_ExactRedirect())
            with opener.open(request, timeout=timeout) as response:
                if response.status != 206 or response.geturl() != url:
                    raise ValueError('Common Crawl server did not return the exact requested range')
                match = re.fullmatch(r'bytes (\d+)-(\d+)/(?:\d+|\*)', response.headers.get('Content-Range', ''))
                if not match or (int(match[1]), int(match[2])) != (offset, offset+length-1):
                    raise ValueError('Common Crawl response selected another byte range')
                while chunk := response.read(min(1024*1024, length-compressed.tell()+1)):
                    compressed.write(chunk)
                    if compressed.tell() > length:
                        raise ValueError('Common Crawl response exceeds the requested range')
            if compressed.tell() != length:
                raise ValueError('truncated Common Crawl byte range')
            compressed.seek(0)
            records = ArchiveIterator(compressed, check_digests='raise', arc2warc=True)
            record = next(records, None)
            if record is None or record.rec_type != 'response' or not record.http_headers:
                raise ValueError('Common Crawl capture is not a complete HTTP response record')
            target = record.rec_headers.get_header('WARC-Target-URI')
            date = record.rec_headers.get_header('WARC-Date')
            if not isinstance(target, str) or not isinstance(date, str):
                raise ValueError('Common Crawl record lacks a target URI or capture date')
            if canonical_source(target, allowed) != canonical_source(original, allowed):
                raise ValueError('Common Crawl record contains another source URL')
            if datetime.fromisoformat(date.replace('Z', '+00:00')).astimezone(timezone.utc).strftime('%Y%m%d%H%M%S') != timestamp:
                raise ValueError('Common Crawl record contains another capture date')
            if record.rec_headers.get_header('WARC-Truncated') or record.http_headers.get_statuscode() != '200':
                raise ValueError('Common Crawl capture is truncated or has a non-200 original status')
            expected = capture.get('digest', '')
            recorded = record.rec_headers.get_header('WARC-Payload-Digest') or ''
            if expected and expected != '-' and expected.upper().removeprefix('SHA1:') != recorded.upper().removeprefix('SHA1:'):
                raise ValueError('Common Crawl index and record payload digests differ')
            stream = record.content_stream()
            while chunk := stream.read(1024*1024):
                output.write(chunk)
            if getattr(record.raw_stream, 'limit', 0) != 0:
                raise ValueError('truncated Common Crawl response record')
            declared = record.http_headers.get_header('Content-Length')
            encoded = record.http_headers.get_header('Content-Encoding') or record.http_headers.get_header('Transfer-Encoding')
            if declared is not None and not encoded and int(declared) != output.tell():
                raise ValueError('Common Crawl original Content-Length does not match its payload')
            content_type = record.http_headers.get_header('Content-Type') or 'application/octet-stream'
            if 'html' in content_type.lower():
                raise ValueError('Common Crawl captured an HTML page instead of a software payload')
            if next(records, None) is not None:
                raise ValueError('Common Crawl range contains multiple records')
        headers = Message()
        headers['Content-Type'] = content_type
        headers['Content-Length'] = str(output.tell())
        output.seek(0)
        return addinfourl(output, headers, url, code=200)
    except Exception:
        output.close()
        raise


def discover(previous, fetch, *, pages, roots, extensions, allowed, match_type=None):
    """Resume every advertised collection; a failed query cannot pin the cursor."""
    from .archive_index import _capture, _observations, _remember
    if not isinstance(pages, int) or isinstance(pages, bool) or pages <= 0:
        raise ValueError('Common Crawl page budget must be positive')
    state = {**previous, 'collections': dict(previous.get('collections', {})),
             'queries': {k: dict(v) for k, v in previous.get('queries', {}).items()}}
    errors, observations = [], []
    registry = state.get('registry', {})
    if due(registry) and due(state.get('host_retry')):
        try:
            collections = fetch(REGISTRY)
            if not isinstance(collections, list) or not collections:
                raise ValueError('Common Crawl registry must list collections')
            for item in collections:
                endpoint = item.get('cdx-api', '')
                parsed = urlsplit(endpoint)
                if (parsed.scheme != 'https' or parsed.netloc != 'index.commoncrawl.org'
                        or parsed.query or parsed.fragment or not re.fullmatch(r'/[A-Za-z0-9_-]+-index', parsed.path)):
                    raise ValueError('Common Crawl registry advertised an unsupported index endpoint')
                state['collections'][endpoint] = item.get('id', parsed.path)
            state['registry'] = checkpoint(registry, success=True, success_hours=168)
        except Exception as error:
            state['registry'] = {**error_checkpoint(error, registry), 'error': str(error), 'url': REGISTRY,
                                 'http_status': getattr(error, 'code', None)}
            if getattr(error, 'code', None) in {403, 429}:
                state['host_retry'] = error_checkpoint(error, state.get('host_retry'))
    entries, artifacts = _observations(previous.get('entries', {})), _observations(previous.get('artifacts', {}))
    revision = hashlib.sha256(json.dumps([list(roots.items()), list(extensions), match_type]).encode()).hexdigest()
    if previous.get('query_revision') != revision:
        state['queries'] = {}
        state['next_query'] = 0
    endpoints = sorted(state['collections'], reverse=True)
    ordered = []
    for index in range((len(endpoints)+1)//2):
        ordered.append(endpoints[index])
        opposite = len(endpoints)-1-index
        if opposite != index:
            ordered.append(endpoints[opposite])
    jobs = [(endpoint, root) for endpoint in ordered for root in roots.values()]
    position = state.get('next_query', 0)
    attempted = set()
    for _ in range(len(jobs)):
        if len(attempted) >= pages or not due(state.get('host_retry')):
            break
        endpoint, root = jobs[position % len(jobs)]
        position += 1
        identity = endpoint+'|'+root
        prior = state['queries'].get(identity, {})
        if not due(prior):
            continue
        attempted.add(identity)
        query = {'url': root, 'matchType': match_type or ('domain' if root == 'sonos.com/' else 'prefix'),
                 'output': 'json', 'pageSize': 1,
                 'filter': ['=status:200', '~url:(?i).*[.]('+'|'.join(re.escape(ext) for ext in extensions)+')([?].*)?$']}
        try:
            page = prior.get('next_page', 0) if prior.get('status') != 'complete' else 0
            total = prior.get('pages')
            if total is None or prior.get('status') == 'complete':
                count_url = endpoint+'?'+urlencode({**query, 'showNumPages': 'true'}, doseq=True)
                count = _query(fetch, count_url, count=True)
                total = count.get('pages') if isinstance(count, dict) else None
                if not isinstance(total, int) or isinstance(total, bool) or total < 0:
                    raise ValueError('Common Crawl index returned an invalid page count')
            url = endpoint+'?'+urlencode({**query, 'page': page}, doseq=True)
            prior = {**prior, 'pages': total, 'next_page': page}
            rows = _query(fetch, url) if page < total else []
            if isinstance(rows, dict) and 'url' in rows:
                rows = [rows]
            if not isinstance(rows, list):
                raise ValueError('Common Crawl page must contain JSON records')
            invalid = False
            for row in rows:
                try:
                    observed = _capture([row['timestamp'], row['url'], row.get('digest', '-')], url, allowed)
                    if observed is None:
                        continue
                    original, capture = observed
                    capture.update(provider='commoncrawl', filename=row['filename'], offset=row['offset'], length=row['length'])
                    capture_url(capture)
                    _remember(entries, artifacts, original, capture, extensions)
                except (ValueError, TypeError, KeyError) as error:
                    invalid = True
                    errors.append({'url': url, 'error': str(error)})
            if invalid:
                raise ValueError('Common Crawl page contains malformed capture records; page retained for retry')
            complete = page+1 >= total
            state['queries'][identity] = {'next_page': page+1, 'pages': total,
                'status': 'complete' if complete else 'pending',
                **(checkpoint(prior, success=True, success_hours=168) if complete else {})}
            observations.append({'url': url, 'records': len(rows), 'more_pages': not complete})
        except Exception as error:
            state['queries'][identity] = {**prior, **error_checkpoint(error, prior), 'status': 'failed',
                'error': str(error), 'url': endpoint, 'http_status': getattr(error, 'code', None)}
            if getattr(error, 'code', None) in {403, 429}:
                state['host_retry'] = error_checkpoint(error, state.get('host_retry'))
                break
    state.update(query_revision=revision, next_query=position % len(jobs) if jobs else 0, entries=entries, artifacts=artifacts)
    errors.extend(row for row in [state.get('registry', {}), *state['queries'].values()] if row.get('error'))
    pending = sum(state['queries'].get(endpoint+'|'+root, {}).get('status') != 'complete' or
                  due(state['queries'].get(endpoint+'|'+root, {})) for endpoint, root in jobs)
    state.update(errors=errors, observations=observations, pending_queries=pending,
                 status='partial' if errors or pending or not jobs else 'ok')
    return state
