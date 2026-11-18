"""Cold-start archive replay through the real acquisition and publication helpers."""
import gzip
import io
import json
import struct
import tempfile
import unittest
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import archive_replay, historical_firmware as history, releases, retry, release_series
from sonos_firmware.catalog import validate_catalog
from sonos_firmware.firmware_archive import catalog_at
from sonos_firmware.storage import atomic_json
from test_app_archive import FakeGitHub, NOW
from test_upd import section

URL = 'https://update-firmware.sonos.com/firmware/Prod/fixture/connectx.upd'
MANIFEST = URL.rsplit('/', 1)[0]+'/update.upm'
XML = b'<update_manifest version="1.1"><image model="17" version="1.0-1">1.0-1-1-17.upd</image></update_manifest>'


def capture(timestamp='20200101000000', url=URL):
    return {'timestamp': timestamp, 'original_url': url.replace('https:', 'http:'), 'digest': 'index-evidence'}


class Response(io.BytesIO):
    def __init__(self, url, data, headers=None):
        super().__init__(data)
        self.url, self.headers = url, headers or {}

    def geturl(self):
        return self.url


class HistoricalFirmwareTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.frontier = self.root/'data/discovery/firmware-frontier.json'
        self.github = FakeGitHub()
        self.responses = {}
        self.fetched = []
        self.addCleanup(patch.stopall)
        patch.object(releases, 'remote_assets', side_effect=self.github.assets).start()
        patch.object(releases.subprocess, 'run', side_effect=self.github.command).start()
        patch.object(release_series, 'matching_tags', side_effect=self.github.matching_tags).start()
        patch.object(releases, 'public_release', side_effect=self.github.public_release).start()
        patch.object(releases, 'upload_streaming', side_effect=self.github.upload_streaming).start()
        # Keep the actual redirect, source-identity and response validation in play.
        patch.object(archive_replay.urllib.request, 'build_opener', return_value=self).start()

    def open(self, request, **kwargs):
        self.fetched.append(request.full_url)
        value = self.responses[request.full_url]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            data, headers = value
        else:
            data, headers = value, {}
        return Response(request.full_url, data, headers)

    def index(self, captures=None, manifests=None):
        artifacts = {URL: {'version': 'unknown', 'package_model': None, 'captures': captures}} if captures else {}
        atomic_json(self.frontier, {'archive_index': {'artifacts': artifacts, 'entries':
            {MANIFEST: {'captures': manifests}} if manifests else {}}})

    def run_history(self, *, now=NOW, repository='fresh/archive', **kwargs):
        with patch.object(retry, '_now', return_value=now):
            return history.archive_history(self.root, self.root/'worker', repository, **kwargs)

    def add_firmware(self, identity, timestamp, version):
        item = capture(timestamp)
        self.responses[archive_replay.replay_url(URL, item)] = section(1, 0, struct.pack('<III', *version))
        return identity, item

    def test_distinct_versions_at_same_url_bootstrap_with_no_catalog_or_releases(self):
        captures = dict([self.add_firmware('old', '20200101000000', (1, 0, 1)),
                         self.add_firmware('new', '20210101000000', (2, 0, 2))])
        self.index(captures)
        self.assertFalse((self.root/'data/catalog.json').exists())
        self.assertEqual(self.github.releases, {})
        result = self.run_history()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['preserved'], 2)
        packages = catalog_at(self.root/'data/catalog.json')['packages']
        self.assertEqual({row['version'] for row in packages}, {'1.0-1', '2.0-2'})
        self.assertTrue(all(row['package_model'] is None for row in packages))
        self.assertEqual(len({row['release_asset'] for row in packages}), 2)
        self.assertTrue(all(row['sha256'] in row['release_asset'] for row in packages))
        self.assertEqual({row['retrievals'][0]['url'] for row in packages}, set(self.fetched))
        self.assertFalse((self.root/'data/discovery/firmware-acquisition.json').exists())
        self.assertEqual(list((self.root/'worker').iterdir()), [])
        self.assertEqual(validate_catalog(self.root), [])

    def test_lost_upload_response_recovers_without_replay_or_cached_payload(self):
        self.index(dict([self.add_firmware('old', '20200101000000', (1, 0, 1))]))
        self.github.lose_upload_response = True
        self.assertEqual(self.run_history()['preserved'], 0)
        self.assertEqual(list((self.root/'worker').iterdir()), [])
        self.responses.clear()
        result = self.run_history(now=NOW+timedelta(hours=2))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['preserved'], 1)
        self.assertEqual(len(self.fetched), 1)

    def test_preserved_capture_refresh_only_checks_destination_and_fresh_repository_republishes(self):
        self.index(dict([self.add_firmware('old', '20200101000000', (1, 0, 1))]))
        self.run_history()
        self.run_history(now=NOW+timedelta(days=8))
        self.assertEqual(len(self.fetched), 1)
        self.assertEqual(len(self.github.uploads), 1)
        self.run_history(repository='another/archive')
        self.assertEqual(len(self.fetched), 2)
        self.assertEqual(len(self.github.uploads), 2)
        self.assertEqual(self.github.uploads[-1][0], 'another/archive')

    def test_unattempted_index_captures_survive_index_reset(self):
        self.index(dict([self.add_firmware('old', '20200101000000', (1, 0, 1)),
                         self.add_firmware('new', '20210101000000', (2, 0, 2))]))
        self.assertEqual(self.run_history(limit=1)['pending'], 1)
        self.index()
        self.assertEqual(self.run_history(limit=1)['preserved'], 1)
        self.assertEqual(len(self.github.uploads), 2)

    def test_historical_manifest_replay_preserves_raw_gzip_and_discovers_vendor_relative_urls(self):
        item = capture(url=MANIFEST)
        self.index(manifests={'manifest': item})
        raw = gzip.compress(XML)
        self.responses[archive_replay.replay_url(MANIFEST, item)] = raw
        result = self.run_history()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['manifests']['preserved'], 1)
        self.assertEqual(self.github.uploads[0][3], raw)
        frontier = json.loads(self.frontier.read_text())
        self.assertEqual(frontier['candidates'][0]['url'], MANIFEST.replace('update.upm', '1.0-1-1-17.upd'))
        self.assertEqual(frontier['candidates'][0]['package_model'], 17)
        self.assertNotIn('available', frontier['candidates'][0])
        manifest = catalog_at(self.root/'data/catalog.json')['source_manifests'][0]
        self.assertEqual(manifest['publication']['repository'], 'fresh/archive')
        self.assertEqual(manifest['retrievals'][0]['archive_capture'], item)

    def test_manifest_upload_reconciles_after_cache_and_source_disappear(self):
        item = capture(url=MANIFEST)
        self.index(manifests={'manifest': item})
        self.responses[archive_replay.replay_url(MANIFEST, item)] = XML
        self.github.lose_upload_response = True
        with patch.object(history.ReleaseSeries, '_proof', side_effect=RuntimeError('worker interrupted after upload')):
            self.assertEqual(self.run_history()['manifests']['preserved'], 0)
        self.responses.clear()
        self.index()
        result = self.run_history(now=NOW+timedelta(hours=2))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['manifests']['preserved'], 1)
        self.assertEqual(len(self.fetched), 1)
        self.assertEqual(len(json.loads(self.frontier.read_text())['candidates']), 1)

    def test_historical_manifest_rollover_records_actual_partition(self):
        item = capture(url=MANIFEST)
        self.index(manifests={'manifest': item})
        self.responses[archive_replay.replay_url(MANIFEST, item)] = XML
        base = 'manifest-history-'+item['timestamp'][:8]
        self.github.releases[('fresh/archive', base)] = {f'old-{i}': {'size': 1} for i in range(1000)}
        result = self.run_history()
        self.assertEqual(result['manifests']['preserved'], 1)
        row, = catalog_at(self.root/'data/catalog.json')['source_manifests']
        self.assertEqual(row['release_tag'], base+'-part-2')
        self.assertEqual(row['publication']['release_tag'], row['release_tag'])
        journal = json.loads((self.root/'data/discovery/historical-manifest-acquisition.json').read_text())
        self.assertEqual(journal['sources']['fresh/archive']['manifest']['manifest']['release_tag'], row['release_tag'])

    def test_interrupted_overflow_upload_recovers_without_replay_or_cached_body(self):
        item = capture(url=MANIFEST)
        self.index(manifests={'manifest': item})
        self.responses[archive_replay.replay_url(MANIFEST, item)] = XML
        base = 'manifest-history-'+item['timestamp'][:8]
        self.github.releases[('fresh/archive', base)] = {f'old-{i}': {'size': 1} for i in range(1000)}
        with patch.object(history.ReleaseSeries, '_proof', side_effect=RuntimeError('worker interrupted after upload')):
            self.assertEqual(self.run_history()['manifests']['preserved'], 0)
        self.assertEqual(len(self.github.releases[('fresh/archive', base+'-part-2')]), 1)
        self.responses.clear()
        self.index()
        resumed = self.run_history(now=NOW+timedelta(hours=2))
        self.assertEqual(resumed['manifests']['preserved'], 1)
        self.assertEqual(len(self.fetched), 1)
        row, = catalog_at(self.root/'data/catalog.json')['source_manifests']
        self.assertEqual(row['release_tag'], base+'-part-2')

    def test_manifest_replay_retry_after_defers_new_captures_across_restarts(self):
        old, peer = capture(url=MANIFEST), capture('20210101000000', MANIFEST)
        self.index(manifests={'old': old})
        url = archive_replay.replay_url(MANIFEST, old)
        self.responses[url] = urllib.error.HTTPError(url, 429, 'retry later', {'Retry-After': '75'}, None)
        def archive(now):
            with patch.object(retry, '_now', return_value=now):
                frontier = json.loads(self.frontier.read_text())
                return history.archive_manifests(self.root, self.root/'worker', 'fresh/archive', frontier, limit=4)
        archive(NOW)
        self.index(manifests={'old': old, 'peer': peer})
        self.responses[archive_replay.replay_url(MANIFEST, peer)] = XML
        archive(NOW+timedelta(seconds=74))
        self.assertEqual(self.fetched, [url])
        self.responses[url] = XML
        resumed = archive(NOW+timedelta(seconds=76))
        self.assertEqual(resumed['preserved'], 2)
        self.assertEqual(len(self.fetched), 3)

    def test_manifest_publication_retry_after_defers_source_requests_and_resumes(self):
        old, peer = capture(url=MANIFEST), capture('20210101000000', MANIFEST)
        self.index(manifests={'old': old})
        url = archive_replay.replay_url(MANIFEST, old)
        self.responses[url] = XML
        self.github.inventory_failure = urllib.error.HTTPError('https://api.github.com/fixture', 429,
            'retry later', {'Retry-After': '75'}, None)
        def archive(now):
            with patch.object(retry, '_now', return_value=now):
                return history.archive_manifests(self.root, self.root/'worker', 'fresh/archive',
                    json.loads(self.frontier.read_text()), limit=4)
        first = archive(NOW)
        self.assertEqual(first['preserved'], 0)
        self.assertTrue(first['paused'])
        self.assertEqual(first['paused_hosts'], [])
        self.assertFalse(self.github.releases)
        self.index(manifests={'old': old, 'peer': peer})
        self.responses[archive_replay.replay_url(MANIFEST, peer)] = XML
        archive(NOW+timedelta(seconds=74))
        self.assertEqual(self.fetched, [url])
        self.assertEqual(archive(NOW+timedelta(seconds=76))['preserved'], 2)

    def test_manifest_replay_throttle_does_not_pause_an_independent_archive(self):
        old = capture(url=MANIFEST)
        other = {**capture('20210101000000', MANIFEST), 'provider': 'commoncrawl',
                 'filename': 'crawl/fixture.warc.gz', 'offset': '0', 'length': '100'}
        self.index(manifests={'old': old, 'other': other})
        visited = []
        def replay(url, item):
            host = archive_replay.capture_host(item)
            visited.append(host)
            if host == 'web.archive.org':
                raise urllib.error.HTTPError('https://web.archive.org/fixture', 429,
                    'retry later', {'Retry-After': '75'}, None)
            return Response('https://data.commoncrawl.org/crawl/fixture.warc.gz', XML)
        with patch.object(history, 'open_capture', side_effect=replay), patch.object(retry, '_now', return_value=NOW):
            result = history.archive_manifests(self.root, self.root/'worker', 'fresh/archive',
                json.loads(self.frontier.read_text()), limit=4)
        self.assertEqual(visited, ['web.archive.org', 'data.commoncrawl.org'])
        self.assertEqual(result['preserved'], 1)
        self.assertEqual(result['paused_hosts'], ['web.archive.org'])

    def test_new_manifest_backlog_does_not_starve_interrupted_publication_recovery(self):
        old = capture(url=MANIFEST)
        self.index(manifests={'old': old})
        self.responses[archive_replay.replay_url(MANIFEST, old)] = XML
        self.github.lose_upload_response = True
        with patch.object(history.ReleaseSeries, '_proof', side_effect=RuntimeError('worker interrupted after upload')):
            self.assertEqual(self.run_history()['manifests']['preserved'], 0)
        captures = {'old': old}
        for day in range(1, 9):
            item = capture(f'202101{day:02d}000000', MANIFEST)
            captures[str(day)] = item
            self.responses[archive_replay.replay_url(MANIFEST, item)] = XML
        self.index(manifests=captures)
        self.run_history(now=NOW+timedelta(hours=2), manifest_limit=4)
        journal = json.loads((self.root/'data/discovery/historical-manifest-acquisition.json').read_text())
        self.assertEqual(journal['sources']['fresh/archive']['old']['status'], 'preserved')
        self.assertEqual(len(self.fetched), 4)  # Original capture plus three new ones; recovery needs no GET.

    def test_unsupported_manifest_is_preserved_and_discovery_remains_partial(self):
        item = capture(url=MANIFEST)
        self.index(manifests={'manifest': item})
        self.responses[archive_replay.replay_url(MANIFEST, item)] = b'<update_manifest><broken></update_manifest>'
        result = self.run_history()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['manifests']['preserved'], 1)
        self.assertTrue(result['manifests']['errors'])
        self.assertEqual(len(self.github.uploads), 1)

    def test_truncation_does_not_publish_and_challenge_pauses_other_replays(self):
        captures = dict([self.add_firmware('old', '20200101000000', (1, 0, 1)),
                         self.add_firmware('new', '20210101000000', (2, 0, 2))])
        self.index(captures)
        first = archive_replay.replay_url(URL, captures['old'])
        self.responses[first] = (b'short', {'Content-Length': '100'})
        result = self.run_history()
        self.assertEqual(result['preserved'], 1)
        self.assertEqual(result['pending'], 1)
        self.assertIn('Content-Length', result['errors'][0]['error'])
        self.fetched.clear()
        self.responses[first] = urllib.error.HTTPError(first, 429, 'fixture challenge', {}, None)
        self.index(captures)
        result = self.run_history(now=NOW+timedelta(hours=2), repository='other/archive')
        self.assertEqual(result['preserved'], 0)
        self.assertEqual(len(self.fetched), 1)

    def test_html_payload_challenge_pauses_remaining_replays_without_http_error(self):
        captures = dict([self.add_firmware('old', '20200101000000', (1, 0, 1)),
                         self.add_firmware('new', '20210101000000', (2, 0, 2))])
        self.index(captures)
        first = archive_replay.replay_url(URL, captures['old'])
        self.responses[first] = (b'<html>challenge</html>', {'Content-Type': 'text/html'})
        result = self.run_history()
        self.assertEqual(result['pending'], 2)
        self.assertEqual(self.fetched, [first])
        self.assertEqual(self.github.uploads, [])

    def test_html_manifest_challenge_also_pauses_payload_replays(self):
        item = capture(url=MANIFEST)
        self.index(dict([self.add_firmware('old', '20200101000000', (1, 0, 1))]), {'manifest': item})
        first = archive_replay.replay_url(MANIFEST, item)
        self.responses[first] = (b'<html>challenge</html>', {'Content-Type': 'text/html'})
        result = self.run_history()
        self.assertEqual(result['pending'], 1)
        self.assertEqual(result['manifests']['pending'], 1)
        self.assertEqual(self.fetched, [first])
        self.assertEqual(self.github.uploads, [])

    def test_challenge_still_allows_pending_publication_to_reconcile(self):
        captures = dict([self.add_firmware('old', '20200101000000', (1, 0, 1))])
        self.index(captures)
        self.github.lose_upload_response = True
        self.assertEqual(self.run_history()['preserved'], 0)
        item = capture(url=MANIFEST)
        self.index(captures, {'manifest': item})
        manifest_replay = archive_replay.replay_url(MANIFEST, item)
        self.responses[manifest_replay] = (b'<html>challenge</html>', {'Content-Type': 'text/html'})
        result = self.run_history(now=NOW+timedelta(hours=2))
        self.assertEqual(result['preserved'], 1)
        self.assertEqual(result['manifests']['pending'], 1)
        self.assertEqual(self.fetched, [archive_replay.replay_url(URL, captures['old']), manifest_replay])
        self.assertEqual(len(self.github.uploads), 1)

    def test_saved_index_challenge_expires_and_both_queues_resume_without_new_discovery(self):
        captures = dict([self.add_firmware('old', '20200101000000', (1, 0, 1))])
        item = capture(url=MANIFEST)
        self.index(captures, {'manifest': item})
        self.responses[archive_replay.replay_url(MANIFEST, item)] = XML
        frontier = json.loads(self.frontier.read_text())
        frontier['archive_index']['errors'] = [{'http_status': 429,
            'next_attempt_at': (NOW+timedelta(hours=1)).isoformat()}]
        atomic_json(self.frontier, frontier)
        first = self.run_history()
        self.assertEqual(first['pending'], 1)
        self.assertEqual(first['manifests']['pending'], 1)
        self.assertEqual(self.fetched, [])
        second = self.run_history(now=NOW+timedelta(hours=2))
        self.assertEqual(second['status'], 'ok')
        self.assertEqual(second['preserved'], 1)
        self.assertEqual(second['manifests']['preserved'], 1)

    def test_undated_legacy_index_error_does_not_permanently_disable_replay(self):
        self.index(dict([self.add_firmware('old', '20200101000000', (1, 0, 1))]))
        frontier = json.loads(self.frontier.read_text())
        frontier['archive_index']['errors'] = [{'http_status': 403}]
        atomic_json(self.frontier, frontier)
        self.assertEqual(self.run_history()['preserved'], 1)


class ReplayIdentityTests(unittest.TestCase):
    def test_redirects_cannot_select_another_date_source_or_rewriting_mode(self):
        item = capture()
        url = archive_replay.replay_url(URL, item)
        redirect = archive_replay._ReplayRedirect(URL, item['timestamp'])
        for bad in (url.replace('20200101', '20200102'), url.replace('connectx', 'other'),
                    url.replace('id_/', '/'), url.replace('web.archive.org', 'example.com')):
            with self.subTest(url=bad), self.assertRaises(ValueError):
                redirect.redirect_request(urllib.request.Request(url), None, 302, 'redirect', {}, bad)

    def test_memento_mismatch_and_html_error_page_are_rejected(self):
        item = capture()
        url = archive_replay.replay_url(URL, item)
        for headers in ({'Memento-Datetime': 'Thu, 02 Jan 2020 00:00:00 GMT'}, {'Content-Type': 'text/html'}):
            response = Response(url, b'error', headers)
            with patch.object(archive_replay.urllib.request.OpenerDirector, 'open', return_value=response):
                with self.assertRaises(ValueError):
                    archive_replay.open_capture(URL, item)
            self.assertTrue(response.closed)


if __name__ == '__main__':
    unittest.main()
