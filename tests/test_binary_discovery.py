import hashlib
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock, patch

from sonos_firmware.binary_discovery import discover_preserved, preserved_sources
from sonos_firmware.storage import atomic_json


class BinaryDiscoveryTests(unittest.TestCase):
    def test_archive_alias_is_used_even_when_only_the_binary_named_source_is_available(self):
        import tarfile
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:xz') as archive:
            body = b'/documents/gpl/22.17/gpl.html'
            info = tarfile.TarInfo('bin/upgrade')
            info.size = len(body)
            archive.addfile(info, io.BytesIO(body))
        payload = stream.getvalue()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.publish_receipts(root, [self.source(payload, 'payload.bin'), self.source(payload, 'inventory.tar.xz')])
            def acquire(source, path, repository):
                if source['release_asset'].endswith('.xz'):
                    raise FileNotFoundError('archive-named alias unavailable')
                path.write_bytes(payload)
            result = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire)
            captured = next(iter(result['scans'].values()))
            self.assertEqual(captured['format'], 'tar')
            self.assertEqual(captured['verified_source']['release_asset'], 'payload.bin')
            self.assertEqual(captured['filename'], 'inventory.tar.xz')
            self.assertIn('22.17', result['version_evidence'])

    def test_new_target_receipts_are_found_from_declared_metadata_paths(self):
        source = self.source(b'public fixture')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'config/targets.json', {'schema_version': 1, 'targets': [{
                'name': 'new-source', 'provider': 'fixture', 'description': 'Fixture adapter',
                'interval_hours': 6, 'metadata': ['data/custom/receipts.json']}]})
            atomic_json(root/'data/custom/receipts.json', {'artifacts': [source]})
            sources = preserved_sources(root, 'owner/repo')
        self.assertEqual(len(sources), 1)
        self.assertEqual(sources[0]['sources'][0]['metadata'], 'data/custom/receipts.json')
    def apk(self, version='22.17'):
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('classes.dex', f'https://www.sonos.com/documents/gpl/{version}/gpl.html')
        return stream.getvalue()

    def source(self, body, name='controller.apk', tag='apps-fixture'):
        return {'release_asset': name, 'release_tag': tag, 'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body)}

    def publish_receipts(self, root, rows):
        atomic_json(root/'data/apps/mobile-archive.json', {'artifacts': rows})

    def test_empty_archive_needs_no_local_inputs_and_the_provider_schedules_feedback_first(self):
        from sonos_firmware.platform.registry import targets
        from sonos_firmware.platform.runner import plan
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            acquire = Mock()
            result = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire)
            acquire.assert_not_called()
            self.assertEqual(result['status'], 'ok')
        root = Path(__file__).resolve().parents[1]
        steps = plan(targets(root)['gpl'], root, 'owner/repo')['steps']
        self.assertIn('scan-preserved-binaries', steps[0]['command'])
        self.assertTrue(all(step['after_failure'] for step in steps[1:]))

    def test_verified_compressed_app_evidence_is_discovered_automatically_and_not_reacquired(self):
        body = self.apk()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = self.source(body)
            self.publish_receipts(root, [source])
            result = discover_preserved(root, root/'worker', 'owner/repo',
                acquire=lambda src, path, repo: path.write_bytes(body))
            self.assertEqual(result['status'], 'ok')
            self.assertIn('22.17', result['version_evidence'])
            self.assertEqual(result['scans'][source['sha256']]['verified_source']['sha256'], source['sha256'])
            self.assertNotIn(temporary, (root/'data/gpl/live/binary-evidence.json').read_text())
            self.assertEqual(list((root/'worker').iterdir()), [])
            acquire = Mock()
            again = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire)
            acquire.assert_not_called()
            self.assertEqual(again['attempted_this_run'], 0)

            import importlib.util
            from argparse import Namespace
            from sonos_firmware.gpl_archive import HttpResult
            script = Path(__file__).resolve().parents[1]/'scripts/archive_gpl_direct.py'
            spec = importlib.util.spec_from_file_location('automatic_binary_feedback', script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            args = Namespace(output=root/'data/gpl/live', binary=[], recheck=False,
                             discovery_window=1, limit=1, delay=0)
            response = HttpResult(403, b'<Error><Code>AccessDenied</Code></Error>', 'application/xml', '')
            with patch.object(module, 'offline_candidates', return_value={}), \
                 patch.object(module, 'get_page', return_value=response) as request, patch.object(module, 'pause'):
                self.assertEqual(module.discover(args), 0)
            request.assert_called_once_with('https://www.sonos.com/documents/gpl/22.17/gpl.html')
            self.assertEqual(module.live_catalog(args.output)['releases'], {})

    def test_one_bad_capture_does_not_stop_another_and_negative_results_stay_compact(self):
        good = b'ELF fixture with no GPL URL'
        bad = self.apk()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, second = self.source(bad), self.source(good, 'updater.bin')
            self.publish_receipts(root, [first, second])
            def acquire(src, path, repo):
                path.write_bytes(b'wrong' if src['sha256'] == first['sha256'] else good)
            result = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire, limit=2)
            self.assertEqual(result['attempted_this_run'], 2)
            self.assertEqual(result['pending_total'], 1)
            self.assertEqual(result['scans'], {})
            checkpoint = result['repositories']['owner/repo'][second['sha256']]
            self.assertEqual(set(checkpoint), {'status', 'revision', 'bytes_scanned'})
            self.assertEqual(checkpoint['status'], 'scanned')
            self.assertEqual(checkpoint['bytes_scanned'], len(good))
            import contextlib
            import importlib.util
            from argparse import Namespace
            script = Path(__file__).resolve().parents[1]/'scripts/archive_gpl_direct.py'
            spec = importlib.util.spec_from_file_location('compact_binary_reporting', script)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            with contextlib.redirect_stdout(io.StringIO()):
                module.report(Namespace(output=root/'data/gpl/live'))
            summary = json.loads((root/'data/gpl/live/report.json').read_text())
            self.assertEqual(summary['binary_scanned_inputs'], 1)
            self.assertEqual(summary['binary_executable_text_bytes_scanned'], len(good))
            self.assertEqual(summary['binary_pending_inputs'], 1)
            acquire = Mock()
            retry = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire)
            acquire.assert_not_called()
            self.assertEqual(len(retry['errors']), 1)

    def test_source_aliases_deduplicate_content_and_an_available_alias_can_recover_it(self):
        body = self.apk()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.publish_receipts(root, [self.source(body, tag='old'), self.source(body, tag='new')])
            def acquire(src, path, repo):
                if src['release_tag'] == 'old':
                    raise FileNotFoundError('old capture unavailable')
                path.write_bytes(body)
            self.assertEqual(len(preserved_sources(root, 'owner/repo')), 1)
            result = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire, limit=1)
            self.assertEqual(result['attempted_this_run'], 1)
            self.assertEqual(next(iter(result['scans'].values()))['verified_source']['release_tag'], 'new')

    def test_many_failed_source_aliases_cannot_permanently_hide_a_later_capture(self):
        body = self.apk()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = [self.source(body, tag=str(n)) for n in range(5)]
            self.publish_receipts(root, rows)
            def acquire(src, path, repo):
                if src['release_tag'] != '4':
                    raise FileNotFoundError('fixture unavailable')
                path.write_bytes(body)
            first = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire, limit=1)
            observation = first['repositories']['owner/repo'][rows[0]['sha256']]
            self.assertEqual(observation['status'], 'failed')
            observation['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
            atomic_json(root/'data/gpl/live/binary-evidence.json', first)
            result = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire, limit=1)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(next(iter(result['scans'].values()))['verified_source']['release_tag'], '4')

    def test_partial_new_analysis_keeps_earlier_evidence_and_changed_limits_requeue(self):
        body = self.apk()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.publish_receipts(root, [self.source(body)])
            acquire = lambda src, path, repo: path.write_bytes(body)
            discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire)
            def partial(path, **kwargs):
                return {'filename': path.name, 'sha256': hashlib.sha256(body).hexdigest(), 'complete': False,
                        'version_evidence': {}, 'limitations': ['fixture limit']}
            result = discover_preserved(root, root/'worker', 'owner/repo', acquire=acquire,
                scan=partial, max_bytes=1000)
            self.assertIn('22.17', result['version_evidence'])
            self.assertEqual(result['pending_total'], 1)

    def test_catalog_components_web_resources_and_app_splits_are_all_sources(self):
        source = self.source(b'public fixture')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'data/catalog.json', {'packages': [{**source, 'artifact_status': 'preserved'}],
                                                  'raw_images': [source]})
            self.publish_receipts(root, [{'release_tag': source['release_tag'], 'components': [source],
                                         'complete_wrapper': source}])
            atomic_json(root/'data/apps/web-app-archive.json', {'deployments': [{'resources': [source]}]})
            sources = preserved_sources(root, 'owner/repo')
        self.assertEqual(len(sources), 1)
        self.assertEqual(len(sources[0]['sources']), 3)
