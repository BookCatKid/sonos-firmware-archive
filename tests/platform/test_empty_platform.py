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
from sonos_firmware.platform.runner import plan, run
from sonos_firmware.platform.model import Step
from sonos_firmware.platform.git_state import validate_metadata
from tests.discovery.test_frontier import scan_frontier
from sonos_firmware import discovery, frontier, releases, release_series
from tests.apps.test_app_archive import FakeGitHub
from tests.firmware.test_firmware_archive import Response
from tests.firmware.test_upd import section

ROOT = Path(__file__).resolve().parents[2]


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
        spec = importlib.util.spec_from_file_location('empty_discover', ROOT/'scripts/firmware/discover_firmware.py')
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
            result = scan_frontier({}, root/'observations', fetch=fetch, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), work_units=1, )
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


class ColdStartTargetCoverageTests(unittest.TestCase):
    """Every configured target must accept empty, existing and migrated state.

    Item 19 asks for per-target evidence, not a sample. These tests iterate the
    real `config/targets.json` so a newly added target is covered automatically.
    """

    def registry(self, root):
        (root / 'config').mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / 'config/targets.json', root / 'config/targets.json')
        return targets(root)


    def test_a_corrupt_metadata_document_is_reported_for_the_owning_target_only(self):
        """One target's damaged state must not silently validate or block others."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            registry = self.registry(root)
            broken = registry['apple-listings']
            victim = root / broken.metadata[0]
            victim.parent.mkdir(parents=True, exist_ok=True)
            victim.write_text('{"schema_version": 1,')
            with self.assertRaises(json.JSONDecodeError):
                validate_metadata(root, broken)
            for name, target in registry.items():
                if name == broken.name:
                    continue
                with self.subTest(target=name):
                    validate_metadata(root, target)


    def test_optional_capabilities_never_block_a_target_that_can_still_work(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary, \
             patch('sonos_firmware.platform.runner.shutil.which', return_value=None), \
             patch('sonos_firmware.platform.runner.importlib.util.find_spec', return_value=None):
            root = Path(temporary)
            registry = self.registry(root)
            optional = [t for t in registry.values() if t.optional_capabilities]
            self.assertTrue(optional, 'expected a target with optional capabilities')
            for target in optional:
                if target.capabilities:
                    # A target with required capabilities is blocked separately.
                    continue
                with self.subTest(target=target.name):
                    report = run(target, root, 'fresh/archive',
                                 environment={'GITHUB_ACTIONS': 'true'},
                                 provider_map={target.provider: lambda c: [Step('metadata', ('fixture',))]},
                                 execute=lambda *args: (0, 'discovered'))
                    self.assertTrue(report['executed'])
                    self.assertEqual(report['status'], 'ok')
                    self.assertEqual(report['pending'], [])
