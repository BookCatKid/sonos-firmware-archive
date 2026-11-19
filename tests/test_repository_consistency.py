"""Repository consistency invariants stay true as targets and scripts change.

Item 20 requires that obsolete entrypoints and duplicated logic be reconciled,
stale documentation corrected and reporting made actionable. These checks make
the invariants executable so a future change cannot quietly orphan an adapter,
a script or a documentation link.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('repo_consistency', ROOT/'scripts/audit_repository_consistency.py')
consistency = importlib.util.module_from_spec(spec)
spec.loader.exec_module(consistency)


class LiveRepositoryTests(unittest.TestCase):
    """The committed repository satisfies every invariant."""

    def test_the_repository_is_consistent(self):
        report = consistency.audit(ROOT)
        self.assertEqual(report['providers_missing_implementation'], [])
        self.assertEqual(report['providers_never_configured'], [])
        self.assertEqual(report['unreachable_targets'], [])
        self.assertEqual(report['orphan_scripts'], [])
        self.assertEqual(report['documentation_paths'], [])
        self.assertEqual(report['retired_workflows'], [])
        self.assertEqual(consistency.failures(report), [])

    def test_every_configured_provider_is_implemented_and_reachable(self):
        report = consistency.audit(ROOT)
        self.assertEqual(report['providers_configured'], report['providers_implemented'])
        self.assertGreaterEqual(report['targets'], 13)

    def test_every_script_is_referenced_or_documented(self):
        referenced = consistency.script_references(ROOT) | consistency.documented_scripts(ROOT)
        scripts = {path.name for path in (ROOT/'scripts').glob('*.py')}
        self.assertEqual(sorted(scripts - referenced), [])

    def test_the_historical_audit_links_are_pinned_so_they_are_not_stale_claims(self):
        text = (ROOT/'docs/archive-platform-runtime-audit.md').read_text()
        workflow_links = [line for line in text.splitlines() if '.github/workflows/' in line]
        self.assertTrue(workflow_links)
        # Every mention of a retired workflow is pinned to the commit it described.
        for line in workflow_links:
            self.assertTrue(consistency.PINNED.search(line) or 'archive.yml' in line
                            or 'verify.yml' in line, line)

    def test_the_unified_workflows_are_the_only_archive_entrypoints(self):
        """`archive.yml` drives every target; `verify.yml` gates changes.

        A checkout may also carry local diagnostic workflows (an isolated
        validation repository does) and may not carry the pull-request gate, so
        this asserts what must be true rather than an exact file list.
        """
        present = {path.name for path in (ROOT/'.github/workflows').glob('*.yml')}
        self.assertIn('archive.yml', present)
        text = (ROOT/'.github/workflows/archive.yml').read_text()
        # The scheduled archive workflow selects targets from configuration.
        self.assertIn('config/targets.json', text)
        self.assertIn('sonos-archive targets', text)
        # No second workflow claims to run an archive target.
        for path in (ROOT/'.github/workflows').glob('*.yml'):
            if path.name == 'archive.yml':
                continue
            self.assertNotIn('sonos-archive run', path.read_text(), path.name)

    def test_the_committed_verify_workflow_runs_every_audit(self):
        """Where the gate exists, it must exercise the audits it advertises."""
        verify = ROOT/'.github/workflows/verify.yml'
        if not verify.exists():
            self.skipTest('this checkout has no pull-request verify workflow')
        text = verify.read_text()
        for script in ('run_tests.py', 'audit_repository_consistency.py',
                       'audit_discovery_ceilings.py'):
            with self.subTest(script=script):
                self.assertIn(script, text)


class FixtureRepositoryTests(unittest.TestCase):
    def make_root(self, temporary, *, targets=None, scripts=(), docs=None):
        root = Path(temporary)
        (root/'config').mkdir(parents=True, exist_ok=True)
        (root/'src').mkdir(parents=True, exist_ok=True)
        (root/'docs').mkdir(parents=True, exist_ok=True)
        (root/'.github/workflows').mkdir(parents=True, exist_ok=True)
        (root/'tests').mkdir(parents=True, exist_ok=True)
        (root/'scripts').mkdir(parents=True, exist_ok=True)
        (root/'config/targets.json').write_text(json.dumps({'schema_version': 1, 'targets': targets or []}))
        for name in scripts:
            (root/'scripts'/name).write_text('"""Fixture."""\n')
        for name, body in (docs or {}).items():
            (root/'docs'/name).write_text(body)
        return root

    def test_an_orphan_script_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, scripts=['forgotten.py'])
            report = consistency.audit(root)
        self.assertEqual(report['orphan_scripts'], ['forgotten.py'])

    def test_a_documented_script_is_not_an_orphan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, scripts=['documented.py'],
                                  docs={'tools.md': 'Run `scripts/documented.py`.\n'})
            report = consistency.audit(root)
        self.assertEqual(report['orphan_scripts'], [])

    def test_a_script_referenced_by_another_script_is_not_an_orphan(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, scripts=['helper.py', 'entry.py'])
            (root/'scripts/entry.py').write_text('"""Entry."""\nRUN = "helper.py"\n')
            report = consistency.audit(root)
        self.assertEqual(report['orphan_scripts'], ['entry.py'])

    def test_a_documentation_path_that_does_not_exist_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, docs={'guide.md': 'See `docs/gone.md`.\n'})
            report = consistency.audit(root)
        self.assertEqual([row['path'] for row in report['documentation_paths']], ['docs/gone.md'])

    def test_a_historical_commit_link_is_not_treated_as_a_current_claim(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, docs={'history.md':
                'See [old](https://github.com/o/r/blob/d34f6c8/.github/workflows/retired.yml#L1).\n'
                'Also [gone](https://github.com/o/r/blob/d34f6c8/docs/also-gone.md#L2).\n'})
            report = consistency.audit(root)
        # Both lines are pinned to a commit, so neither is a claim about today.
        self.assertEqual(report['documentation_paths'], [])
        self.assertEqual(report['retired_workflows'], [])

    def test_a_current_reference_to_a_retired_workflow_is_reported(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, docs={'guide.md': 'Run `.github/workflows/old.yml`.\n'})
            report = consistency.audit(root)
        self.assertEqual([row['workflow'] for row in report['retired_workflows']], ['old.yml'])

    def test_an_unknown_provider_is_reported_before_it_runs(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, targets=[{
                'name': 'new', 'provider': 'not-implemented', 'description': 'x',
                'interval_hours': 6, 'metadata': ['data/new']}])
            with patch.object(sys, 'path', [str(ROOT/'src'), *sys.path]):
                report = consistency.audit(root)
        self.assertEqual(report['providers_missing_implementation'], ['not-implemented'])

    def test_failures_are_listed_in_a_stable_order(self):
        report = {'providers_missing_implementation': ['a'], 'providers_never_configured': [],
                  'unreachable_targets': [], 'orphan_scripts': ['b.py'],
                  'documentation_paths': [], 'retired_workflows': []}
        self.assertEqual(consistency.failures(report),
                         ["providers_missing_implementation: ['a']", "orphan_scripts: ['b.py']"])

    def test_the_cli_fails_on_a_problem_and_writes_a_report(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, scripts=['forgotten.py'])
            output = root/'report.json'
            with patch.object(sys, 'argv', ['audit', '--root', str(root), '--output', str(output)]):
                self.assertEqual(consistency.main(), 1)
            self.assertEqual(json.loads(output.read_text())['orphan_scripts'], ['forgotten.py'])


if __name__ == '__main__':
    unittest.main()
