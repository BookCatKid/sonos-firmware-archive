"""Per-run budgets must never become permanent coverage ceilings.

Item 15 requires fixed ranges, pagination limits and traversal exclusions to be
audited, and per-run budgets to resume fairly. Two properties decide that:
every scheduler call persists its rotation lane, and a bounded space widens
instead of declaring itself complete.
"""
import importlib.util
import json
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('ceilings', ROOT/'scripts/audit_discovery_ceilings.py')
ceilings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ceilings)


class LiveRepositoryTests(unittest.TestCase):
    def test_no_scheduler_call_can_turn_a_batch_into_a_lifetime_ceiling(self):
        calls = ceilings.scheduler_calls(ROOT)
        self.assertGreaterEqual(len(calls), 15)
        self.assertEqual(ceilings.unrotated_calls(calls), [])
        self.assertEqual(ceilings.unnamed_queue_calls(calls), [])

    def test_the_model_space_still_widens(self):
        bounds = ceilings.expanding_bounds(ROOT)
        self.assertTrue(bounds['model_space_expands'])
        self.assertTrue(bounds['model_policy_named'])

    def test_the_audit_passes_on_the_committed_repository(self):
        report = ceilings.audit(ROOT)
        self.assertEqual(ceilings.failures(report), [])
        self.assertGreaterEqual(report['configurable_budget_count'], 20)
        # The model bound is exposed as a per-run option, not hardcoded only.
        self.assertIn('model_max', report['configurable_budgets'])
        self.assertIn('limit', report['configurable_budgets'])

    def test_every_worker_budget_is_configurable_from_target_options(self):
        budgets = ceilings.configurable_budgets(ROOT)
        # The budgets that bound a run's work must all be overridable.
        for name in ('limit', 'seconds', 'history_limit', 'extraction_limit',
                     'inventory_limit', 'binary_limit', 'discovery_limit'):
            with self.subTest(budget=name):
                self.assertIn(name, budgets)


class FixtureAuditTests(unittest.TestCase):
    def make_root(self, temporary, *, frontier_body='', script_body=''):
        root = Path(temporary)
        (root/'src/sonos_firmware/platform').mkdir(parents=True, exist_ok=True)
        (root/'scripts').mkdir(parents=True, exist_ok=True)
        (root/'src/sonos_firmware/frontier.py').write_text(frontier_body)
        (root/'src/sonos_firmware/platform/providers.py').write_text(
            'def firmware(c):\n    return [c.options.get("limit", 24)]\n')
        if script_body:
            (root/'scripts/worker.py').write_text(script_body)
        return root

    def test_a_call_without_state_is_reported_as_a_lifetime_ceiling(self):
        body = textwrap.dedent('''
            from sonos_firmware.retry import select
            def run(items, records):
                return select(items, records, key=str, limit=5, queue='q')
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, script_body=body)
            calls = ceilings.scheduler_calls(root)
        self.assertEqual(len(calls), 1)
        self.assertFalse(calls[0]['persists_state'])
        self.assertEqual(len(ceilings.unrotated_calls(calls)), 1)

    def test_a_call_sharing_the_default_lane_is_reported(self):
        body = textwrap.dedent('''
            from sonos_firmware.retry import select
            def run(items, records, state):
                return select(items, records, key=str, limit=1, state=state)
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, script_body=body)
            calls = ceilings.scheduler_calls(root)
        self.assertEqual([row['line'] for row in ceilings.unnamed_queue_calls(calls)], [4])

    def test_a_fully_persisted_call_is_accepted(self):
        body = textwrap.dedent('''
            from sonos_firmware.retry import select
            def run(items, records, state):
                return select(items, records, key=str, limit=1, state=state, queue='lane')
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, script_body=body)
            calls = ceilings.scheduler_calls(root)
        self.assertEqual(ceilings.unrotated_calls(calls), [])
        self.assertEqual(ceilings.unnamed_queue_calls(calls), [])

    def test_the_stdlib_selector_is_not_mistaken_for_the_scheduler(self):
        body = textwrap.dedent('''
            import selectors
            def run():
                with selectors.DefaultSelector() as selector:
                    return selector.select(1)
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, script_body=body)
            calls = ceilings.scheduler_calls(root)
        self.assertEqual(calls, [])

    def test_a_model_space_that_stops_at_its_bound_is_reported(self):
        body = "def _namespace_candidates(rows):\n    row['next_model'] = 0\n"
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(temporary, frontier_body=body)
            report = ceilings.audit(root)
        self.assertFalse(report['expanding_bounds']['model_space_expands'])
        self.assertTrue(any('widens' in problem for problem in ceilings.failures(report)))

    def test_the_cli_fails_on_an_unrotated_call_and_writes_a_report(self):
        body = textwrap.dedent('''
            from sonos_firmware.retry import select
            def run(items, records):
                return select(items, records, key=str, limit=5, queue='q')
        ''')
        with tempfile.TemporaryDirectory() as temporary:
            root = self.make_root(
                temporary, script_body=body,
                frontier_body="row['model_max'] = 2*row['model_max'] + 1\n'expanding-passes'\n")
            output = root/'report.json'
            with patch.object(sys, 'argv', ['audit', '--root', str(root), '--output', str(output)]):
                self.assertEqual(ceilings.main(), 1)
            self.assertEqual(len(json.loads(output.read_text())['unrotated_calls']), 1)


if __name__ == '__main__':
    unittest.main()
