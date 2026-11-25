"""Saved and replayed manifests route pages through the same live traversal."""
import json
import tempfile
import unittest
import urllib.error
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import frontier, historical_firmware as history
from sonos_firmware.storage import atomic_json

PAGE = 'https://new-downloads.sonos.com/software/start.html'
PAYLOAD = 'https://new-downloads.sonos.com/releases/100.2-345-1-300.upd'
MANIFEST = 'https://update.sonos.com/firmware/history/update.upm'


def candidate(url, filename, model=21):
    return {'url': url, 'filename': filename, 'version': '1.0-1',
            'package_model': model, 'source': 'image'}


class HistoricalLandingPageTests(unittest.TestCase):
    def manifest(self):
        return {'url': MANIFEST, 'sha256': 'a'*64, 'bytes': 100,
                'archive_capture': {'timestamp': '20200101000000'},
                'metadata': {'manifest_links': [], 'landing_links': [PAGE], 'warnings': []},
                'candidates': [candidate(PAGE, 'start.html'), candidate(PAYLOAD, '100.2-345-1-300.upd', 300)]}

    def test_replayed_pages_become_pending_documents_and_keep_payload_leads(self):
        state = {'candidates': [candidate(PAGE, 'start.html')]}
        history.remember_manifest(state, self.manifest())
        self.assertEqual(state['pending_manifest_urls'], [PAGE])
        self.assertEqual([row['url'] for row in state['candidates']], [PAYLOAD])
        self.assertEqual(state['candidates'][0]['historical_manifests'][0]['manifest_url'], MANIFEST)

    def test_unknown_historical_links_remain_in_saved_metadata_and_errors(self):
        manifest = self.manifest()
        outside = 'https://outside.test/download.html'
        manifest['metadata']['landing_links'].append(outside)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root/'data/discovery/historical-manifest-acquisition.json'
            atomic_json(path, {'schema_version': 1, 'sources': {'fresh/archive': {'capture': {
                'status': 'preserved', 'url': MANIFEST, 'manifest': manifest,
                'checked_at': '2026-01-01T00:00:00+00:00', 'next_attempt_at': '9999-01-01T00:00:00+00:00'}}}})
            state = {}
            result = history.archive_manifests(root, root/'worker', 'fresh/archive', state, limit=1, paused=True)
            saved = json.loads(path.read_text())
        self.assertEqual(result['attempted'], 0)
        self.assertEqual(state['pending_manifest_urls'], [PAGE])
        self.assertEqual([row['url'] for row in result['errors']], [outside])
        metadata = saved['sources']['fresh/archive']['capture']['manifest']['metadata']
        self.assertEqual([row['url'] for row in metadata['unresolved_links']], [outside])

    def test_live_frontier_migrates_a_saved_page_out_of_payload_candidates(self):
        state = {'candidates': [candidate(PAGE, 'start.html')],
                 'probes': {PAGE: {'available': True, 'url': PAGE}}}
        requests = []
        def fetch(url):
            requests.append(url)
            if url == PAGE:
                return ('<a href="'+PAYLOAD+'">Firmware</a>').encode()
            raise urllib.error.HTTPError(url, 404, 'fixture absence', {}, None)
        def probe(row):
            return {**asdict(row), 'available': False, 'http_status': 404}
        with tempfile.TemporaryDirectory() as temporary, patch.object(frontier, 'protocol_roots', return_value=[]), \
             patch.object(frontier.time, 'sleep'):
            result = frontier.discover(state, Path(temporary), fetch=fetch, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), model_max=0, inference_limit=1)
        self.assertIn(PAGE, requests)
        self.assertNotIn(PAGE, {row['url'] for row in result['candidates']})
        self.assertNotIn(PAGE, result['probes'])
        self.assertIn(PAYLOAD, {row['url'] for row in result['candidates']})
