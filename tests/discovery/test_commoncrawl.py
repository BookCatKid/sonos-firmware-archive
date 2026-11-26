"""Independent index traversal and exact archive replay through real publication."""
import gzip
import io
import json
import tempfile
import unittest
import urllib.error
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from warcio.archiveiterator import ArchiveIterator
from warcio.statusandheaders import StatusAndHeaders
from warcio.warcwriter import WARCWriter
from warcio.exceptions import ArchiveLoadFailed

from sonos_firmware import commoncrawl as cc, archive_index, archive_replay, installers, releases, retry
from tests.apps.test_app_archive import NOW, FakeGitHub

A = 'https://index.commoncrawl.org/CC-MAIN-2020-01-index'
B = 'https://index.commoncrawl.org/CC-MAIN-2021-02-index'
SOURCE = 'https://update.sonos.com/software/Sonos.exe'


def indexed(timestamp='20200101000000', **extra):
    return {'url': SOURCE, 'timestamp': timestamp, 'digest': 'fixture',
            'filename': 'crawl-data/fixture/warc/test.warc.gz', 'offset': '99', 'length': '123', **extra}


def scan_cc(previous, fetch, *, steps=None, now=NOW, **options):
    processed = 0
    def counted(url):
        nonlocal processed
        if 'page=' in url and 'showNumPages' not in url:
            processed += 1
        return fetch(url)
    with patch.object(retry, '_now', return_value=now), \
         patch.object(cc, 'winding_down', side_effect=lambda: steps is not None and processed >= steps):
        return cc.discover(previous, counted, **options)


def scan_archives(previous, fetch, *, steps=None, **options):
    wayback = commoncrawl = 0
    def counted(url):
        nonlocal wayback, commoncrawl
        if url.startswith('https://web.archive.org/'):
            wayback += 1
        elif url.startswith('https://index.commoncrawl.org/') and 'page=' in url and 'showNumPages' not in url:
            commoncrawl += 1
        return fetch(url)
    with patch('sonos_firmware.archive_index.winding_down',
               side_effect=lambda: steps is not None and wayback >= steps), \
         patch.object(cc, 'winding_down',
               side_effect=lambda: steps is not None and commoncrawl >= steps):
        return archive_index.discover_archives(previous, counted, **options)


def discover(previous, fetch, now=NOW, steps=1):
    return scan_cc(previous, fetch, now=now, steps=steps,
                   roots={'sonos.com': 'sonos.com/'}, extensions=('exe',),
                   allowed=installers.official_source)


class CommonCrawlIndexTests(unittest.TestCase):
    def test_many_observed_files_coalesce_into_a_directory_that_discovers_unknown_siblings(self):
        unseen = 'https://update.sonos.com/software/previously-unseen.exe?variant=older'
        requests = []
        def fetch(url):
            requests.append(url)
            if url == cc.REGISTRY:
                return [{'cdx-api': A}]
            query = parse_qs(urlsplit(url).query)
            if query.get('matchType') != ['prefix']:
                return [] if urlsplit(url).hostname == 'web.archive.org' else {'pages': 0}
            self.assertEqual(query['url'], ['update.sonos.com/software/'])
            if urlsplit(url).hostname == 'web.archive.org':
                return [['timestamp', 'original', 'digest'], ['20200101000000', unseen, 'fixture']]
            return {'pages': 1} if 'showNumPages' in query else [indexed(url=unseen)]
        seeds = [f'https://update.sonos.com/software/known-{i}.exe?profile={i}' for i in range(1000)]
        with patch.object(retry, '_now', return_value=NOW):
            result = scan_archives({}, fetch=fetch, seed_urls=seeds,
                extensions=('exe',), allowed=installers.official_source)
        self.assertEqual(len(result['wayback_directories']['query_roots']), 1)
        self.assertEqual(len(result['commoncrawl_directories']['query_roots']), 1)
        self.assertIn(unseen, result['artifacts'])
        self.assertEqual(len(result['artifacts'][unseen]['captures']), 2)


    def test_provider_rate_limit_blocks_directory_queries_and_survives_lane_rotation(self):
        requests = []
        def fetch(url):
            requests.append(url)
            if url == cc.REGISTRY:
                return [{'cdx-api': A}]
            raise urllib.error.HTTPError(url, 429, 'later', {'Retry-After': '7200'}, None)
        with patch.object(retry, '_now', return_value=NOW):
            first = scan_archives({}, fetch=fetch, steps=2,
                seed_urls=[SOURCE], extensions=('exe',), allowed=installers.official_source)
            self.assertFalse(any('matchType=prefix' in url for url in requests))
            requests.clear()
            second = scan_archives(first, fetch=fetch, steps=1,
                extensions=('exe',), allowed=installers.official_source)
            third = scan_archives(second, fetch=fetch, steps=1,
                extensions=('exe',), allowed=installers.official_source)
        self.assertEqual(requests, [])
        self.assertEqual(third['wayback_directories']['index_retry'], first['wayback']['index_retry'])


    def test_collection_and_page_cursors_resume_without_download_lists_or_line_limits(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url == cc.REGISTRY:
                return [{'cdx-api': A}, {'cdx-api': B}]
            query = parse_qs(urlsplit(url).query)
            self.assertNotIn('limit', query)
            self.assertNotIn('pageSize', query)
            if 'showNumPages' in query:
                return {'pages': 2 if url.startswith(A) else 1}
            return [indexed('20200101000000' if query['page'] == ['0'] else '20210101000000')]
        state = discover({}, fetch)
        self.assertEqual(state['pending_queries'], 1)
        state = discover(json.loads(json.dumps(state)), fetch)
        self.assertEqual(state['pending_queries'], 1)
        state = discover(json.loads(json.dumps(state)), fetch)
        self.assertEqual(state['status'], 'ok')
        self.assertEqual(len(state['artifacts'][SOURCE]['captures']), 2)
        self.assertEqual(state['artifacts'][SOURCE]['filename'], 'Sonos.exe')
        self.assertEqual(sum(url == cc.REGISTRY for url in calls), 1)
        self.assertTrue(any(url.startswith(A) and parse_qs(urlsplit(url).query).get('page') == ['1'] for url in calls))


    def test_malformed_page_retains_valid_rows_and_retries_same_page(self):
        bad = True
        calls = []
        def fetch(url):
            calls.append(url)
            if url == cc.REGISTRY:
                return [{'cdx-api': A}]
            if 'showNumPages' in url:
                return {'pages': 1}
            return [indexed(), {'invalid': True}] if bad else indexed()
        first = discover({}, fetch)
        self.assertIn(SOURCE, first['artifacts'])
        self.assertEqual(first['pending_queries'], 1)
        bad = False
        second = discover(first, fetch, now=NOW+timedelta(hours=2))
        self.assertEqual(second['status'], 'ok')
        self.assertEqual(sum('showNumPages' in url for url in calls), 1)


def warc(body=b'MZ synthetic installer', source=SOURCE, date='2020-01-01T00:00:00Z', extra=None, encoded=False):
    output = io.BytesIO()
    writer = WARCWriter(output, gzip=True)
    payload = gzip.compress(body) if encoded else body
    headers = [('Content-Type', 'application/octet-stream'), ('Content-Length', str(len(payload)))]
    if encoded:
        headers.append(('Content-Encoding', 'gzip'))
    record = writer.create_warc_record(source, 'response', payload=io.BytesIO(payload),
        http_headers=StatusAndHeaders('200 OK', headers, protocol='HTTP/1.1'),
        warc_headers_dict={'WARC-Date': date, **(extra or {})})
    writer.write_record(record)
    data = output.getvalue()
    digest = next(ArchiveIterator(io.BytesIO(data))).rec_headers.get_header('WARC-Payload-Digest')
    capture = {'provider': 'commoncrawl', 'timestamp': '20200101000000', 'original_url': SOURCE,
        'filename': 'crawl-data/fixture/warc/test.warc.gz', 'offset': '99', 'length': str(len(data)),
        'digest': digest.partition(':')[2]}
    return data, capture


class Response(io.BytesIO):
    status = 206
    def __init__(self, data, capture, **headers):
        super().__init__(data)
        self.url = cc.capture_url(capture)
        self.headers = {'Content-Range': f'bytes 99-{98+int(capture["length"])}/1000000', **headers}
    def geturl(self):
        return self.url


class CommonCrawlReplayTests(unittest.TestCase):
    def replay(self, data, capture, response=None):
        response = response or Response(data, capture)
        with patch.object(cc.urllib.request, 'build_opener') as opener:
            opener.return_value.open.return_value = response
            with archive_replay.open_capture(SOURCE, capture, allowed=installers.official_source) as result:
                body, headers = result.read(), result.headers
            request = opener.return_value.open.call_args.args[0]
            self.assertEqual(request.get_header('Range'), f'bytes=99-{98+int(capture["length"])}')
        self.assertTrue(response.closed)
        return body, headers

    def test_exact_record_and_http_gzip_payload_are_verified(self):
        for encoded in (False, True):
            with self.subTest(encoded=encoded):
                data, capture = warc(encoded=encoded)
                body, headers = self.replay(data, capture)
                self.assertEqual(body, b'MZ synthetic installer')
                self.assertEqual(int(headers['Content-Length']), len(body))


    def test_wrong_record_identity_timestamp_truncation_and_digest_are_rejected(self):
        examples = [warc(source=SOURCE.replace('Sonos.exe', 'another.exe')),
                    warc(date='2021-01-01T00:00:00Z'), warc(extra={'WARC-Truncated': 'length'})]
        data, capture = warc()
        examples.append((data, {**capture, 'digest': 'not-the-indexed-digest'}))
        corrupt = gzip.decompress(data).replace(b'MZ synthetic installer', b'MZ corrupted installer')
        changed = gzip.compress(corrupt)
        examples.append((changed, {**capture, 'length': str(len(changed))}))
        for data, capture in examples:
            with self.subTest(capture=capture), self.assertRaises((ValueError, ArchiveLoadFailed)):
                self.replay(data, capture)

    def test_ignored_range_wrong_range_and_short_response_are_rejected(self):
        data, capture = warc()
        ignored = Response(data, capture)
        ignored.status = 200
        for response in (ignored, Response(data, capture, **{'Content-Range': 'bytes 0-1/2'}), Response(data[:-1], capture)):
            with self.subTest(response=response), self.assertRaises(ValueError):
                self.replay(data, capture, response)


    def test_native_capture_bootstraps_the_actual_installer_publisher(self):
        data, capture = warc()
        github = FakeGitHub()
        index = {'status': 'ok', 'errors': [], 'artifacts': {SOURCE: {'captures': {'native': capture}}}}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(installers, 'discover_sources', return_value=index), \
                 patch.object(cc.urllib.request, 'build_opener') as opener, \
                 patch.object(releases, 'remote_assets', side_effect=github.assets), \
                 patch.object(releases.subprocess, 'run', side_effect=github.command):
                opener.return_value.open.return_value = Response(data, capture)
                result = installers.archive_history(root, root/'work', 'fresh/archive', 'desktop', )
            self.assertEqual(result['artifact_total'], 1)
            self.assertEqual(github.uploads[0][3], b'MZ synthetic installer')
            receipt = json.loads((root/'data/apps/desktop-archive.json').read_text())
            observed = receipt['artifacts'][0]['acquisitions'][0]
            self.assertEqual(observed['acquisition_id'], 'commoncrawl:native')
            self.assertEqual(observed['archive_capture'], capture)
            self.assertNotIn('wayback_timestamp', observed)


if __name__ == '__main__':
    unittest.main()
