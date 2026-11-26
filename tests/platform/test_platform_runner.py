import json
import tempfile
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from sonos_firmware.platform.model import Step, Target
from sonos_firmware.platform.runner import run


class RunnerTests(unittest.TestCase):
    def workflow_repo(self, root):
        path = root/'.github/workflows/archive.yml'
        path.parent.mkdir(parents=True)
        path.write_text('name: old\n')
        subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
        self.commit_fixture(root)
        return path, subprocess.check_output(['git', 'rev-parse', 'HEAD'], cwd=root, text=True).strip()

    def commit_fixture(self, root):
        subprocess.run(['git', 'add', '.'], cwd=root, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
                        'commit', '-qm', 'fixture'], cwd=root, check=True, capture_output=True)

    def workflow_environment(self, revision, event='push'):
        return {'GITHUB_ACTIONS': 'true', 'GITHUB_EVENT_NAME': event, 'GITHUB_WORKFLOW_SHA': revision,
                'GITHUB_WORKFLOW_REF': 'owner/repo/.github/workflows/archive.yml@refs/heads/main'}

    def test_superseded_automatic_workflow_skips_without_overwriting_target_progress(self):
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path, old = self.workflow_repo(root)
            path.write_text('name: corrected\n')
            self.commit_fixture(root)
            state = root/'data/platform/targets/new-source.json'
            state.parent.mkdir(parents=True)
            state.write_text('{"status":"partial","pending":["retained"]}')
            before = state.read_bytes()
            provider = Mock(side_effect=AssertionError('obsolete workflow must not acquire'))
            report = run(self.target(), root, 'owner/repo', environment=self.workflow_environment(old),
                         provider_map={'fixture': provider})
            self.assertEqual(report['status'], 'skipped')
            self.assertFalse(report['executed'])
            self.assertEqual(report['reason'], 'workflow definition superseded')
            self.assertEqual(state.read_bytes(), before)
            provider.assert_not_called()


    def target(self, **extra):
        return Target.from_dict({'name': 'new-source', 'provider': 'fixture', 'description': 'Fixture source',
                                 'interval_hours': 6, 'metadata': ['data/new-source'], **extra})


    def test_local_acquisition_is_rejected_before_any_files_or_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'runs in CI'):
                run(self.target(), root, 'owner/repo', environment={}, provider_map={})
            self.assertEqual(list(root.iterdir()), [])


    def test_missing_play_dependency_blocks_only_that_target_without_running_it(self):
        from unittest.mock import patch
        def forbidden(c): raise AssertionError('must not execute')
        with tempfile.TemporaryDirectory() as temporary, patch(
                'sonos_firmware.platform.runner.importlib.util.find_spec', return_value=None):
            report = run(self.target(capabilities=['goopdl']), Path(temporary), 'owner/repo',
                         environment={'GITHUB_ACTIONS': 'true'}, provider_map={'fixture': forbidden})
            self.assertEqual(report['status'], 'blocked')
            self.assertFalse(report['executed'])
            self.assertEqual(report['pending'], ['goopdl'])


    def test_scheduled_backlog_resumes_hourly_then_returns_to_its_success_cadence(self):
        from datetime import datetime, timedelta
        from sonos_firmware.platform.runner import is_due
        def provider(c):
            return [Step('audit', ('fixture',), report=str(c.work/'native.json'))]
        def execute(step, context, environment):
            Path(step.report).write_text(json.dumps({'status': status}))
            return 0, 'normal scheduled worker'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = self.target(interval_hours=24)
            self.assertTrue(is_due(root, target))
            status = 'partial'
            first = run(target, root, 'owner/repo', environment={'GITHUB_ACTIONS': 'true'},
                        provider_map={'fixture': provider}, execute=execute)
            finished = datetime.fromisoformat(first['finished_at'])
            self.assertFalse(is_due(root, target, now=finished+timedelta(minutes=59)))
            self.assertTrue(is_due(root, target, now=finished+timedelta(hours=1)))
            status = 'ok'
            second = run(target, root, 'owner/repo', environment={'GITHUB_ACTIONS': 'true'},
                         provider_map={'fixture': provider}, execute=execute)
            finished = datetime.fromisoformat(second['finished_at'])
            self.assertFalse(is_due(root, target, now=finished+timedelta(hours=23)))
            self.assertTrue(is_due(root, target, now=finished+timedelta(hours=24)))


    def test_credentials_are_not_written_into_run_reports(self):
        token = 'secret-value-must-never-be-in-the-report'
        with tempfile.TemporaryDirectory() as temporary:
            report = run(self.target(), Path(temporary), 'owner/repo',
                         environment={'GITHUB_ACTIONS': 'true', 'GH_TOKEN': token},
                         provider_map={'fixture': lambda c: [Step('test', ('fixture',))]},
                         execute=lambda *args: (1, 'error token=' + token))
            self.assertNotIn(token, json.dumps(report))
            self.assertIn('[REDACTED]', json.dumps(report))


    def test_target_metadata_cannot_stage_code_or_escape_the_checkout(self):
        for path in ('src', '../secrets', '/etc', 'data/../keys'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.target(metadata=[path])

class TargetRegistrationTests(unittest.TestCase):


    def test_incomplete_coverage_does_not_fail_the_command(self):
        import contextlib
        import io
        from sonos_firmware.platform import cli
        partial = {'schema_version': 1, 'target': 'configured-source', 'status': 'partial',
                   'executed': True, 'steps': [], 'errors': [], 'pending': []}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'config').mkdir()
            (root/'config/targets.json').write_text(json.dumps({'schema_version': 1, 'targets': [
                {'name': 'configured-source', 'provider': 'fixture', 'description': 'Configured source',
                 'interval_hours': 6, 'metadata': ['data/source'], 'options': {}}]}))
            with patch.object(cli, 'run', return_value=partial), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--root', str(root), 'run', 'configured-source', '--execute',
                                          '--repository', 'owner/repo']), 0)
            with patch.object(cli, 'run', return_value={**partial, 'status': 'failed'}), \
                    contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--root', str(root), 'run', 'configured-source', '--execute',
                                          '--repository', 'owner/repo']), 1)
            source_only = {**partial, 'errors': [{'step': 'scan', 'kind': 'source-reported-incomplete'}]}
            with patch.object(cli, 'run', return_value=source_only), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--root', str(root), 'run', 'configured-source', '--execute',
                                          '--repository', 'owner/repo']), 0)
            faulted = {**partial, 'errors': [{'step': 'scan', 'kind': 'exception', 'error': 'boom'}]}
            with patch.object(cli, 'run', return_value=faulted), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--root', str(root), 'run', 'configured-source', '--execute',
                                          '--repository', 'owner/repo']), 1)


class ExtractionCheckpointTests(unittest.TestCase):
    def test_interrupted_upload_cannot_expose_staged_catalog_records(self):
        from unittest.mock import patch
        from sonos_firmware.platform.tasks import extract_firmware
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'data').mkdir()
            catalog = root / 'data/catalog.json'
            source = b'{"packages": [{"id": "verified-source"}], "raw_images": []}\n'
            catalog.write_bytes(source)
            work = root / 'worker'
            def worker(command, **kwargs):
                script = Path(command[1]).name
                if script == 'decrypt_pending_firmware.py':
                    (work / 'receipts').mkdir()
                    (work / 'receipts/source.json').write_text('{"components": []}')
                elif script == 'import_raw_receipts.py':
                    staging = Path(command[command.index('--catalog') + 1])
                    staging.write_text('{"packages": [], "raw_images": ["unverified"]}')
                elif script == 'upload_raw_assets.py':
                    self.assertEqual(catalog.read_bytes(), source)
                    raise KeyboardInterrupt('simulated interrupted worker')
            with patch('sonos_firmware.platform.tasks.subprocess.run', side_effect=worker):
                with self.assertRaises(KeyboardInterrupt):
                    extract_firmware(root, work, 'owner/repo')
            self.assertEqual(catalog.read_bytes(), source)
            self.assertFalse((root / 'data/raw/source.json').exists())

    def test_successful_packages_publish_after_extraction_and_upload_failures(self):
        from unittest.mock import patch
        from sonos_firmware.platform.tasks import extract_firmware
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'data').mkdir()
            catalog = root / 'data/catalog.json'
            catalog.write_text(json.dumps({'packages': ['preserved-source'], 'raw_images': []}))
            work = root / 'worker'
            attempted = []
            def worker(command, **kwargs):
                script = Path(command[1]).name
                if script == 'prepare_model_keys.py':
                    keys = work / 'keys'
                    keys.mkdir()
                    (keys / 'model17.pem').write_text('private fixture')
                    raise subprocess.CalledProcessError(1, command)
                elif script == 'decrypt_pending_firmware.py':
                    receipts = work / 'receipts'
                    receipts.mkdir(parents=True)
                    for package in ('alpha', 'beta', 'gamma', 'other'):
                        component_package = 'alpha' if package == 'other' else package
                        (receipts / (package + '.json')).write_text(json.dumps({'components': [{'package_id': component_package}]}))
                    raise subprocess.CalledProcessError(1, command)
                elif script == 'import_raw_receipts.py':
                    package = Path(command[2]).stem
                    staging = Path(command[command.index('--catalog') + 1])
                    document = json.loads(staging.read_text())
                    document['raw_images'].append(package)
                    staging.write_text(json.dumps(document))
                elif script == 'upload_raw_assets.py':
                    package = command[command.index('--package-id') + 1]
                    self.assertNotIn(package, json.loads(catalog.read_text())['raw_images'])
                    attempted.append(package)
                    if package == 'beta':
                        raise subprocess.CalledProcessError(1, command)
            with patch('sonos_firmware.platform.tasks.subprocess.run', side_effect=worker):
                with self.assertRaisesRegex(RuntimeError, 'verified progress retained'):
                    extract_firmware(root, work, 'owner/repo')
            self.assertEqual(attempted, ['alpha', 'beta', 'gamma'])
            document = json.loads(catalog.read_text())
            self.assertEqual(document['packages'], ['preserved-source'])
            self.assertEqual(document['raw_images'], ['alpha', 'gamma'])
            self.assertEqual(sorted(path.stem for path in (root / 'data/raw').glob('*.json')), ['alpha', 'gamma'])
            report = json.loads((work / 'extraction.json').read_text())
            self.assertEqual(report['published'], ['alpha', 'gamma'])
            self.assertEqual(report['status'], 'partial')
            self.assertTrue(any(row.get('package_id') == 'other' for row in report['errors']))
            self.assertFalse((work / 'keys').exists())

    def test_raw_upload_failure_keeps_earlier_source_publication_and_no_raw_receipt(self):
        from unittest.mock import patch
        from sonos_firmware.platform.tasks import extract_firmware
        import subprocess
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'data').mkdir()
            catalog = root / 'data/catalog.json'
            published_sources = b'{"packages": [{"id": "already-published-source"}]}\n'
            catalog.write_bytes(published_sources)
            work = root / 'worker'
            def worker(command, **kwargs):
                script = Path(command[1]).name
                if script == 'decrypt_pending_firmware.py':
                    receipts = work / 'receipts'
                    receipts.mkdir(parents=True)
                    (receipts / 'source.json').write_text('{"components": []}')
                if script == 'import_raw_receipts.py':
                    staging = Path(command[command.index('--catalog') + 1])
                    staging.write_text('{"packages": [], "raw_images": ["not-yet-published"]}')
                if script == 'upload_raw_assets.py':
                    self.assertEqual(command[-2:], ['--repo', 'owner/custom-repository'])
                    raise subprocess.CalledProcessError(1, command)
            with patch('sonos_firmware.platform.tasks.subprocess.run', side_effect=worker):
                with self.assertRaisesRegex(RuntimeError, 'verified progress retained'):
                    extract_firmware(root, work, 'owner/custom-repository')
            self.assertEqual(catalog.read_bytes(), published_sources)
            self.assertFalse((root / 'data/raw/source.json').exists())
            self.assertFalse((work / 'keys').exists())


class HistoryBootstrapTests(unittest.TestCase):

    def test_previously_observed_selector_survives_an_unavailable_store_listing(self):
        import importlib.util
        from unittest.mock import patch
        from types import SimpleNamespace
        import sys
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location('retained_sweep', root/'scripts/apps/sweep_android_store_history.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ledger = directory/'state.json'
            ledger.write_text(json.dumps({'attempts': [{'package': 'com.sonos.acr2', 'version_selector': 'old',
                                                       'status': 'failed', 'checked': '2020-01-01'}]}))
            acquired = []
            def acquire(command, **kwargs):
                acquired.append(command[command.index('--version-selector')+1])
                return SimpleNamespace(returncode=0, stdout='verified publication')
            with patch.object(sys, 'argv', ['sweep', '--package', 'com.sonos.acr2',
                    '--receipt', str(directory/'archive.json'), '--ledger', str(ledger)]), \
                 patch.object(module, 'discover_versions', side_effect=OSError('listing unavailable')), \
                 patch.object(module, 'discover_apkpure', return_value={'variants': [], 'errors': []}), \
                 patch.object(module, 'discover_sdk_levels', return_value={'levels': [29], 'errors': []}), \
                 patch.object(module, 'run_acquisition', side_effect=acquire):
                self.assertEqual(module.main(), 1)
            self.assertEqual(acquired, ['old'])
            document = json.loads(ledger.read_text())
            self.assertEqual(document['attempts'][0]['status'], 'archived')
            self.assertEqual(document['pending_total'], 0)
            self.assertEqual(document['status'], 'partial')

    def test_empty_archive_rotates_past_failed_selector_and_retains_retry(self):
        import importlib.util
        from unittest.mock import patch
        from types import SimpleNamespace
        import sys
        root = Path(__file__).resolve().parents[2]
        spec = importlib.util.spec_from_file_location('sweep_fixture', root / 'scripts/apps/sweep_android_store_history.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / 'archive.json'
            ledger = Path(temporary) / 'state.json'
            argv = ['sweep', '--package', 'com.sonos.acr2', '--receipt', str(receipt),
                    '--ledger', str(ledger), '--repo', 'owner/repo']
            acquired = []
            def acquire(command, **kwargs):
                acquired.append(command[command.index('--version-selector') + 1])
                self.assertIn('owner/repo', command)
                return SimpleNamespace(returncode=1, stdout='fixture failure')
            with patch.object(sys, 'argv', argv), patch.object(module, 'discover_versions', return_value=['old', 'new']), \
                 patch.object(module, 'discover_apkpure', return_value={'variants': [], 'errors': []}), \
                 patch.object(module, 'discover_sdk_levels', return_value={'levels': [29], 'errors': []}), \
                 patch.object(module, 'run_acquisition', side_effect=acquire):
                self.assertEqual(module.main(), 1)
                self.assertEqual(module.main(), 1)
                from datetime import datetime, timedelta, timezone
                from sonos_firmware import retry
                with patch.object(retry, '_now', return_value=datetime.now(timezone.utc)+timedelta(hours=2)):
                    self.assertEqual(module.main(), 1)
            self.assertEqual(acquired, ['old', 'new', 'old', 'new'])
            document = json.loads(ledger.read_text())
            self.assertEqual(document['pending_total'], 2)
            self.assertEqual(document['status'], 'partial')


class ReceiptRepositoryTests(unittest.TestCase):
    def test_another_repository_cannot_suppress_selected_repository_publication(self):
        from sonos_firmware.releases import receipt_matches_repository
        record = {'components': [{'release_url': 'https://github.com/owner/original/releases/download/tag/base.apk'}]}
        self.assertTrue(receipt_matches_repository(record, 'owner/original'))
        self.assertFalse(receipt_matches_repository(record, 'owner/fork'))
        record['complete_wrapper'] = {'release_url': 'https://github.com/owner/fork/releases/download/tag/wrapper.xapk'}
        self.assertFalse(receipt_matches_repository(record, 'owner/original'))
        self.assertFalse(receipt_matches_repository({}, 'owner/original'))


class IndependentSourceFailureTests(unittest.TestCase):
    def load(self, name):
        import importlib.util
        scripts = Path(__file__).resolve().parents[2] / 'scripts'
        spec = importlib.util.spec_from_file_location('fixture_' + name, next(scripts.rglob(name + '.py')))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module


    def test_mobile_current_discovery_does_not_query_the_separate_history_index(self):
        import sys
        from unittest.mock import patch
        module = self.load('discover_mobile_installers')
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'discovery.json'
            with patch.object(sys, 'argv', ['discover', '--output', str(output), '--archive-root', temporary]), \
                 patch.object(module.urllib.request, 'urlopen', side_effect=AssertionError('unexpected history query')) as fetch, \
                 patch.object(module, 'resolve_latest_redirect', return_value={
                     'location': 'https://update-software.sonos.com/software/android/Sonos_90.0-12345.apk',
                     'location_source': 'live'}), \
                 patch.object(module, 'probe', side_effect=lambda item: {**item, 'http_status': 200}):
                self.assertEqual(module.main(), 0)
            report = json.loads(output.read_text())
            self.assertEqual(report['status'], 'ok')
            self.assertEqual(report['available_live_total'], 1)
            fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
