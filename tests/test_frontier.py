import hashlib
import importlib.util
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

HOST = 'https://update.sonos.com'
BASE = HOST + '/firmware/Prod/discovered/'


class FrontierTests(unittest.TestCase):
    def test_missing_prod_sibling_still_discovers_dev_history(self):
        root = HOST + '/firmware/latest/update.upm'
        prod = HOST + '/firmware/Prod/history/update.upm'
        dev = prod.replace('/Prod/', '/Dev/')
        installer = dev.rsplit('/', 1)[0] + '/1.0-12345-1-3.exe'
        body = ('<update_manifest version="1.1"><image model="3" version="1.0-12345">'
                + prod.rsplit('/', 1)[0] + '/1.0-12345-1-3.exe</image></update_manifest>').encode()
        calls = []
        def fetch(url):
            calls.append(url)
            if url == root:
                return body
            if url == dev:
                return ('<update_manifest version="1.1"><image model="3" version="1.0-12345">'
                        + installer + '</image></update_manifest>').encode()
            raise urllib.error.HTTPError(url, 404, 'missing', {}, None)
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': root}], [], Path(temporary), fetch=fetch,
                                    explore_channels=True, delay=0)
        self.assertIn(dev, calls)
        self.assertIn(installer, {candidate.url for candidate in graph['candidates']})
        self.assertEqual(calls.count(prod), 1)
        self.assertEqual(calls.count(dev), 1)
        self.assertIn({'url': dev, 'parent': prod, 'reason': 'channel-counterpart'}, graph['edges'])

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

    def test_one_slot_inference_does_not_repeat_only_a_failed_namespace(self):
        from datetime import timedelta
        from sonos_firmware import retry
        from test_app_archive import NOW
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
                    state = frontier.discover(state, Path(temporary), metadata_fetch=lambda: (b'', {}),
                        probe_fn=probe, model_max=0, inference_limit=1)
                state = json.loads(json.dumps(state))
        self.assertIn('/bad/', seen[0])
        self.assertIn('/good/', seen[1])
        self.assertIn('/bad/', seen[2])

    def test_indexed_packages_enter_discovery_without_any_available_manifest(self):
        from sonos_firmware.archive_index import discover_sources
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
            state = frontier.discover({}, Path(temporary), fetch=missing, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), model_max=0, inference_limit=1,
                index_fetch=lambda previous: discover_sources(previous, fetch=lambda url: rows, pages=1))
        self.assertEqual(state['manifests'], [])
        self.assertTrue(state['probes'][known]['available'])
        self.assertEqual(state['probes'][known]['package_model'], 17)
        self.assertTrue(state['probes'][unknown]['available'])
        self.assertIsNone(state['probes'][unknown]['package_model'])
        self.assertEqual(len(state['probes'][unknown]['archive_captures']), 1)

    def test_one_request_inference_budget_rotates_between_fresh_namespaces(self):
        uri = (BASE + '^1.0-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0) + uri
        visited = []
        def probe(c):
            visited.append(c)
            return {**asdict(c), 'available': False, 'http_status': 404}
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        state = {}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            for _ in range(2):
                state = frontier.discover(state, Path(temporary), inference_limit=1, model_max=2,
                    metadata_fetch=lambda: (ups, {}), fetch=missing, probe_fn=probe)
        self.assertEqual([row.package_model for row in visited], [0, 0])
        self.assertEqual(len({row.url for row in visited}), 2)

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
            state = frontier.discover({}, Path(temporary), known_artifacts=known, inference_limit=1,
                metadata_fetch=challenged, fetch=fetch, probe_fn=probe)
        fetch.assert_not_called()
        original = [row for key, row in state['inferred_namespaces'].items() if key.startswith(HOST+'/')]
        peers = [row for key, row in state['inferred_namespaces'].items() if not key.startswith(HOST+'/')]
        self.assertTrue(all(row['next_model'] == 0 for row in original))
        self.assertEqual(sum(row['next_model'] for row in peers), 1)

    def test_direct_manifest_image_advances_its_inference_cursor_without_losing_provenance(self):
        root = HOST+'/firmware/feed/update.upm'
        target = HOST+'/firmware/directory/1.0-12345-1-0.upd'
        body = f'<update_manifest version="1.1"><image model="0" version="1.0-12345">{target}</image></update_manifest>'.encode()
        def fetch(url):
            return body if url == root else b'<update_manifest version="1.1"></update_manifest>'
        def probe(c):
            return {**asdict(c), 'available': c.package_model == 0, 'http_status': 200 if c.package_model == 0 else 404}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[root]), \
             patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), model_max=2, fetch=fetch, probe_fn=probe,
                                      metadata_fetch=lambda: (b'', {}))
        self.assertEqual(state['inference_pending'], 0)
        self.assertEqual(next(row for row in state['candidates'] if row['url'] == target)['source'], 'image')
        self.assertEqual(len(state['candidates']), 2)

    def test_transient_namespace_failure_retries_its_model_without_blocking_its_peer(self):
        uri = (BASE + '^1.0-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0) + uri
        visited = []
        def probe(c):
            visited.append((c.url, c.package_model))
            failed = c.url.startswith(HOST+'/') and c.package_model == 1
            return {**asdict(c), 'available': False, 'http_status': 503 if failed else 404}
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), model_max=2, fetch=missing, probe_fn=probe,
                                      metadata_fetch=lambda: (ups, {}))
            self.assertEqual(state['inference_pending'], 2)
            self.assertFalse(any(url.startswith(HOST+'/') and model == 2 for url, model in visited))
            failed = next(row for row in state['inferred_namespaces'].values() if row.get('status') == 'failed')
            def healthy_peer(candidate):
                self.assertFalse(candidate.url.startswith(HOST+'/'), 'failed namespace retried before backoff')
                return {**asdict(candidate), 'available': False, 'http_status': 404}
            state = frontier.discover(state, Path(temporary), model_max=2, fetch=missing,
                probe_fn=healthy_peer, metadata_fetch=lambda: (ups, {}))
            failed = next(row for row in state['inferred_namespaces'].values() if row.get('status') == 'failed')
            failed['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
            state = frontier.discover(state, Path(temporary), model_max=2, fetch=missing,
                probe_fn=lambda c: {**asdict(c), 'available': False, 'http_status': 404}, metadata_fetch=lambda: (ups, {}))
        self.assertEqual(state['inference_pending'], 0)

    def test_many_observed_directories_keep_negative_probe_state_compact(self):
        known = [{'version': '1.0-12345', 'url': HOST+f'/firmware/directory-{n}/Sonos.dmg',
                  'filename': 'Sonos.dmg', 'package_model': 3, 'source': 'saved-archive-observation'} for n in range(61)]
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), known_artifacts=known, inference_limit=16,
                fetch=missing, metadata_fetch=lambda: (b'', {}),
                probe_fn=lambda c: {**asdict(c), 'available': False, 'http_status': 404})
        self.assertEqual(len(state['inferred_namespaces']), 122)
        self.assertEqual(len(state['candidates']), 122)
        self.assertTrue(all(row['filename'] == 'Sonos.dmg' for row in state['candidates']))
        self.assertEqual(state['inference_pending'], 61 * 512 - 16)

    def test_negative_namespace_probes_widen_without_recording_a_url_inventory(self):
        uri = (BASE + '^1.0-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0) + uri
        visited = []
        def probe(candidate):
            visited.append((candidate.url, candidate.package_model))
            return {**asdict(candidate), 'available': False, 'http_status': 404}
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        state = {}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            for _ in range(3):
                state = frontier.discover(state, Path(temporary), model_max=2, inference_limit=2,
                    metadata_fetch=lambda: (ups, {}), fetch=missing, probe_fn=probe)
            self.assertEqual(state['inference_pending'], 0)
            for _ in range(4):
                state = frontier.discover(state, Path(temporary), model_max=2, inference_limit=2,
                    metadata_fetch=lambda: (ups, {}), fetch=missing, probe_fn=probe)
        self.assertEqual(len(visited), 14)
        self.assertEqual({model for _, model in visited}, {0, 1, 2, 3})
        self.assertTrue(all(row['model_max'] == 5 for row in state['inferred_namespaces'].values()))
        self.assertEqual(state['candidates'], [])
        self.assertEqual(state['probes'], {})
        self.assertEqual(len(state['inferred_namespaces']), 2)

    def test_model_discovery_crosses_the_default_255_boundary_without_configuration_changes(self):
        uri = (BASE + '^1.0-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0) + uri
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        state = {}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            for _ in range(9):
                state = frontier.discover(state, Path(temporary), fetch=missing,
                    metadata_fetch=lambda: (ups, {}), inference_limit=128,
                    probe_fn=lambda candidate: {**asdict(candidate), 'available': candidate.package_model == 256,
                                               'http_status': 200 if candidate.package_model == 256 else 404})
        self.assertEqual({row['package_model'] for row in state['candidates']}, {256})
        self.assertTrue(all(row['model_max'] == 511 for row in state['inferred_namespaces'].values()))
        self.assertEqual(state['model_exploration']['policy'], 'expanding-passes')

    def test_raising_initial_model_range_above_4095_preserves_the_cursor_and_request_budget(self):
        uri = (BASE + '^1.0-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0) + uri
        visited = []
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        def probe(candidate):
            visited.append(candidate.package_model)
            return {**asdict(candidate), 'available': False, 'http_status': 404}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), model_max=1, fetch=missing,
                metadata_fetch=lambda: (ups, {}), inference_limit=2, probe_fn=probe)
            state = frontier.discover(state, Path(temporary), model_max=5000, fetch=missing,
                metadata_fetch=lambda: (ups, {}), inference_limit=2, probe_fn=probe)
        self.assertEqual(visited, [0, 0, 1, 1])
        self.assertTrue(all(row['model_max'] == 5000 and row['next_model'] == 2
                            for row in state['inferred_namespaces'].values()))

    def test_cached_available_model_does_not_make_later_models_skip_their_cursor(self):
        uri = (BASE + '^1.0-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0) + uri
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        def probe(candidate):
            return {**asdict(candidate), 'available': candidate.package_model == 0,
                    'http_status': 200 if candidate.package_model == 0 else 404}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), model_max=1, fetch=missing,
                                      metadata_fetch=lambda: (ups, {}), inference_limit=8, probe_fn=probe)
            state = frontier.discover(state, Path(temporary), model_max=1, fetch=missing,
                                      metadata_fetch=lambda: (ups, {}), inference_limit=8, probe_fn=probe)
        self.assertTrue(all(row['next_model'] == 4 for row in state['inferred_namespaces'].values()))
        self.assertTrue(all(row['model_max'] == 3 for row in state['inferred_namespaces'].values()))

    def test_legacy_completed_range_widens_without_waiting_for_its_old_refresh_time(self):
        uri = (BASE + '^1.0-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0) + uri
        state = {'inferred_namespaces': {BASE+'^1.0-12345': {
            'next_model': 3, 'model_max': 2, 'status': 'complete', 'next_attempt_at': '2099-01-01T00:00:00+00:00'}}}
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            result = frontier.discover(state, Path(temporary), model_max=2, fetch=missing,
                metadata_fetch=lambda: (ups, {}), inference_limit=2,
                probe_fn=lambda candidate: {**asdict(candidate), 'available': False, 'http_status': 404})
        original = result['inferred_namespaces'][BASE+'^1.0-12345']
        self.assertEqual(original['model_max'], 5)
        self.assertEqual(original['next_model'], 1)

    def test_saved_namespace_migration_needs_no_rediscovery_and_preserves_failed_cooldowns(self):
        completed = BASE+'^1.0-12345'
        failed = BASE+'other/^1.0-12345'
        future = '2099-01-01T00:00:00+00:00'
        state = {'inferred_namespaces': {
            completed: {'source': 'metadata-base', 'next_model': 3, 'model_max': 2, 'status': 'complete', 'next_attempt_at': future},
            failed: {'source': 'metadata-base', 'next_model': 1, 'model_max': 2, 'status': 'failed', 'next_attempt_at': future}}}
        visited = []
        def probe(candidate):
            visited.append(candidate.url)
            return {**asdict(candidate), 'available': False, 'http_status': 404}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            result = frontier.discover(state, Path(temporary), model_max=2, inference_limit=1,
                metadata_fetch=lambda: (b'', {}), fetch=lambda url: self.fail('unexpected namespace rediscovery'),
                probe_fn=probe)
        self.assertEqual(len(visited), 1)
        self.assertEqual(result['inferred_namespaces'][completed]['model_max'], 5)
        self.assertEqual(result['inferred_namespaces'][failed]['next_model'], 1)
        self.assertEqual(result['inferred_namespaces'][failed]['next_attempt_at'], future)

    def test_own_installer_observations_expand_speaker_directories_without_a_manifest(self):
        row = {'version': '91.0-69300', 'package_model': 3, 'filename': 'Sonos.dmg',
               'url': HOST+'/firmware/Gold/known/Sonos.dmg', 'source': 'saved-archive-observation'}
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), known_artifacts=[row], model_max=2, fetch=missing,
                metadata_fetch=lambda: (b'', {}), probe_fn=lambda c: {**asdict(c), 'available': c.package_model == 2, 'http_status': 200 if c.package_model == 2 else 404})
        found = [row for row in state['candidates'] if row['source'] == 'observed-directory']
        self.assertEqual({row['package_model'] for row in found}, {2})
        self.assertEqual(state['inference_pending'], 0)
        self.assertTrue(all('/firmware/Gold/known/91.0-69300-1-' in row['url'] for row in found))
        self.assertEqual(len(frontier.monitor_report(state, {'packages': []})['available_uncatalogued_candidates']), 2)

    def test_unknown_manifest_structure_cannot_report_complete_discovery(self):
        root = HOST+'/firmware/current/update.upm'
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[root]), \
             patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), metadata_fetch=lambda: (b'', {}),
                fetch=lambda url: b'<update_manifest version="99"></update_manifest>')
        self.assertEqual(state['status'], 'partial')
        self.assertIn('unrecognized', state['manifests'][0]['metadata']['warnings'][0])

    def test_private_metadata_uri_is_reported_without_requesting_it(self):
        uri = b'https://127.0.0.1/firmware/update.upm\0'
        ups = struct.pack('<IIII', 0x35167F49, 14, len(uri)+16, 0) + uri
        fetch = Mock(return_value=b'<update_manifest version="1.1"></update_manifest>')
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]):
            state = frontier.discover({}, Path(temporary), metadata_fetch=lambda: (ups, {}),
                fetch=fetch)
        fetch.assert_not_called()
        self.assertEqual(state['status'], 'partial')
        self.assertTrue(any('127.0.0.1' in row.get('url', '') for row in state['bootstrap_errors']))

    def test_publication_identity_matches_observed_urls_instead_of_filenames(self):
        candidate = {'url': HOST+'/firmware/one/1.2-3-1-8.upd'}
        packages = [{'artifact_status': 'preserved', 'source_url': candidate['url'].replace('https:', 'http:')}]
        self.assertTrue(frontier.candidate_is_preserved(candidate, packages))
        alternate = {'url': candidate['url'].replace('/one/', '/two/')}
        self.assertFalse(frontier.candidate_is_preserved(alternate, packages))
        packages[0]['source_urls'] = [alternate['url']]
        self.assertTrue(frontier.candidate_is_preserved(alternate, packages))

    def test_metadata_base_discovers_packages_even_when_its_manifest_is_gone(self):
        uri = (BASE + '^37.5-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri) + 16, 0) + uri
        def fetch(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            result = frontier.discover({}, Path(temporary), fetch=fetch, metadata_fetch=lambda: (ups, {}),
                model_max=2, probe_fn=lambda c: {**asdict(c), 'available': c.package_model == 2, 'http_status': 200 if c.package_model == 2 else 404})
        rows = result['candidates']
        self.assertEqual({row['package_model'] for row in rows}, {2})
        self.assertEqual(result['inference_pending'], 0)
        self.assertEqual({row['version'] for row in rows}, {'37.5-12345'})
        self.assertTrue(all('/firmware/Prod/discovered/37.5-12345-1-' in row['url'] for row in rows))
        self.assertTrue(all(row['metadata_sha256'] == hashlib.sha256(ups).hexdigest() for row in rows))
        self.assertEqual(len(frontier.monitor_report(result, {'packages': []})['available_uncatalogued_candidates']), 2)

    def test_pending_roots_are_not_starved_by_a_small_manifest_budget(self):
        roots = [HOST+f'/firmware/{name}/update.upm' for name in ('one','two','three')]
        visited = []
        def fetch(url):
            visited.append(url)
            return b'<update_manifest version="1.1"></update_manifest>'
        state = {}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=roots), \
             patch.object(frontier.time, 'sleep'):
            for _ in range(3):
                state = frontier.discover(state, Path(temporary), max_manifests=1, fetch=fetch,
                                           metadata_fetch=lambda: (b'', {}))
        self.assertEqual(visited, roots)

    def test_current_feeds_rotate_without_consuming_the_deferred_queue_budget(self):
        roots = [HOST+f'/firmware/live-{n}/update.upm' for n in range(3)]
        pending = [HOST+f'/firmware/old-{n}/update.upm' for n in range(20)]
        visited = []
        def fetch(url):
            visited.append(url)
            return b'<update_manifest version="1.1"></update_manifest>'
        state = {'pending_manifest_urls': pending}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=roots), \
             patch.object(frontier.time, 'sleep'):
            for _ in range(3):
                state = frontier.discover(state, Path(temporary), max_manifests=4, fetch=fetch,
                                           metadata_fetch=lambda: (b'', {}))
        self.assertEqual(visited[::4], roots)
        self.assertEqual([url for url in visited if url in pending], pending[:9])

    def test_profile_response_flows_into_the_same_manifest_and_package_frontier(self):
        uri = (BASE + '^42.0-45678').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri) + 16, 0) + uri
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), profile_limit=1, profile_count=2,
                profile_fetch=lambda profile: ups, metadata_fetch=lambda: (b'', {}), model_max=1,
                fetch=lambda url: b'<update_manifest version="1.1"></update_manifest>',
                probe_fn=lambda c: {**asdict(c), 'available': True})
            self.assertTrue((Path(temporary)/(hashlib.sha256(ups).hexdigest()+'.ups')).is_file())
        self.assertEqual(state['metadata_profiles']['attempted_this_run'], 1)
        self.assertEqual({row['package_model'] for row in state['candidates']}, {0, 1})
        self.assertIn(BASE+'update.upm', state['roots'])

    def test_profile_challenge_pauses_its_host_but_not_other_protocol_hosts(self):
        roots = [HOST+'/firmware/current/update.upm', 'https://update-stage.sonos.com/firmware/current/update.upm']
        seen = []
        def profile_fetch(profile):
            raise urllib.error.HTTPError(HOST, 429, 'retry later', {}, None)
        def fetch(url):
            seen.append(url)
            return b'<update_manifest version="1.1"></update_manifest>'
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=roots), \
             patch.object(frontier.time, 'sleep'), patch.object(frontier, 'fetch_update_metadata') as baseline:
            state = frontier.discover({}, Path(temporary), profile_limit=2, profile_count=2,
                profile_fetch=profile_fetch, metadata_fetch=baseline, fetch=fetch)
        self.assertEqual(seen, [roots[1]])
        self.assertIn(roots[0], state['pending_manifest_urls'])
        self.assertEqual(state['blocked_hosts'], ['update.sonos.com'])
        self.assertEqual(state['metadata_profiles']['attempted_this_run'], 1)
        baseline.assert_not_called()

    def test_saved_unavailable_observations_resume_for_all_models(self):
        row = {'version': '1.0-1', 'package_model': 8, 'url': BASE+'old.upd',
               'filename': 'old.upd', 'source': 'saved-archive-observation'}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'), patch.object(frontier, 'crawl_manifests', return_value={
                'manifests': [], 'candidates': [], 'blocked_hosts': [], 'pending_manifest_urls': [], 'failures': []}) as crawl:
            result = frontier.discover({}, Path(temporary), metadata_fetch=lambda: (b'', {}), known_artifacts=[row],
                probe_fn=lambda candidate: {**asdict(candidate), 'available': True, 'http_status': 200})
        self.assertIn(row['url'], result['checked_urls'])
        self.assertIn({'url': BASE+'update.upm'}, crawl.call_args.args[1])

    def test_protocol_artifact_is_discovered_without_an_initial_version_or_hash(self):
        url = 'https://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd'
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), patch.object(frontier.time, 'sleep'):
            result = frontier.discover({}, Path(temporary), metadata_fetch=lambda: (b'', {}),
                fetch=lambda url: b'<update_manifest version="1.1"></update_manifest>',
                artifact_roots=[{'url': url, 'package_model': 17, 'refresh_hours': 6}],
                probe_fn=lambda candidate: {**asdict(candidate), 'available': True, 'http_status': 200})
        row = result['probes'][url]
        self.assertEqual(row['version'], 'unknown')
        self.assertEqual(row['refresh_hours'], 6)
        self.assertEqual(row['source'], 'protocol-static')
        self.assertIn(url.rsplit('/', 1)[0] + '/update.upm', result['roots'])

    def test_invalid_protocol_configuration_is_rejected_before_network_work(self):
        valid = {'url': BASE+'static.upd', 'package_model': 17}
        invalid = [None, {}, {**valid, 'url': BASE.replace('https:', 'http:')+'static.upd'},
                   {**valid, 'url': BASE.replace('update.sonos.com', 'update.sonos.com:444')+'static.upd'},
                   {**valid, 'package_model': True}, {**valid, 'refresh_hours': float('inf')},
                   {**valid, 'refresh_hours': '6'}]
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'crawl_manifests') as crawl, \
             patch.object(frontier, 'fetch_update_metadata') as metadata:
            for root in invalid:
                with self.subTest(root=root), self.assertRaises(ValueError):
                    frontier.discover({}, Path(temporary), artifact_roots=[root], metadata_fetch=metadata)
            crawl.assert_not_called()
            metadata.assert_not_called()

    def test_empty_state_discovers_base_headers_channels_and_every_model(self):
        root = HOST + '/firmware/swgen/1/latest/update.upm?anonymous=1'
        canonical = BASE + 'update.upm'
        xml = b'<update_manifest version="1.1" base_url="https://update.sonos.com/firmware/Gold/history/^28.1-83040"><image model="17" version="57.18-44230">57.18-44230-1-17.upd</image><image model="8" version="57.18-44230">57.18-44230-1-8.upd</image></update_manifest>'
        history = b'<update_manifest version="1.1"><image model="1" version="28.1-83040">28.1-83040-1-1.upd</image></update_manifest>'
        calls = []
        def fetch(url):
            calls.append(url)
            if url == root: return xml, root, canonical
            if url == HOST + '/firmware/Gold/history/update.upm': return history
            raise urllib.error.HTTPError(url, 404, 'missing', {}, None)
        def probe(candidate): return {**asdict(candidate), 'available': True, 'http_status': 200, 'bytes': 1}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[root]), patch.object(frontier.time, 'sleep'):
            result = frontier.discover({}, Path(temporary), fetch=fetch, probe_fn=probe, metadata_fetch=lambda: (b'', {}))
        self.assertTrue(result['inference_pending'] > 0)
        self.assertLess(len(result['candidates']), 150)
        self.assertTrue({1, 8, 17}.issubset({c['package_model'] for c in result['candidates'] if c['source'] == 'image'}))
        self.assertIn(HOST + '/firmware/Dev/discovered/update.upm', calls)
        self.assertTrue(any(e['reason'] == 'content-location' for e in result['graph']['edges']))
        self.assertTrue(any(e['reason'] == 'manifest-base' for e in result['graph']['edges']))

    def test_legacy_ups_supplies_roots_without_a_campaign_list(self):
        uri = (BASE + 'update.upm').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 14, len(uri) + 16, 0) + uri
        xml = b'<update_manifest version="1.1"></update_manifest>'
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), patch.object(frontier.time, 'sleep'):
            result = frontier.discover({}, Path(temporary), fetch=lambda url: xml, metadata_fetch=lambda: (ups, {}))
        self.assertIn(BASE + 'update.upm', result['roots'])
        self.assertIn(BASE + 'update.upm', {m['url'] for m in result['manifests']})

    def test_resume_retains_versions_and_does_not_reuse_stale_head_success(self):
        old = {'url': BASE + 'old.upd', 'version': '1.0-1', 'package_model': 17, 'filename': 'old.upd', 'source': 'image'}
        state = {'candidates': [old], 'probes': {old['url']: {**old, 'available': True}},
                 'manifests': [{'url': BASE + 'update.upm', 'sha256': 'old-hash', 'snapshot': 'old-capture'}]}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]):
            result = frontier.discover(state, Path(temporary), seconds=0, metadata_fetch=lambda: (b'', {}))
        self.assertEqual(result['manifests'][0]['sha256'], 'old-hash')
        self.assertEqual(frontier.monitor_report(result, {'packages': []})['available_uncatalogued_candidates'], [])
        self.assertTrue(result['pending_manifest_urls'])

    def test_a_challenged_host_does_not_block_another_official_host(self):
        blocked = HOST + '/firmware/first/update.upm'
        skipped = HOST + '/firmware/second/update.upm'
        good = 'https://update-stage.sonos.com/firmware/Prod/good/update.upm'
        calls = []
        def fetch(url):
            calls.append(url)
            if url == blocked: raise urllib.error.HTTPError(url, 429, 'slow down', {}, None)
            return b'<update_manifest version="1.1"></update_manifest>'
        with tempfile.TemporaryDirectory() as temporary:
            result = crawl_manifests({}, [{'url': u} for u in (blocked, skipped, good)], [], Path(temporary), fetch=fetch, delay=0)
        self.assertNotIn(skipped, calls)
        self.assertIn(good, calls)
        self.assertIn(skipped, result['pending_manifest_urls'])

    def test_private_content_location_is_never_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = crawl_manifests({}, [{'url': BASE + 'update.upm'}], [], Path(temporary),
                fetch=lambda url: (b'<update_manifest version="1.1"></update_manifest>', url, 'https://127.0.0.1/update.upm'), delay=0)
        self.assertFalse(result['manifests'])
        self.assertEqual(result['failures'][0]['kind'], 'error')

    def test_protocol_roots_have_no_opaque_release_names(self):
        roots = frontier.protocol_roots()
        self.assertEqual(len(roots), 8)
        self.assertTrue(all('/swgen/' in u and '/latest/update.upm?' in u for u in roots))
        self.assertFalse(any('/Prod/' in u or '/Dev/' in u for u in roots))

    def test_previously_checked_candidates_rotate_when_a_run_budget_is_small(self):
        rows = [{'url': BASE + f'{i}.upd', 'version': f'{i}.0-1', 'package_model': 17,
                 'filename': f'{i}.upd', 'source': 'image'} for i in (1, 2, 3)]
        peers = [{**r, 'url': r['url'].replace('update.sonos.com', 'update-firmware.sonos.com')} for r in rows]
        state = {'candidates': rows + peers, 'probe_sequence': 6,
                 'probes': {r['url']: {**r, 'attempt_sequence': i} for i, r in enumerate(rows + peers, 1)}}
        visited = []
        def probe(candidate):
            visited.append(candidate.url)
            return {**asdict(candidate), 'available': False, 'http_status': 404}
        def graph(*args, **kwargs):
            return {'manifests': [], 'candidates': [], 'blocked_hosts': [],
                    'pending_manifest_urls': [], 'failures': []}
        with tempfile.TemporaryDirectory() as temporary:
            for _ in range(2):
                with patch.object(frontier, 'protocol_roots', return_value=[]), \
                     patch.object(frontier, 'crawl_manifests', side_effect=graph), \
                     patch.object(frontier.time, 'monotonic', side_effect=[0, 0, 2]), \
                     patch.object(frontier.time, 'sleep'):
                    state = frontier.discover(state, Path(temporary), seconds=1, probe_fn=probe,
                                              metadata_fetch=lambda: (b'', {}))
        self.assertEqual(visited, [rows[0]['url'], rows[1]['url']])


class PreservationTests(unittest.TestCase):
    def test_new_document_registers_after_an_earlier_release_inventory_failure(self):
        from sonos_firmware import releases
        from sonos_firmware.firmware_archive import register_document
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        import discover_firmware as publisher
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root/'data/catalog.json'
            manifests = []
            for label in ('old', 'new'):
                body = label.encode()
                (root/(label+'.upm')).write_bytes(body)
                manifests.append({'url': BASE+label+'.upm', 'snapshot': label+'.upm',
                                  'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'metadata': {}})
            tag = 'manifest-snapshots-fixture'
            old, new = manifests
            asset = {'size': new['bytes'], 'digest': 'sha256:'+new['sha256'],
                     'browser_download_url': 'https://github.com/owner/repo/releases/download/'+tag+'/'+publisher.document_name(new)}
            proof = releases.publication_receipt('owner/repo', tag, publisher.document_name(old), old,
                                                 {'url': 'https://github.com/owner/repo/releases/download/'+tag+'/old'})
            register_document(catalog, old, proof)
            state = {'manifests': manifests, 'probes': {}}
            with patch.object(publisher, 'datetime') as clock, \
                 patch.object(releases, 'remote_assets', side_effect=urllib.error.URLError('fixture inventory outage')), \
                 patch.object(publisher, 'ensure_release'), \
                 patch.object(publisher, 'publish_verified', return_value=asset), \
                 patch.object(publisher, 'archive_candidates', return_value={'errors': [], 'pending': 0}):
                clock.now.return_value.date.return_value.isoformat.return_value = 'fixture'
                self.assertTrue(publisher.preserve(state, catalog, root/'state.json', root, root, 'owner/repo'))
            self.assertEqual(len(json.loads(catalog.read_text())['source_manifests']), 2)
            self.assertEqual(len(state['preservation_errors']), 1)

    def test_existing_documents_share_one_release_inventory_without_cached_bodies(self):
        from sonos_firmware import releases
        from sonos_firmware.firmware_archive import register_document
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        import discover_firmware as publisher
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = root/'data/catalog.json'
            manifests, assets = [], {}
            for index in range(3):
                body = f'<update_manifest version="{index}"/>'.encode()
                manifest = {'url': BASE+str(index)+'.upm', 'snapshot': 'missing.upm',
                            'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(), 'metadata': {}}
                name = publisher.document_name(manifest)
                asset = {'size': len(body), 'digest': 'sha256:'+manifest['sha256'],
                         'browser_download_url': 'https://github.com/owner/repo/releases/download/snapshots/'+name}
                assets[name] = asset
                proof = releases.publication_receipt('owner/repo', 'snapshots', name, manifest, asset)
                register_document(catalog, manifest, proof)
                manifests.append(manifest)
            state = {'manifests': manifests, 'probes': {}}
            with patch.object(releases, 'remote_assets', return_value=assets) as inventory, \
                 patch.object(publisher, 'publish_verified', side_effect=AssertionError('already published')), \
                 patch.object(publisher, 'archive_candidates', return_value={'errors': [], 'pending': 0}):
                self.assertFalse(publisher.preserve(state, catalog, root/'state.json', root/'no-cache', root, 'owner/repo'))
                inventory.assert_called_once_with('owner/repo', 'snapshots')
                # A later run must fetch fresh metadata and still reject corruption.
                inventory.reset_mock()
                assets[name]['digest'] = 'sha256:'+'0'*64
                self.assertTrue(publisher.preserve(state, catalog, root/'state.json', root/'no-cache', root, 'owner/repo'))
                inventory.assert_called_once_with('owner/repo', 'snapshots')
                self.assertEqual(len(state['preservation_errors']), 1)
                self.assertIn('GitHub asset mismatch', state['preservation_errors'][0]['error'])
                inventory.reset_mock()
                inventory.side_effect = urllib.error.URLError('fixture inventory outage')
                self.assertTrue(publisher.preserve(state, catalog, root/'state.json', root/'no-cache', root, 'owner/repo'))
                inventory.assert_called_once_with('owner/repo', 'snapshots')
                self.assertEqual(len(state['preservation_errors']), 3)
            self.assertEqual(len(json.loads(catalog.read_text())['source_manifests']), 3)

    def test_capture_failure_does_not_block_other_manifests_or_package_versions(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
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
            def publish(*args):
                operations.append('document')
                return {'url': 'https://github.com/fixture/asset'}
            def acquire(*args, **kwargs):
                operations.append('firmware')
                return {
                     'preserved': 1, 'pending': 1, 'errors': [{'url': candidates[0]['url'], 'error': 'first version failed'}]
                }
            with patch.object(publisher, 'ensure_release'), patch.object(publisher, 'publish_verified', side_effect=publish), \
                 patch.object(publisher, 'archive_candidates', side_effect=acquire) as preserve:
                self.assertTrue(publisher.preserve(state, catalog, root / 'state.json', evidence, root, 'owner/repo'))
            preserve.assert_called_once()
            self.assertEqual(preserve.call_args.args[2], candidates)
            self.assertEqual(len(json.loads(catalog.read_text())['source_manifests']), 1)
            self.assertEqual(len(state['preservation_errors']), 2)
            self.assertEqual(operations, ['firmware', 'document'])


if __name__ == '__main__':
    unittest.main()
