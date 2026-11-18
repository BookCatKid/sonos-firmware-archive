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
from test_app_archive import NOW, FakeGitHub

A = 'https://index.commoncrawl.org/CC-MAIN-2020-01-index'
B = 'https://index.commoncrawl.org/CC-MAIN-2021-02-index'
SOURCE = 'https://update.sonos.com/software/Sonos.exe'


def indexed(timestamp='20200101000000', **extra):
    return {'url': SOURCE, 'timestamp': timestamp, 'digest': 'fixture',
            'filename': 'crawl-data/fixture/warc/test.warc.gz', 'offset': '99', 'length': '123', **extra}


def discover(previous, fetch, now=NOW, pages=1):
    with patch.object(retry, '_now', return_value=now):
        return cc.discover(previous, fetch, pages=pages, roots={'sonos.com': 'sonos.com/'},
                           extensions=('exe',), allowed=installers.official_source)


class CommonCrawlIndexTests(unittest.TestCase):
    def test_empty_query_404_is_distinct_from_a_missing_collection(self):
        for message, expected in (('No Captures found for: sonos.com/', 'ok'),
                                  ('No handler for collection', 'partial')):
            with self.subTest(message=message):
                def fetch(url):
                    if url == cc.REGISTRY:
                        return [{'cdx-api': A}]
                    raise urllib.error.HTTPError(url, 404, 'not found', {},
                        io.BytesIO(json.dumps({'message': message}).encode()))
                state = discover({}, fetch)
                self.assertEqual(state['status'], expected)

    def test_collection_and_page_cursors_resume_without_download_lists_or_line_limits(self):
        calls = []
        def fetch(url):
            calls.append(url)
            if url == cc.REGISTRY:
                return [{'cdx-api': A}, {'cdx-api': B}]
            query = parse_qs(urlsplit(url).query)
            self.assertNotIn('limit', query)
            self.assertEqual(query['pageSize'], ['1'])
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

    def test_failed_collection_does_not_starve_other_collections_and_retries(self):
        fail = True
        def fetch(url):
            if url == cc.REGISTRY:
                return [{'cdx-api': A}, {'cdx-api': B}]
            if url.startswith(B) and fail:
                raise TimeoutError('temporary index failure')
            return {'pages': 1} if 'showNumPages' in url else [indexed()]
        first = discover({}, fetch)
        second = discover(first, fetch)
        self.assertIn(SOURCE, second['artifacts'])
        self.assertEqual(second['pending_queries'], 1)
        fail = False
        third = discover(second, fetch, now=NOW+timedelta(hours=2))
        self.assertEqual(third['status'], 'ok')

    def test_429_retry_after_pauses_index_host_and_then_resumes(self):
        fail, requests = True, []
        def fetch(url):
            requests.append(url)
            if url == cc.REGISTRY:
                return [{'cdx-api': A}, {'cdx-api': B}]
            if fail:
                raise urllib.error.HTTPError(url, 429, 'retry later', {'Retry-After': '75'}, None)
            return {'pages': 1} if 'showNumPages' in url else [indexed()]
        first = discover({}, fetch)
        count = len(requests)
        discover(first, fetch, now=NOW+timedelta(seconds=74))
        self.assertEqual(len(requests), count)
        fail = False
        second = discover(first, fetch, now=NOW+timedelta(seconds=76))
        self.assertIn(SOURCE, second['artifacts'])
        self.assertEqual(archive_index.paused_index_hosts(second), set())

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

    def test_new_collections_enter_after_registry_refresh(self):
        refreshed = False
        def fetch(url):
            if url == cc.REGISTRY:
                return [{'cdx-api': A}, *([{'cdx-api': B}] if refreshed else [])]
            return {'pages': 0}
        first = discover({}, fetch)
        refreshed = True
        second = discover(first, fetch, now=NOW+timedelta(days=8), pages=2)
        self.assertIn(B, second['collections'])
        self.assertEqual(second['status'], 'ok')

    def test_registry_cannot_redirect_discovery_to_an_arbitrary_endpoint(self):
        requests = []
        def fetch(url):
            requests.append(url)
            return [{'cdx-api': 'https://127.0.0.1/private-index'}]
        result = discover({}, fetch)
        self.assertEqual(requests, [cc.REGISTRY])
        self.assertEqual(result['status'], 'partial')

    def test_wayback_failure_does_not_prevent_independent_captures(self):
        def fetch(url):
            if url.startswith('https://web.archive.org/'):
                raise TimeoutError('Wayback unavailable')
            if url == cc.REGISTRY:
                return [{'cdx-api': A}]
            return {'pages': 1} if 'showNumPages' in url else [indexed()]
        with patch.object(retry, '_now', return_value=NOW):
            result = archive_index.discover_archives({}, fetch, pages=1, extensions=('exe',), allowed=installers.official_source)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['commoncrawl']['status'], 'ok')
        capture, = result['artifacts'][SOURCE]['captures'].values()
        self.assertEqual(capture['provider'], 'commoncrawl')

    def test_combined_checkpoint_keeps_capture_records_once_and_resumes_both_providers(self):
        def fetch(url):
            if url.startswith('https://web.archive.org/'):
                return [['timestamp', 'original', 'digest'], ['20200101000000', SOURCE, 'wayback']]
            if url == cc.REGISTRY:
                return [{'cdx-api': A}]
            return {'pages': 1} if 'showNumPages' in url else [indexed()]
        with patch.object(retry, '_now', return_value=NOW):
            first = archive_index.discover_archives({}, fetch, pages=1, extensions=('exe',), allowed=installers.official_source)
            second = archive_index.discover_archives(json.loads(json.dumps(first)), fetch, pages=1,
                                                     extensions=('exe',), allowed=installers.official_source)
        self.assertEqual(len(second['artifacts'][SOURCE]['captures']), 2)
        self.assertEqual(second['artifacts'][SOURCE]['filename'], 'Sonos.exe')
        self.assertNotIn('artifacts', second['commoncrawl'])
        self.assertNotIn('artifacts', second['wayback'])
        self.assertEqual(second['status'], 'ok')


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

    def test_older_arc_record_ranges_are_supported(self):
        body = b'MZ older synthetic installer'
        http = b'HTTP/1.1 200 OK\r\nContent-Type: application/octet-stream\r\nContent-Length: '+str(len(body)).encode()+b'\r\n\r\n'+body
        record = f'{SOURCE} 0.0.0.0 20200101000000 application/http {len(http)}\n'.encode()+http+b'\n'
        data = gzip.compress(record)
        _, capture = warc()
        capture.update(filename='crawl-data/legacy/test.arc.gz', length=str(len(data)), digest='-')
        self.assertEqual(self.replay(data, capture)[0], body)

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

    def test_storage_paths_cannot_escape_the_fixed_archive_host(self):
        for filename in ('../private', 'https://outside.test/warc.gz', 'a?token=secret', '/absolute'):
            with self.subTest(filename=filename), self.assertRaises(ValueError):
                cc.capture_url({'filename': filename, 'offset': 0, 'length': 10})

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
                result = installers.archive_history(root, root/'work', 'fresh/archive', 'desktop', limit=1)
            self.assertEqual(result['artifact_total'], 1)
            self.assertEqual(github.uploads[0][3], b'MZ synthetic installer')
            receipt = json.loads((root/'data/apps/desktop-archive.json').read_text())
            observed = receipt['artifacts'][0]['acquisitions'][0]
            self.assertEqual(observed['acquisition_id'], 'commoncrawl:native')
            self.assertEqual(observed['archive_capture'], capture)
            self.assertNotIn('wayback_timestamp', observed)

    def test_wayback_backoff_does_not_block_native_firmware_publication_from_empty_state(self):
        import struct
        from test_upd import section
        from sonos_firmware.historical_firmware import archive_history
        from sonos_firmware.storage import atomic_json
        source = SOURCE.rsplit('/', 1)[0]+'/1.2-345-1-17.upd'
        body = section(1, 0, struct.pack('<III', 1, 2, 345)) + section(99, 0, b'synthetic firmware')
        data, capture = warc(body, source=source)
        capture['original_url'] = source
        index = {'errors': [{'url': 'https://web.archive.org/cdx', 'http_status': 429,
                            'next_attempt_at': (NOW+timedelta(days=1)).isoformat()}],
                 'artifacts': {source: {'version': '1.2-345', 'package_model': 17, 'captures': {'native': capture}}}}
        github = FakeGitHub()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'data/discovery/firmware-frontier.json', {'archive_index': index})
            with patch.object(retry, '_now', return_value=NOW), \
                 patch.object(cc.urllib.request, 'build_opener') as opener, \
                 patch.object(releases, 'remote_assets', side_effect=github.assets), \
                 patch.object(releases.subprocess, 'run', side_effect=github.command):
                opener.return_value.open.return_value = Response(data, capture)
                result = archive_history(root, root/'work', 'fresh/archive', limit=1)
            self.assertEqual(result['preserved'], 1)
            self.assertEqual(github.uploads[0][3], body)
            package, = json.loads((root/'data/catalog.json').read_text())['packages']
            self.assertEqual(package['version'], '1.2-345')
            self.assertEqual(package['artifact_status'], 'preserved')


if __name__ == '__main__':
    unittest.main()
