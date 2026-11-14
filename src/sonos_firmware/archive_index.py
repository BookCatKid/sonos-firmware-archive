"""Discover historical Sonos manifest paths through a resumable public index."""
import json
import urllib.request
from urllib.parse import urlencode, unquote_plus, urlsplit, urlunsplit

from .crawl import official, normalize
from .discovery import USER_AGENT

HOSTS = ('update.sonos.com', 'update-firmware.sonos.com', 'update-stage.sonos.com',
         'update-test.sonos.com', 'update-firmware-test.sonos.com', 'update-perf.sonos.com')


def fetch_index(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=15) as response:
        body = response.read(2_000_001)
    if len(body) > 2_000_000:
        raise ValueError('archive index page exceeds 2 MB')
    return json.loads(body)


def manifest_roots(previous, fetch=fetch_index, pages=2):
    cursors = dict(previous.get('cursors', {}))
    entries = dict(previous.get('entries', {}))
    observations = []
    errors = []
    position = previous.get('next_host', 0)
    for _ in range(pages):
        host = HOSTS[position % len(HOSTS)]
        position += 1
        query = {'url': host + '/firmware/', 'matchType': 'prefix', 'output': 'json',
                 'fl': 'timestamp,original,digest',
                 'filter': ['statuscode:200', 'original:.*[.]upm([?].*)?$'],
                 'collapse': 'urlkey', 'limit': 500, 'showResumeKey': 'true'}
        if cursors.get(host):
            query['resumeKey'] = unquote_plus(cursors[host])
        url = 'https://web.archive.org/cdx/search/cdx?' + urlencode(query, doseq=True)
        try:
            rows = fetch(url)
            resume = None
            if len(rows) >= 2 and rows[-2] == []:
                resume = rows[-1][0]
                rows = rows[:-2]
            if rows and rows[0] != ['timestamp', 'original', 'digest']:
                raise ValueError('unexpected archive index fields')
            for timestamp, original, digest in rows[1:]:
                parsed = urlsplit(original)
                if not parsed.username and not parsed.password and (
                    (parsed.scheme == 'http' and parsed.port == 80) or
                    (parsed.scheme == 'https' and parsed.port == 443)
                ):
                    original = urlunsplit((parsed.scheme, parsed.hostname, parsed.path, parsed.query, ''))
                original = normalize(original)
                if official(original) and original.split('?', 1)[0].endswith('.upm'):
                    entries[original] = {'timestamp': timestamp, 'digest': digest, 'index_url': url}
            cursors[host] = resume
            observations.append({'url': url, 'records': max(0, len(rows) - 1), 'more_pages': bool(resume)})
        except Exception as error:
            errors.append({'url': url, 'error': str(error), 'http_status': getattr(error, 'code', None)})
            if getattr(error, 'code', None) in (403, 429):
                break
    return {'entries': entries, 'cursors': cursors, 'next_host': position % len(HOSTS),
            'observations': observations, 'errors': errors}
