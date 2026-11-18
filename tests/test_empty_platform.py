"""A source checkout can initialize bookkeeping without prior release assets."""
import importlib.util
from dataclasses import asdict
import json
import shutil
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from sonos_firmware.platform.registry import targets
from sonos_firmware.platform.runner import plan
from sonos_firmware.platform.git_state import validate_metadata
from sonos_firmware.platform.tasks import extract_firmware
from sonos_firmware import discovery, frontier, releases, release_series
from test_app_archive import FakeGitHub
from test_firmware_archive import Response
from test_upd import section

ROOT = Path(__file__).resolve().parents[1]


class EmptyPlatformTests(unittest.TestCase):
    def test_protocol_discovery_through_first_publication_needs_no_previous_releases(self):
        manifest_url = 'https://update.sonos.com/firmware/Prod/fixture/update.upm'
        payload_url = manifest_url.replace('update.upm', '1.0-1-1-17.upd')
        xml = b'<update_manifest version="1.1"><image model="17" version="1.0-1">1.0-1-1-17.upd</image></update_manifest>'
        body = section(99, 0, b'first discovered firmware capture')
        github = FakeGitHub()
        def fetch(url):
            if url == manifest_url:
                return xml
            raise urllib.error.HTTPError(url, 404, 'fixture absence', {}, None)
        def probe(candidate):
            return {**asdict(candidate), 'available': candidate.url == payload_url,
                    'http_status': 200 if candidate.url == payload_url else 404}
        spec = importlib.util.spec_from_file_location('empty_discover', ROOT/'scripts/discover_firmware.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frontier, 'protocol_roots', return_value=[manifest_url]), \
             patch.object(frontier.time, 'sleep'), \
             patch.object(releases, 'remote_assets', side_effect=github.assets), \
             patch.object(releases.subprocess, 'run', side_effect=github.command), \
             patch.object(release_series, 'matching_tags', side_effect=github.matching_tags), \
             patch.object(releases, 'public_release', side_effect=github.public_release), \
             patch.object(releases, 'upload_streaming', side_effect=github.upload_streaming), \
             patch.object(discovery, 'open_advertised', side_effect=lambda *args, **kwargs: Response(body)):
            root = Path(temporary)
            result = frontier.discover({}, root/'observations', fetch=fetch, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), profile_limit=0, inference_limit=1, max_manifests=8)
            self.assertEqual(github.releases, {})
            self.assertFalse((root/'data').exists())
            (root/'worker').mkdir()
            self.assertFalse(module.preserve(result, root/'data/catalog.json', root/'data/discovery/frontier.json',
                root/'observations', root/'worker', 'fresh/archive'))
            catalog = json.loads((root/'data/catalog.json').read_text())
            self.assertEqual(len(catalog['packages']), 1)
            self.assertEqual(catalog['packages'][0]['source_url'], payload_url)
            self.assertEqual(catalog['packages'][0]['artifact_status'], 'preserved')
            self.assertEqual(len(github.uploads), 2)
            # Committed receipts from another destination cannot substitute for
            # actually publishing the first manifest and payload in a fresh fork.
            self.assertFalse(module.preserve(result, root/'data/catalog.json', root/'data/discovery/frontier.json',
                root/'observations', root/'worker', 'another/archive'))
            self.assertEqual(len(github.uploads), 4)
            catalog = json.loads((root/'data/catalog.json').read_text())
            self.assertEqual(len(catalog['source_manifests']), 1)
            self.assertEqual(set(catalog['source_manifests'][0]['publications']), {'fresh/archive', 'another/archive'})
            # Even a receipt for this repository must not hide deleted releases.
            github.releases.clear()
            self.assertFalse(module.preserve(result, root/'data/catalog.json', root/'data/discovery/frontier.json',
                root/'observations', root/'worker', 'fresh/archive'))
            self.assertEqual(len(github.uploads), 5)

    def test_all_registered_targets_plan_and_validate_without_any_archive_state(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'config').mkdir()
            shutil.copyfile(ROOT/'config/targets.json', root/'config/targets.json')
            registry = targets(root)
            self.assertTrue(registry)
            for target in registry.values():
                with self.subTest(target=target.name):
                    self.assertEqual(plan(target, root, 'fresh/archive')['status'], 'planned')
                    validate_metadata(root, target)
            self.assertFalse((root/'data').exists())

    def test_empty_extraction_worker_needs_no_catalog_keys_or_release_downloads(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'scripts').mkdir()
            for name in ('refresh_key_recovery_ledger.py', 'prepare_model_keys.py', 'decrypt_pending_firmware.py'):
                shutil.copyfile(ROOT/'scripts'/name, root/'scripts'/name)
            self.assertFalse((root/'data').exists())
            # Actual child Python tasks inherit the network guard. An accidental
            # release download or recovery subprocess fails the entire test run.
            extract_firmware(root, root/'worker', 'fresh/archive')
            result = json.loads((root/'worker/extraction.json').read_text())
            self.assertEqual(result['errors'], [])
            self.assertEqual(result['published'], [])
            self.assertEqual(result['inventory']['pending'], 0)
            self.assertFalse((root/'worker/keys').exists())

    def test_release_audit_has_no_expected_assets_in_an_empty_archive(self):
        spec = importlib.util.spec_from_file_location('empty_audit', ROOT/'scripts/audit_release_assets.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            self.assertEqual(module.recorded_assets(Path(temporary)), {})
