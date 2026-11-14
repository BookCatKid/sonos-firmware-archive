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
