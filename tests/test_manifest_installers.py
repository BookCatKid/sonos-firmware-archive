"""Native protocol observations feed installer discovery without archive prerequisites."""
import json
import hashlib
import io
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

    def test_empty_checkout_needs_no_catalog_or_firmware_run(self):
        result = installers.manifest_candidates(self.root, 'desktop')
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['errors'], [])

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

    def test_bad_catalog_does_not_discard_valid_frontier_leads(self):
        path = self.root/'data/catalog.json'
        path.parent.mkdir(parents=True)
        path.write_text('{not json')
        url = 'https://new-downloads.sonos.com/software/Sonos_123.4-567.exe'
        atomic_json(self.root/'data/discovery/firmware-frontier.json', {'candidates': [{'url': url}]})
        result = installers.manifest_candidates(self.root, 'desktop')
        self.assertEqual(len(result['errors']), 1)
        self.assertEqual([x['url'] for x in result['candidates']], [url])

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

    def test_bad_alias_does_not_discard_other_urls_in_the_same_observation(self):
        first = 'https://update.sonos.com/software/Sonos_123.4-567.exe'
        second = 'https://update-firmware.sonos.com/software/Sonos_123.4-567.exe'
        atomic_json(self.root/'data/catalog.json', {'packages': [
            {'source_url': first, 'source_urls': [{'invalid': True}, 'https://127.0.0.1/Sonos_123.4-567.exe', second]}]})
        result = installers.manifest_candidates(self.root, 'desktop')
        self.assertEqual({x['url'] for x in result['candidates']}, {first, second})
        self.assertEqual(len(result['errors']), 2)

    def test_later_native_observations_fill_unknown_classification(self):
        numeric = 'https://update.sonos.com/firmware/observed/123.4-567-1-3.exe'
        opaque = 'https://update.sonos.com/software/observed/controller.exe'
        atomic_json(self.root/'data/catalog.json', {'packages': [{'source_url': numeric}, {'source_url': opaque}]})
        atomic_json(self.root/'data/discovery/firmware-frontier.json', {'candidates': [
            {'url': numeric, 'family': 's2'}, {'url': opaque, 'version': '123.4-567', 'family': 's2'}]})
        result = installers.manifest_candidates(self.root, 'desktop')
        self.assertEqual(result['errors'], [])
        found = {row['url']: row for row in result['candidates']}
        self.assertEqual(found[numeric]['family'], 's2')
        self.assertEqual(found[opaque]['version'], '123.4-567')
        self.assertEqual(found[opaque]['family'], 's2')

    def test_native_lead_reaches_verified_publication_in_an_empty_destination(self):
        from sonos_firmware import releases
        from sonos_firmware.app_archive import preserve
        from test_app_archive import FakeGitHub
        url = 'https://update.sonos.com/firmware/observed/123.4-567-1-3.exe'
        atomic_json(self.root/'data/discovery/firmware-frontier.json', {'candidates': [{'url': url}]})
        discovery = self.root/'data/apps/desktop-discovery.json'
        atomic_json(discovery, installers.manifest_candidates(self.root, 'desktop'))
        github = FakeGitHub()
        body = b'MZ synthetic installer; no payload or hash was supplied to discovery'
        def response(*args, **kwargs):
            incoming = io.BytesIO(body)
            incoming.headers = {'Content-Type': 'application/octet-stream', 'Content-Length': str(len(body))}
            return incoming
        with patch.object(installers, 'open_advertised', side_effect=response), \
             patch.object(releases, 'remote_assets', side_effect=github.assets), \
             patch.object(releases.subprocess, 'run', side_effect=github.command):
            result = preserve(installers.source(), discovery, self.root/'data/apps/desktop-archive.json',
                              'owner/repo', root=self.root)
        artifact, = result['artifacts']
        self.assertEqual(artifact['sha256'], hashlib.sha256(body).hexdigest())
        self.assertEqual(artifact['bytes'], len(body))
        self.assertIn(url, artifact['source_urls'])
        self.assertEqual(len(github.uploads), 1)
        self.assertEqual(github.uploads[0][3], body)

    def test_both_discovery_commands_consume_native_manifest_observations(self):
        for kind, suffix in (('desktop', 'exe'), ('mobile', 'apk')):
            with self.subTest(kind=kind), ExitStack() as stack:
                root = self.root/kind
                url = ('https://new-downloads.sonos.com/firmware/observed/123.4-567-1-3.exe' if kind == 'desktop'
                       else 'https://new-downloads.sonos.com/software/Sonos_123.4-567.apk')
                atomic_json(root/'data/discovery/firmware-frontier.json', {'candidates': [{'url': url}]})
                script = Path(__file__).resolve().parents[1]/f'scripts/discover_{kind}_installers.py'
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
