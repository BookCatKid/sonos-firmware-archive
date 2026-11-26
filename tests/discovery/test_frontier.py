import hashlib
import json
import struct
import sys
import tempfile
import unittest
import urllib.error
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch, Mock

from sonos_firmware import frontier
from sonos_firmware.crawl import crawl_manifests
from tests.discovery.test_archive_index import scan_index

HOST = 'https://update.sonos.com'
BASE = HOST + '/firmware/Prod/discovered/'


def scan_frontier(state, evidence, *, work_units=8, **options):
    attempts = 0
    checks = 0
    def offline(url):
        raise urllib.error.HTTPError(url, 404, 'not in fixture', {}, None)
    options.setdefault('fetch', offline)
    original = options.get('probe_fn', lambda candidate: {**asdict(candidate), 'available': False, 'http_status': 404})
    def probe(candidate):
        nonlocal attempts
        attempts += 1
        return original(candidate)
    options['probe_fn'] = probe
    ready = {'profiles': {}, 'responses': {}, 'errors': [], 'status': 'ok',
             'attempted_this_run': 0}
    def complete():
        nonlocal checks
        checks += 1
        return attempts >= work_units or checks >= 100 * work_units
    def single_profile(previous, location, catalog, *, stop, on_capture, fetch):
        from sonos_firmware.metadata_sweep import discover_profiles
        def one(profile):
            try:
                return fetch(profile)
            finally:
                stop.set()
        return discover_profiles(previous, location, catalog, stop=stop,
                                 on_capture=on_capture, fetch=one)
    source = (lambda *args, **kwargs: single_profile(*args, **kwargs)) if 'profile_fetch' in options else \
             (lambda *args, **kwargs: ready)
    with patch.object(frontier, 'winding_down', side_effect=complete), \
         patch.object(frontier, 'discover_profiles', side_effect=source):
        return frontier.discover(state, evidence, **options)


class FrontierTests(unittest.TestCase):
    def test_cold_start_protocol_hosts_get_independent_index_queries_after_a_broad_timeout(self):
        from urllib.parse import parse_qs, urlsplit
        from sonos_firmware import archive_index, commoncrawl
        root = HOST+'/firmware/latest/update.upm'
        requested = []
        def index(url):
            requested.append(url)
            if url == commoncrawl.REGISTRY:
                return [{'cdx-api': 'https://index.commoncrawl.org/CC-MAIN-2020-01-index'}]
            query = parse_qs(urlsplit(url).query)
            if urlsplit(url).hostname == 'web.archive.org':
                if query['url'] == ['sonos.com/']:
                    raise TimeoutError('broad domain query timed out')
                return []
            return {'pages': 0}
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'missing', {}, None)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frontier, 'protocol_roots', return_value=[root]), patch.object(frontier.time, 'sleep'):
            state = scan_frontier({}, Path(temporary), fetch=missing,
                probe_fn=lambda candidate: {**asdict(candidate), 'available': False, 'http_status': 404},
                metadata_fetch=lambda: (b'', {}), work_units=1, index_fetch=lambda previous: archive_index.discover_archives(previous, fetch=index))
        self.assertEqual(state['archive_index']['query_roots'],
                         {'sonos.com': 'sonos.com/', 'update.sonos.com': 'update.sonos.com/'})
        wayback = [parse_qs(urlsplit(url).query)['url'][0] for url in requested
                   if urlsplit(url).hostname == 'web.archive.org']
        self.assertIn('sonos.com/', wayback)
        self.assertIn('update.sonos.com/firmware/latest/', wayback)
        # The same budget now covers directory searches too; host traversal
        # keeps its position and continues in the next normal worker.
        requested.clear()
        following = archive_index.discover_archives(state['archive_index'], fetch=index)
        self.assertIn('update.sonos.com', following['completed_hosts'])
        self.assertEqual(following['pending_hosts'], ['sonos.com'])


    def test_challenged_channel_counterpart_is_retained_without_another_request(self):
        prod = BASE + 'update.upm'
        dev = prod.replace('/Prod/', '/Dev/')
        calls = []
        def fetch(url):
            calls.append(url)
            raise urllib.error.HTTPError(url, 429, 'retry later', {}, None)
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': prod}], [], Path(temporary), fetch=fetch,
                                    explore_channels=True, delay=0)
        self.assertEqual(calls, [prod])
        self.assertIn(dev, graph['pending_manifest_urls'])

    def test_inference_continues_other_namespaces_after_one_fails(self):
        from datetime import timedelta
        from sonos_firmware import retry
        from tests.apps.test_app_archive import NOW
        bad = 'https://new-firmware.sonos.com/bad/^1.0-1'
        good = 'https://new-firmware.sonos.com/good/^2.0-2'
        state = {'inferred_namespaces': {
            bad: {'next_model': 0, 'model_max': 0, 'status': 'failed', 'source': 'metadata-base',
                  'checked_at': '2000-01-01T00:00:00+00:00'},
            good: {'next_model': 0, 'model_max': 0, 'source': 'metadata-base'}}}
        seen = []
        def probe(candidate):
            seen.append(candidate.url)
            return {**asdict(candidate), 'available': False, 'http_status': 500 if '/bad/' in candidate.url else 404}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            for index in range(3):
                with patch.object(retry, '_now', return_value=NOW+timedelta(days=30*index)):
                    state = scan_frontier(state, Path(temporary), metadata_fetch=lambda: (b'', {}),
                        probe_fn=probe, work_units=1, )
                state = json.loads(json.dumps(state))
        self.assertTrue(any('/bad/' in url for url in seen))
        self.assertTrue(any('/good/' in url for url in seen))

    def test_indexed_packages_enter_discovery_without_any_available_manifest(self):
        known = HOST+'/firmware/history/1.2-345-1-17.upd'
        unknown = HOST+'/firmware/history/opaque.upd'
        rows = [['timestamp', 'original', 'digest'],
                ['20200101000000', known, 'known'], ['20200101000000', unknown, 'unknown']]
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'no manifest', {}, None)
        def probe(candidate):
            return {**asdict(candidate), 'available': candidate.url in {known, unknown},
                    'http_status': 200 if candidate.url in {known, unknown} else 404}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frontier, 'protocol_roots', return_value=[]), patch.object(frontier.time, 'sleep'):
            state = scan_frontier({}, Path(temporary), fetch=missing, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), work_units=32, index_fetch=lambda previous: scan_index(previous, fetch=lambda url: rows))
        self.assertEqual(state['manifests'], [])
        self.assertTrue(state['probes'][known]['available'])
        self.assertEqual(state['probes'][known]['package_model'], 17)
        self.assertTrue(state['probes'][unknown]['available'])
        self.assertIsNone(state['probes'][unknown]['package_model'])
        self.assertEqual(len(state['probes'][unknown]['archive_captures']), 1)


    def test_paused_catalog_hosts_cannot_reenter_crawl_or_starve_healthy_inference(self):
        known = [{'version': '1.0-12345', 'url': HOST+f'/firmware/directory-{n}/Sonos.dmg',
                  'filename': 'Sonos.dmg', 'package_model': 3, 'source': 'saved-archive-observation'} for n in range(3)]
        def challenged():
            raise urllib.error.HTTPError(HOST, 429, 'retry later', {}, None)
        def probe(c):
            self.assertFalse(c.url.startswith(HOST+'/'))
            return {**asdict(c), 'available': False, 'http_status': 404}
        fetch = Mock(return_value=b'<update_manifest version="1.1"></update_manifest>')
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = scan_frontier({}, Path(temporary), known_artifacts=known, work_units=1, metadata_fetch=challenged, fetch=fetch, probe_fn=probe)
        fetch.assert_not_called()
        original = [row for key, row in state['inferred_namespaces'].items() if key.startswith(HOST+'/')]
        peers = [row for key, row in state['inferred_namespaces'].items() if not key.startswith(HOST+'/')]
        self.assertTrue(all(row['next_model'] == 0 for row in original))
        self.assertEqual(sum(row['next_model'] for row in peers), 1)


    def test_model_discovery_crosses_the_default_255_boundary_without_configuration_changes(self):
        uri = (BASE + '^1.0-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0) + uri
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        state = {}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            for _ in range(9):
                state = scan_frontier(state, Path(temporary), fetch=missing,
                    metadata_fetch=lambda: (ups, {}), work_units=128, probe_fn=lambda candidate: {**asdict(candidate), 'available': candidate.package_model == 256,
                                               'http_status': 200 if candidate.package_model == 256 else 404})
        self.assertEqual({row['package_model'] for row in state['candidates']}, {256})
        self.assertTrue(all(row['next_model'] > 255 for row in state['inferred_namespaces'].values()))
        self.assertEqual(state['model_exploration']['policy'], 'unbounded-model-cursors')


    def test_own_installer_observations_expand_speaker_directories_without_a_manifest(self):
        row = {'version': '91.0-69300', 'package_model': 3, 'filename': 'Sonos.dmg',
               'url': HOST+'/firmware/Gold/known/Sonos.dmg', 'source': 'saved-archive-observation'}
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = scan_frontier({}, Path(temporary), known_artifacts=[row], fetch=missing,
                metadata_fetch=lambda: (b'', {}), probe_fn=lambda c: {**asdict(c), 'available': c.package_model == 2, 'http_status': 200 if c.package_model == 2 else 404})
        found = [row for row in state['candidates'] if row['source'] == 'observed-directory']
        self.assertEqual({row['package_model'] for row in found}, {2})
        self.assertEqual(state['inference_pending'], len(state['inferred_namespaces']))
        self.assertTrue(all('/firmware/Gold/known/91.0-69300-1-' in row['url'] for row in found))
        self.assertEqual(len(frontier.monitor_report(state, {'packages': []})['available_uncatalogued_candidates']), 2)


    def test_private_metadata_uri_is_reported_without_requesting_it(self):
        uri = b'https://127.0.0.1/firmware/update.upm\0'
        ups = struct.pack('<IIII', 0x35167F49, 14, len(uri)+16, 0) + uri
        fetch = Mock(return_value=b'<update_manifest version="1.1"></update_manifest>')
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]):
            state = scan_frontier({}, Path(temporary), metadata_fetch=lambda: (ups, {}),
                fetch=fetch)
        fetch.assert_not_called()
        self.assertEqual(state['status'], 'partial')
        self.assertTrue(any('127.0.0.1' in row.get('url', '') for row in state['bootstrap_errors']))


    def test_metadata_base_discovers_packages_even_when_its_manifest_is_gone(self):
        uri = (BASE + '^37.5-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri) + 16, 0) + uri
        def fetch(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            result = scan_frontier({}, Path(temporary), fetch=fetch, metadata_fetch=lambda: (ups, {}),
                probe_fn=lambda c: {**asdict(c), 'available': c.package_model == 2, 'http_status': 200 if c.package_model == 2 else 404})
        rows = result['candidates']
        self.assertEqual({row['package_model'] for row in rows}, {2})
        self.assertEqual(result['inference_pending'], len(result['inferred_namespaces']))
        self.assertEqual({row['version'] for row in rows}, {'37.5-12345'})
        self.assertTrue(all('/firmware/Prod/discovered/37.5-12345-1-' in row['url'] for row in rows))
        self.assertTrue(all(row['metadata_sha256'] == hashlib.sha256(ups).hexdigest() for row in rows))
        self.assertEqual(len(frontier.monitor_report(result, {'packages': []})['available_uncatalogued_candidates']), 2)

    def test_pending_roots_are_not_starved_by_current_feeds(self):
        roots = [HOST+f'/firmware/{name}/update.upm' for name in ('one','two','three')]
        visited = []
        def fetch(url):
            visited.append(url)
            return b'<update_manifest version="1.1"></update_manifest>'
        state = {}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=roots), \
             patch.object(frontier.time, 'sleep'):
            for _ in range(3):
                state = scan_frontier(state, Path(temporary), fetch=fetch,
                                           metadata_fetch=lambda: (b'', {}))
        self.assertTrue(set(roots).issubset(set(visited)))


    def test_profile_response_flows_into_the_same_manifest_and_package_frontier(self):
        uri = (BASE + '^42.0-45678').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri) + 16, 0) + uri
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = scan_frontier({}, Path(temporary), profile_fetch=lambda profile: ups, metadata_fetch=lambda: (b'', {}), fetch=lambda url: b'<update_manifest version="1.1"></update_manifest>',
                probe_fn=lambda c: {**asdict(c), 'available': True})
            self.assertTrue((Path(temporary)/(hashlib.sha256(ups).hexdigest()+'.ups')).is_file())
            self.assertEqual(state['metadata_profiles']['attempted_this_run'], 1)
            state = scan_frontier(state, Path(temporary), metadata_fetch=lambda: (b'', {}),
                fetch=lambda url: b'<update_manifest version="1.1"></update_manifest>',
                probe_fn=lambda c: {**asdict(c), 'available': True})
        self.assertTrue({0, 1}.issubset({row['package_model'] for row in state['candidates']}))
        self.assertIn(BASE+'update.upm', state['roots'])


    def test_protocol_artifact_is_discovered_without_an_initial_version_or_hash(self):
        url = 'https://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd'
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), patch.object(frontier.time, 'sleep'):
            result = scan_frontier({}, Path(temporary), metadata_fetch=lambda: (b'', {}),
                fetch=lambda url: b'<update_manifest version="1.1"></update_manifest>',
                artifact_roots=[{'url': url, 'package_model': 17, 'refresh_hours': 6}],
                probe_fn=lambda candidate: {**asdict(candidate), 'available': True, 'http_status': 200})
        row = result['probes'][url]
        self.assertEqual(row['version'], 'unknown')
        self.assertEqual(row['refresh_hours'], 6)
        self.assertEqual(row['source'], 'protocol-static')
        self.assertIn(url.rsplit('/', 1)[0] + '/update.upm', result['roots'])


    def test_private_content_location_is_never_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = crawl_manifests({}, [{'url': BASE + 'update.upm'}], [], Path(temporary),
                fetch=lambda url: (b'<update_manifest version="1.1"></update_manifest>', url, 'https://127.0.0.1/update.upm'), delay=0)
        self.assertFalse(result['manifests'])
        self.assertEqual(result['failures'][0]['kind'], 'error')


class PreservationTests(unittest.TestCase):


    def test_capture_failure_does_not_block_other_manifests_or_package_versions(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'scripts/firmware'))
        import discover_firmware as publisher
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / 'evidence'
            evidence.mkdir()
            payload = b'<update_manifest version="1.1"></update_manifest>'
            capture = evidence / 'good.upm'
            capture.write_bytes(payload)
            valid = {'url': BASE + 'good.upm', 'snapshot': str(capture), 'bytes': len(payload),
                     'sha256': hashlib.sha256(payload).hexdigest(), 'metadata': {}}
            invalid = {**valid, 'url': BASE + 'bad.upm', 'snapshot': str(root / 'outside.upm')}
            candidates = [{ 'url': BASE + str(i) + '.upd', 'version': f'{i}.0-1', 'package_model': 17,
                            'filename': f'{i}.0-1-1.upd', 'available': True} for i in (1, 2)]
            state = {'manifests': [invalid, valid], 'probes': {c['url']: c for c in candidates},
                     'checked_urls': [c['url'] for c in candidates]}
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({'packages': [], 'source_manifests': []}))
            operations = []
            def publish(path, manifest):
                operations.append('document')
                return {'status': 'verified', 'repository': 'owner/repo', 'release_tag': 'fixture',
                        'release_asset': path.name, 'release_url': 'https://github.com/fixture/asset',
                        'bytes': manifest['bytes'], 'sha256': manifest['sha256']}
            def acquire(*args, **kwargs):
                operations.append('firmware')
                return {
                     'preserved': 1, 'pending': 1, 'errors': [{'url': candidates[0]['url'], 'error': 'first version failed'}]
                }
            with patch.object(publisher.ReleaseSeries, 'publish', side_effect=publish), \
                 patch.object(publisher, 'archive_candidates', side_effect=acquire) as preserve:
                self.assertTrue(publisher.preserve(state, catalog, root / 'state.json', evidence, root, 'owner/repo'))
            preserve.assert_called_once()
            self.assertEqual(preserve.call_args.args[2], candidates)
            self.assertEqual(len(json.loads(catalog.read_text())['source_manifests']), 1)
            self.assertEqual(len(state['preservation_errors']), 2)
            self.assertEqual(operations, ['firmware', 'document'])


if __name__ == '__main__':
    unittest.main()
