"""Verify the common workflow preserves worker setup and durable failure reports."""
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[1]


class PlatformWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / '.github/workflows/archive.yml').read_text())
        cls.job = cls.workflow['jobs']['archive']
        cls.steps = {s.get('id', s.get('name', s.get('uses'))): s for s in cls.job['steps']}

    def test_targets_drive_one_matrix_and_shared_metadata_locks(self):
        self.assertIn('fromJSON(needs.select.outputs.matrix)', self.job['strategy']['matrix'])
        self.assertFalse(self.job['strategy']['fail-fast'])
        concurrency = self.job['concurrency']
        self.assertIn('matrix.lock', concurrency['group'])
        self.assertEqual(concurrency['queue'], 'max')
        self.assertFalse(concurrency['cancel-in-progress'])
        from sonos_firmware.platform.registry import targets
        registry = targets(ROOT)
        self.assertEqual(registry['gpl'].lock, registry['gpl-archive'].lock)
        self.assertEqual(registry['android-history'].lock, registry['google-play'].lock)
        self.assertEqual(set(p.name for p in (ROOT / '.github/workflows').glob('*.yml')), {'archive.yml', 'verify.yml'})

    def test_worker_failure_still_checkpoints_verified_progress_and_reports_failure(self):
        self.assertTrue(self.steps['run']['continue-on-error'])
        self.assertIn('always()', self.steps['commit']['if'])
        self.assertIn('sonos-archive commit', self.steps['commit']['run'])
        self.assertEqual(self.steps['Retain run report and metadata checkpoint']['if'], 'always()')
        failure = self.steps['Report incomplete work']
        self.assertIn("steps.run.outcome == 'failure'", failure['if'])
        self.assertIn("steps.commit.outcome == 'failure'", failure['if'])
        self.assertEqual(failure['run'], 'exit 1')

    def test_keys_are_scoped_to_execution_and_not_logged(self):
        run = self.steps['run']
        self.assertIn('umask 077', run['run'])
        self.assertNotIn('set -x', run['run'])
        for model in (1, 8, 9, 12, 16, 17):
            name = f'SONOS_MODEL_{model}_PRIVATE_KEY'
            self.assertEqual(run['env'][name], '${{ secrets.' + name + ' }}')

    def test_acquisition_uses_pinned_binary_and_optional_play_dependency(self):
        installer = self.steps['Install pinned APKPure acquisition binary']
        self.assertEqual(installer['continue-on-error'], '${{ matrix.apkeep_optional }}')
        self.assertIn('tools/install-apkeep', installer['run'])
        script = (ROOT/'tools/install-apkeep').read_text()
        self.assertIn('releases/download/1.0.0/apkeep-', script)
        self.assertIn('sha256sum --check', script)
        self.assertNotIn('cargo', str(self.job))
        self.assertEqual(self.steps['Install pinned Google Play client']['if'], 'matrix.goopdl')
        self.assertIn("'.[google-play]'", self.steps['Install pinned Google Play client']['run'])

    def test_google_play_is_scheduled_without_personal_credentials(self):
        from sonos_firmware.platform.registry import targets
        target = targets(ROOT)['google-play']
        self.assertFalse(target.manual)
        self.assertEqual(target.capabilities, ('goopdl',))
        self.assertEqual(target.optional_capabilities, ('java',))
        self.assertFalse(any(name.startswith('GOOGLE_PLAY_') for name in self.steps['run']['env']))

    def test_optional_android_tools_are_still_selected_for_worker_installation(self):
        import contextlib
        import io
        import json
        from sonos_firmware.platform.cli import main
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(main(['--root', str(ROOT), 'targets', '--matrix', '--select', 'android-history']), 0)
        worker, = json.loads(output.getvalue())['include']
        self.assertTrue(worker['java'])
        self.assertTrue(worker['java_optional'])
        self.assertTrue(worker['apkeep'])
        self.assertTrue(worker['apkeep_optional'])


if __name__ == '__main__':
    unittest.main()
