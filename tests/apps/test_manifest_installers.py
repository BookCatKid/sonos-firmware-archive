"""Native protocol observations feed installer discovery without archive prerequisites."""
import json
import importlib.util
import sys
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import installers
from sonos_firmware.storage import atomic_json


class ManifestInstallerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)


    def test_protocol_installer_filenames_keep_their_actual_version(self):
        url = 'https://update.sonos.com/firmware/observed/123.45-67890-1-3.exe'
        atomic_json(self.root/'data/discovery/firmware-frontier.json', {'candidates': [
            {'url': url, 'version': '999.0-1', 'package_model': 3, 'source': 'image'},
            {'url': url.rsplit('.', 1)[0]+'.upd', 'version': '123.45-67890'}]})
        result = installers.manifest_candidates(self.root, 'desktop')
        self.assertEqual(result['errors'], [])
        candidate, = result['candidates']
        self.assertEqual(candidate['url'], url)
        self.assertEqual(candidate['version'], '123.45-67890')
        self.assertEqual(candidate['platform'], 'windows')
        self.assertEqual(candidate['family'], 'unknown')
        self.assertTrue(any('firmware-frontier' in source for source in candidate['sources']))

    def test_catalog_aliases_feed_both_desktop_and_mobile_without_new_hashes(self):
        desktop = 'https://update.sonos.com/software/observed/Sonos_123.4-567.dmg'
        mobile = 'https://update.sonos.com/software/observed/Sonos_123.04.56.apk'
        atomic_json(self.root/'data/catalog.json', {'packages': [
            {'source_url': desktop, 'source_urls': [desktop], 'version': '123.4-567'},
            {'source_url': mobile, 'version': '123.04.56'}]})
        self.assertEqual([x['url'] for x in installers.manifest_candidates(self.root, 'desktop')['candidates']], [desktop])
        self.assertEqual([x['url'] for x in installers.manifest_candidates(self.root, 'mobile')['candidates']], [mobile])


    def test_cdn_provenance_is_required_and_retained(self):
        origin = 'https://www.sonos.com/downloads'
        url = 'https://downloads.example.net/Sonos_123.4-567.exe'
        atomic_json(self.root/'data/catalog.json', {'packages': [
            {'source_url': url, 'advertised_sources': {url: origin}},
            {'source_url': 'https://unattested.example.net/Sonos_123.4-567.exe'}]})
        result = installers.manifest_candidates(self.root, 'desktop')
        self.assertEqual([x['url'] for x in result['candidates']], [url])
        self.assertEqual(result['candidates'][0]['advertised_by'], origin)
        self.assertEqual(len(result['errors']), 1)


    def test_both_discovery_commands_consume_native_manifest_observations(self):
        for kind in ('desktop', 'mobile'):
            with self.subTest(kind=kind), ExitStack() as stack:
                root = self.root/kind
                url = ('https://new-downloads.sonos.com/firmware/observed/123.4-567-1-3.exe' if kind == 'desktop'
                       else 'https://new-downloads.sonos.com/software/Sonos_123.4-567.apk')
                atomic_json(root/'data/discovery/firmware-frontier.json', {'candidates': [{'url': url}]})
                script = Path(__file__).resolve().parents[2]/f'scripts/apps/discover_{kind}_installers.py'
                spec = importlib.util.spec_from_file_location('native_'+kind, script)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                output = root/f'data/apps/{kind}-discovery.json'
                stack.enter_context(patch.object(sys, 'argv', ['discover', '--output', str(output), '--archive-root', str(root)]))
                stack.enter_context(patch.object(module, 'probe', side_effect=lambda row: {**row, 'http_status': 200}))
                if kind == 'desktop':
                    for name in ('winget_candidates', 'homebrew_candidates', 'check_direct_redirects'):
                        stack.enter_context(patch.object(module, name, return_value=[]))
                    stack.enter_context(patch.object(module, 'winget_history', return_value={'status': 'ok', 'pending_total': 0, 'errors': []}))
                else:
                    stack.enter_context(patch.object(module, 'resolve_latest_redirect', return_value={}))
                self.assertEqual(module.main(), 0)
                found = {row['url'] for row in json.loads(output.read_text())['candidates']}
                self.assertIn(url, found)
                if kind == 'desktop':
                    self.assertEqual(found, {url, url.replace('-1-3.exe', '-1-4.dmg')})
                    mac = url.replace('-1-3.exe', '-1-4.dmg')+'?download=1'
                    self.assertEqual(module.counterpart({'platform': 'macos', 'url': mac}), ('windows', url+'?download=1'))
                    self.assertIsNone(module.counterpart({'platform': 'windows', 'url': url.replace('-1-3.exe', '-1-9.exe')}))


if __name__ == '__main__':
    unittest.main()
