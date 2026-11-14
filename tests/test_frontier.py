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
from unittest.mock import patch

from sonos_firmware import frontier
from sonos_firmware.crawl import crawl_manifests

HOST = 'https://update.sonos.com'
BASE = HOST + '/firmware/Prod/discovered/'


class FrontierTests(unittest.TestCase):
    def test_metadata_base_discovers_packages_even_when_its_manifest_is_gone(self):
        uri = (BASE + '^37.5-12345').encode() + b'\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri) + 16, 0) + uri
        def fetch(url):
            raise urllib.error.HTTPError(url, 404, 'gone', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            result = frontier.discover({}, Path(temporary), fetch=fetch, metadata_fetch=lambda: (ups, {}),
                model_max=2, probe_fn=lambda c: {**asdict(c), 'available': c.package_model == 2, 'http_status': 200})
        rows = result['candidates']
        self.assertEqual({row['package_model'] for row in rows}, {0, 1, 2})
        self.assertEqual({row['version'] for row in rows}, {'37.5-12345'})
        self.assertTrue(all(row['url'].startswith(BASE+'37.5-12345-1-') for row in rows))
        self.assertTrue(all(row['metadata_sha256'] == hashlib.sha256(ups).hexdigest() for row in rows))
        self.assertEqual(len(frontier.monitor_report(result, {'packages': []})['available_uncatalogued_candidates']), 1)

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
        self.assertEqual({c['package_model'] for c in result['candidates']}, {1, 8, 17})
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

    def test_outside_content_location_is_never_followed(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = crawl_manifests({}, [{'url': BASE + 'update.upm'}], [], Path(temporary),
                fetch=lambda url: (b'<update_manifest version="1.1"></update_manifest>', url, 'https://outside.test/update.upm'), delay=0)
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
        state = {'candidates': rows, 'probe_sequence': 3,
                 'probes': {r['url']: {**r, 'attempt_sequence': i} for i, r in enumerate(rows, 1)}}
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
    def test_capture_failure_does_not_block_other_manifests_or_package_versions(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
        import discover_firmware as publisher
        import archive_monitor_firmware as archive
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
            with patch.object(archive, 'ensure_release'), patch.object(archive, 'upload_small_verified'), \
                 patch.object(publisher, 'archive_candidates', return_value={
                     'preserved': 1, 'pending': 1, 'errors': [{'url': candidates[0]['url'], 'error': 'first version failed'}]
                 }) as preserve:
                self.assertTrue(publisher.preserve(state, catalog, root / 'state.json', evidence, root, 'owner/repo'))
            preserve.assert_called_once()
            self.assertEqual(preserve.call_args.args[2], candidates)
            self.assertEqual(len(json.loads(catalog.read_text())['source_manifests']), 1)
            self.assertEqual(len(state['preservation_errors']), 2)


if __name__ == '__main__':
    unittest.main()
