import json
import tempfile
import unittest
from pathlib import Path

from sonos_firmware.platform.model import Step, Target
from sonos_firmware.platform.registry import targets
from sonos_firmware.platform.runner import run, plan


class RunnerTests(unittest.TestCase):
    def target(self, **extra):
        return Target.from_dict({'name': 'new-source', 'provider': 'fixture', 'description': 'Fixture source',
                                 'interval_hours': 6, 'metadata': ['data/new-source'], **extra})

    def test_new_target_can_be_planned_without_an_archive_or_any_acquisition(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = plan(self.target(), root, 'owner/repo', provider_map={
                'fixture': lambda c: [Step('discover', ('discover-tool', str(c.work)))]})
            self.assertEqual(report['status'], 'planned')
            self.assertFalse(report['executed'])
            self.assertEqual(list(root.iterdir()), [])

    def test_local_acquisition_is_rejected_before_any_files_or_commands(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaisesRegex(ValueError, 'runs in CI'):
                run(self.target(), root, 'owner/repo', environment={}, provider_map={})
            self.assertEqual(list(root.iterdir()), [])

    def test_worker_cleans_artifacts_and_still_reports_completed_progress_after_failure(self):
        workspaces = []
        visited = []
        def provider(c):
            workspaces.append(c.work)
            return [Step('discover', ('fixture',)), Step('acquire', ('fixture',)),
                    Step('dependent', ('fixture',)), Step('checkpoint', ('fixture',), after_failure=True)]
        def execute(step, context, environment):
            visited.append(step.name)
            (context.work / 'downloaded.bin').write_bytes(b'fixture artifact')
            return (1, 'failure') if step.name == 'acquire' else (0, 'done')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = run(self.target(), root, 'owner/repo', environment={'GITHUB_ACTIONS': 'true'},
                         provider_map={'fixture': provider}, execute=execute)
            self.assertEqual(visited, ['discover', 'acquire', 'checkpoint'])
            self.assertEqual(report['status'], 'partial')
            self.assertFalse(workspaces[0].exists())
            self.assertTrue((root / 'data/platform/targets/new-source.json').exists())

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

    def test_native_incomplete_discovery_is_not_reported_as_complete(self):
        def provider(c):
            return [Step('discover', ('fixture',), report=str(c.work / 'native.json'))]
        def execute(step, context, environment):
            Path(step.report).write_text(json.dumps({'status': status}))
            return 0, 'completed bounded discovery'
        for status in ('partial', 'incomplete', 'error', 'blocked', 'failed', 'unavailable'):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                report = run(self.target(), Path(temporary), 'owner/repo', environment={'GITHUB_ACTIONS': 'true'},
                             provider_map={'fixture': provider}, execute=execute)
                self.assertEqual(report['status'], 'partial')

    def test_credentials_are_not_written_into_run_reports(self):
        token = 'secret-value-must-never-be-in-the-report'
        with tempfile.TemporaryDirectory() as temporary:
            report = run(self.target(), Path(temporary), 'owner/repo',
                         environment={'GITHUB_ACTIONS': 'true', 'GH_TOKEN': token},
                         provider_map={'fixture': lambda c: [Step('test', ('fixture',))]},
                         execute=lambda *args: (1, 'error token=' + token))
            self.assertNotIn(token, json.dumps(report))
            self.assertIn('[REDACTED]', json.dumps(report))

    def test_declared_targets_all_have_a_side_effect_free_plan(self):
        root = Path(__file__).resolve().parents[1]
        for target in targets(root).values():
            with self.subTest(target=target.name):
                self.assertTrue(plan(target, root, 'owner/repo')['steps'])

    def test_optional_tools_do_not_block_independent_provider_work(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary, patch(
                'sonos_firmware.platform.runner.shutil.which', return_value=None):
            target = self.target(optional_capabilities=['apkeep'])
            report = run(target, Path(temporary), 'owner/repo', environment={'GITHUB_ACTIONS': 'true'},
                provider_map={'fixture': lambda c: [Step('metadata', ('fixture',))]},
                execute=lambda *args: (0, 'metadata discovered'))
            self.assertTrue(report['executed'])
            self.assertEqual(report['status'], 'ok')
            self.assertEqual(plan(target, Path(temporary), 'owner/repo', provider_map={
                'fixture': lambda c: [Step('metadata', ('fixture',))]})['optional_capabilities'], ['apkeep'])

    def test_target_metadata_cannot_stage_code_or_escape_the_checkout(self):
        for path in ('src', '../secrets', '/etc', 'data/../keys'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.target(metadata=[path])

class TargetRegistrationTests(unittest.TestCase):
    def test_scheduled_cli_retains_explicit_target_options(self):
        import contextlib
        import io
        from unittest.mock import patch
        from sonos_firmware.platform import cli
        visited = []
        def execute_target(target, root, repository, **kwargs):
            return run(target, root, repository, environment={'GITHUB_ACTIONS': 'true'},
                       provider_map={'fixture': lambda c: [Step('check', ('fixture', str(c.options['limit'])))]},
                       execute=lambda step, *args: (visited.append(step.command[-1]) or 0, 'configured task'), **kwargs)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'config').mkdir()
            (root/'config/targets.json').write_text(json.dumps({'schema_version': 1, 'targets': [
                {'name': 'configured-source', 'provider': 'fixture', 'description': 'Configured source',
                 'interval_hours': 6, 'metadata': ['data/source'], 'options': {'limit': 5}}]}))
            with patch.object(cli, 'run', side_effect=execute_target), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['--root', str(root), 'run', 'configured-source', '--execute',
                                          '--if-due', '--repository', 'owner/repo']), 0)
            self.assertEqual(visited, ['5'])

    def test_registering_and_planning_target_needs_only_configuration(self):
        import contextlib
        import io
        from sonos_firmware.platform.cli import main
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'config').mkdir()
            configuration = {'schema_version': 1, 'targets': [
                {'name': 'new-desktop-target', 'provider': 'desktop', 'description': 'New target',
                 'interval_hours': 6, 'metadata': ['data/apps/desktop-discovery.json', 'data/apps/desktop-archive.json']} ]}
            (root / 'config/targets.json').write_text(json.dumps(configuration))
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(main(['--root', str(root), 'run', 'new-desktop-target',
                                       '--repository', 'owner/repo']), 0)
            report = json.loads(output.getvalue())
            self.assertEqual(report['status'], 'planned')
            self.assertFalse(report['executed'])
            self.assertFalse((root / 'data').exists())
            self.assertEqual(sorted(p.name for p in root.iterdir()), ['config'])


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
    def test_unattempted_discovery_survives_limit_and_later_listing_outage(self):
        import importlib.util
        from unittest.mock import patch
        from types import SimpleNamespace
        import sys
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('queued_sweep', root/'scripts/sweep_android_store_history.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            acquired = []
            def acquire(command, **kwargs):
                acquired.append(command[command.index('--version-selector')+1])
                state = json.loads((directory/'state.json').read_text())
                self.assertEqual({row['version_selector'] for row in state['pending_selectors']}, {'first', 'later'})
                return SimpleNamespace(returncode=1, stdout='fixture acquisition failure')
            with patch.object(sys, 'argv', ['sweep', '--package', 'com.sonos.acr2', '--limit', '1',
                    '--receipt', str(directory/'archive.json'), '--ledger', str(directory/'state.json')]), \
                 patch.object(module, 'discover_versions', side_effect=[['first', 'later'], OSError('listing outage')]), \
                 patch.object(module, 'discover_apkpure', return_value={'variants': [], 'errors': []}), \
                 patch.object(module, 'discover_sdk_levels', return_value={'levels': [29], 'errors': []}), \
                 patch.object(module, 'run_acquisition', side_effect=acquire):
                self.assertEqual(module.main(), 1)
                self.assertEqual(module.main(), 1)
            self.assertEqual(acquired, ['first', 'later'])

    def test_previously_observed_selector_survives_an_unavailable_store_listing(self):
        import importlib.util
        from unittest.mock import patch
        from types import SimpleNamespace
        import sys
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('retained_sweep', root/'scripts/sweep_android_store_history.py')
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
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('sweep_fixture', root / 'scripts/sweep_android_store_history.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            receipt = Path(temporary) / 'archive.json'
            ledger = Path(temporary) / 'state.json'
            argv = ['sweep', '--package', 'com.sonos.acr2', '--receipt', str(receipt),
                    '--ledger', str(ledger), '--limit', '1', '--repo', 'owner/repo']
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
            self.assertEqual(acquired, ['old', 'new', 'old'])
            document = json.loads(ledger.read_text())
            self.assertEqual(document['pending_total'], 2)
            self.assertEqual(document['status'], 'partial')

class MetadataPublicationTests(unittest.TestCase):
    def test_metadata_commit_preserves_unrelated_local_work_and_retains_patch(self):
        import subprocess
        from sonos_firmware.platform.git_state import commit_metadata
        def git(root, *args):
            return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL, text=True)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            remote, root, worker = base / 'remote.git', base / 'checkout', base / 'worker'
            root.mkdir(); worker.mkdir()
            git(base, 'init', '--bare', '--initial-branch=main', str(remote))
            git(root, 'init', '--initial-branch=main')
            git(root, 'config', 'user.name', 'Fixture')
            git(root, 'config', 'user.email', 'fixture@example.invalid')
            (root / 'source.py').write_text('initial source\n')
            git(root, 'add', 'source.py'); git(root, 'commit', '-m', 'Initial fixture')
            git(root, 'remote', 'add', 'origin', str(remote))
            git(root, 'push', '-u', 'origin', 'main')
            # Automation owns metadata only. Refuse pre-staged human work.
            (root / 'source.py').write_text('user change\n')
            git(root, 'add', 'source.py')
            target = Target.from_dict({'name': 'fixture', 'provider': 'fixture', 'description': 'fixture',
                                       'interval_hours': 1, 'metadata': ['data/fixture.json']})
            (root / 'data').mkdir()
            (root / 'data/fixture.json').write_text('{"published": true}\n')
            environment = {'GITHUB_ACTIONS': 'true', 'RUNNER_TEMP': str(worker)}
            with self.assertRaisesRegex(ValueError, 'pre-staged'):
                commit_metadata(root, target, environment)
            git(root, 'reset', '--quiet', '--', 'source.py')
            # Rebase refuses unrelated dirty work, but the metadata checkpoint
            # must remain recoverable and the user's edit must remain intact.
            with self.assertRaises(subprocess.CalledProcessError):
                commit_metadata(root, target, environment)
            self.assertIn('data/fixture.json', (worker / 'metadata-checkpoint.patch').read_text())
            self.assertNotIn('user change', (worker / 'metadata-checkpoint.patch').read_text())
            self.assertEqual((root / 'source.py').read_text(), 'user change\n')


class FirmwareBootstrapTests(unittest.TestCase):
    def test_disabled_hint_source_retains_its_observations_without_fetching_or_using_them(self):
        import importlib.util
        import sys
        from unittest.mock import patch
        script = Path(__file__).resolve().parents[1]/'scripts/discover_firmware.py'
        spec = importlib.util.spec_from_file_location('disabled_hint_fixture', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            notes = {'versions': ['99.1-12345'], 'status': 'failed', 'error': 'previous outage'}
            state_path = root/'state.json'
            state_path.write_text(json.dumps({'release_notes': notes}))
            state = {'manifests': [], 'candidates': [], 'probes': {}, 'checked_urls': [],
                     'checked_this_run': 0, 'pending_manifest_urls': [], 'status': 'ok'}
            argv = [str(script), '--state', str(state_path), '--catalog', str(root/'catalog.json'),
                    '--evidence-dir', str(root/'metadata'), '--report', str(root/'report.json'),
                    '--no-history-index', '--no-release-notes']
            with patch.object(sys, 'argv', argv), patch.object(module, 'discover', return_value=state) as discover, \
                 patch.object(module, 'discover_version_hints') as hints:
                self.assertEqual(module.main(), 0)
            hints.assert_not_called()
            self.assertEqual(discover.call_args.kwargs['version_hints'], ())
            self.assertEqual(json.loads(state_path.read_text())['release_notes'], notes)
            self.assertEqual(json.loads((root/'report.json').read_text())['status'], 'ok')

    def test_empty_firmware_catalog_is_created_by_worker_without_binary_inputs(self):
        import importlib.util
        import sys
        from unittest.mock import patch
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        spec = importlib.util.spec_from_file_location('bootstrap_fixture', scripts / 'discover_firmware.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary, patch.object(sys, 'path', [str(scripts), *sys.path]):
            root = Path(temporary)
            state = {'manifests': [], 'probes': {}, 'checked_urls': [], 'status': 'ok'}
            self.assertFalse(module.preserve(state, root / 'catalog.json', root / 'frontier.json',
                                             root / 'evidence', root / 'work', 'owner/repo'))
            catalog = json.loads((root / 'catalog.json').read_text())
            self.assertEqual(catalog['packages'], [])
            self.assertEqual(catalog['raw_images'], [])
            self.assertEqual(catalog['source_manifests'], [])


    def test_captured_manifest_can_resume_after_worker_directory_changes(self):
        import hashlib
        import importlib.util
        import sys
        from unittest.mock import patch
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        spec = importlib.util.spec_from_file_location('capture_resume_fixture', scripts / 'discover_firmware.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary, patch.object(sys, 'path', [str(scripts), *sys.path]):
            root = Path(temporary)
            evidence, work = root / 'restored-metadata', root / 'publication'
            evidence.mkdir(); work.mkdir()
            body = b'<update_manifest version="1.1"/>'
            (evidence / 'captured.upm').write_bytes(body)
            manifest = {'url': 'https://update.sonos.com/firmware/update.upm',
                        'snapshot': '/previous-worker/temporary/captured.upm',
                        'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest(),
                        'metadata': {'system_version': '1.0'}}
            state = {'manifests': [manifest], 'probes': {}, 'checked_urls': [], 'status': 'ok'}
            with patch.object(module, 'ensure_release'), patch.object(module, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}) as upload:
                self.assertFalse(module.preserve(state, root / 'catalog.json', root / 'frontier.json',
                                                 evidence, work, 'owner/repo'))
            upload.assert_called_once()
            self.assertEqual(len(json.loads((root / 'catalog.json').read_text())['source_manifests']), 1)


class ReceiptRepositoryTests(unittest.TestCase):
    def test_another_repository_cannot_suppress_selected_repository_publication(self):
        from sonos_firmware.releases import receipt_matches_repository
        record = {'components': [{'release_url': 'https://github.com/owner/original/releases/download/tag/base.apk'}]}
        self.assertTrue(receipt_matches_repository(record, 'owner/original'))
        self.assertFalse(receipt_matches_repository(record, 'owner/fork'))
        record['complete_wrapper'] = {'release_url': 'https://github.com/owner/fork/releases/download/tag/wrapper.xapk'}
        self.assertFalse(receipt_matches_repository(record, 'owner/original'))
        self.assertFalse(receipt_matches_repository({}, 'owner/original'))


class PublicationRegressionTests(unittest.TestCase):
    def test_secret_crossing_report_tail_boundary_is_redacted(self):
        token = 'SECRET-' + 'x' * 5000
        with tempfile.TemporaryDirectory() as temporary:
            target = Target.from_dict({'name': 'fixture', 'provider': 'fixture', 'description': 'fixture',
                                       'interval_hours': 1, 'metadata': []})
            report = run(target, Path(temporary), 'owner/repo',
                         environment={'GITHUB_ACTIONS': 'true', 'GH_TOKEN': token},
                         provider_map={'fixture': lambda c: [Step('test', ('fixture',))]},
                         execute=lambda *args: (1, 'error ' + token + ' trailing output'))
            self.assertNotIn('x' * 100, json.dumps(report))
            self.assertIn('[REDACTED]', json.dumps(report))

    def test_asset_inventory_falls_back_completely_after_later_page_challenge(self):
        import io
        import urllib.error
        from unittest.mock import patch
        from sonos_firmware import releases
        first_page = [{'name': 'asset-' + str(n)} for n in range(100)]
        error = urllib.error.HTTPError('https://api.github.com/page2', 403, 'rate limited', {}, None)
        with patch.object(releases, 'public_release', return_value={'id': 1}), \
             patch.object(releases.urllib.request, 'urlopen', side_effect=[io.BytesIO(json.dumps(first_page).encode()), error]), \
             patch.object(releases, 'graphql_assets', return_value=first_page + [{'name': 'last-asset'}]) as fallback:
            result = releases.remote_assets('owner/repo', 'tag')
        self.assertEqual(len(result), 101)
        self.assertIn('last-asset', result)
        fallback.assert_called_once_with('owner/repo', 'tag')

    def test_deleted_tracked_metadata_is_published(self):
        import subprocess
        from sonos_firmware.platform.git_state import commit_metadata
        def git(root, *args):
            return subprocess.check_output(['git', *args], cwd=root, stderr=subprocess.DEVNULL, text=True)
        with tempfile.TemporaryDirectory() as temporary:
            base = Path(temporary)
            remote, root = base / 'remote.git', base / 'checkout'
            root.mkdir()
            git(base, 'init', '--bare', '--initial-branch=main', str(remote))
            git(root, 'init', '--initial-branch=main')
            git(root, 'config', 'user.name', 'Fixture'); git(root, 'config', 'user.email', 'fixture@example.invalid')
            (root / 'data').mkdir()
            metadata = root / 'data/obsolete.json'
            metadata.write_text('{}')
            git(root, 'add', 'data'); git(root, 'commit', '-m', 'Initial fixture')
            git(root, 'remote', 'add', 'origin', str(remote)); git(root, 'push', '-u', 'origin', 'main')
            metadata.unlink()
            target = Target.from_dict({'name': 'fixture', 'provider': 'fixture', 'description': 'fixture',
                                       'interval_hours': 1, 'metadata': ['data/obsolete.json']})
            self.assertTrue(commit_metadata(root, target, {'GITHUB_ACTIONS': 'true'}))
            self.assertEqual(git(root, 'status', '--porcelain'), '')
            self.assertEqual(git(base, '--git-dir=' + str(remote), 'ls-tree', '-r', '--name-only', 'main'), '')


class IndependentSourceFailureTests(unittest.TestCase):
    def load(self, name):
        import importlib.util
        scripts = Path(__file__).resolve().parents[1] / 'scripts'
        spec = importlib.util.spec_from_file_location('fixture_' + name, scripts / (name + '.py'))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_desktop_history_failure_keeps_current_discovery_and_reports_partial(self):
        import sys
        from unittest.mock import patch
        module = self.load('discover_desktop_installers')
        redirect = {'name': 's2-windows', 'url': 'https://www.sonos.com/redir/controller_software_pc2',
                    'location': 'https://update-software.sonos.com/software/pc/dcr/Sonos_90.0-12345.exe',
                    'location_source': 'live-redirect'}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'discovery.json'
            with patch.object(sys, 'argv', ['discover', '--output', str(output)]), \
                 patch.object(module, 'winget_candidates', side_effect=OSError('fixture unavailable')), patch.object(module, 'winget_history', return_value={'status': 'ok', 'pending_total': 0, 'errors': []}), \
                 patch.object(module, 'homebrew_candidates', return_value=None), \
                 patch.object(module, 'check_direct_redirects', return_value=[redirect]), \
                 patch.object(module, 'probe', side_effect=lambda item: {**item, 'http_status': 200}):
                self.assertEqual(module.main(), 0)
            report = json.loads(output.read_text())
            self.assertEqual(report['status'], 'partial')
            self.assertEqual(report['available_total'], 2)
            self.assertEqual(report['errors'][0]['source'], 'winget')

    def test_mobile_current_discovery_does_not_query_the_separate_history_index(self):
        import sys
        from unittest.mock import patch
        module = self.load('discover_mobile_installers')
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'discovery.json'
            with patch.object(sys, 'argv', ['discover', '--output', str(output)]), \
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
