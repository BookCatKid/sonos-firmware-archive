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

    def test_acquisition_tool_cache_is_pinned_and_build_failure_is_not_hidden(self):
        cache = self.steps['apkeep_cache']
        self.assertIn("hashFiles('tools/vendor/apkeep-1.0.0.tar.gz')", cache['with']['key'])
        self.assertNotIn('restore-keys', cache['with'])
        self.assertTrue(cache['continue-on-error'])
        build = self.steps['apkeep']
        self.assertIn("cache-hit != 'true'", build['if'])
        self.assertIn(cache['with']['path'].replace('~', '$HOME'), build['run'])
        self.assertIn('sha256sum --check', self.steps['Verify pinned acquisition tool sources']['run'])

    def test_google_play_requires_explicit_consent_and_is_not_scheduled(self):
        triggers = self.workflow.get('on', self.workflow.get(True))
        self.assertFalse(triggers['workflow_dispatch']['inputs']['accept_google_play_tos']['default'])
        self.assertIn('"$ACCEPT_TERMS" == true', self.steps['run']['run'])
        from sonos_firmware.platform.registry import targets
        from sonos_firmware.platform.runner import is_due
        self.assertFalse(is_due(ROOT, targets(ROOT)['google-play']))


if __name__ == '__main__':
    unittest.main()
