"""Verify the common workflow preserves worker setup and durable failure reports."""
import unittest
from pathlib import Path
import yaml

ROOT = Path(__file__).resolve().parents[2]


class PlatformWorkflowTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.workflow = yaml.safe_load((ROOT / '.github/workflows/archive.yml').read_text())
        cls.job = cls.workflow['jobs']['archive']
        cls.steps = {s.get('id', s.get('name', s.get('uses'))): s for s in cls.job['steps']}

    def test_targets_drive_one_matrix_and_shared_metadata_locks(self):
        self.assertIn('fromJSON(needs.select.outputs.matrix)', self.job['strategy']['matrix'])
        self.assertFalse(self.job['strategy']['fail-fast'])
        # Per-family locks serialize shared metadata. A global matrix cap lets
        # waiting firmware/GPL jobs block unrelated targets from being queued.
        self.assertNotIn('max-parallel', self.job['strategy'])
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
        failure = self.steps['Report failed work']
        self.assertEqual(failure['if'],
                         "always() && (steps.run.outcome == 'failure' || steps.commit.outcome == 'failure')")
        self.assertEqual(failure['run'], 'exit 1')


    def test_keys_are_scoped_to_execution_and_not_logged(self):
        run = self.steps['run']
        self.assertIn('umask 077', run['run'])
        self.assertNotIn('set -x', run['run'])
        for model in (1, 8, 9, 12, 16, 17):
            name = f'SONOS_MODEL_{model}_PRIVATE_KEY'
            self.assertEqual(run['env'][name], '${{ secrets.' + name + ' }}')


if __name__ == '__main__':
    unittest.main()
