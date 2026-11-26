"""Executable proof that former work quotas cannot be configured or enforced."""
from __future__ import annotations

import ast
import inspect
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import deadline
from sonos_firmware.archive_index import discover_archives
from sonos_firmware.binary_discovery import discover_preserved
from sonos_firmware.firmware_inventory import inventory_package
from sonos_firmware.gpl_binary_evidence import scan_binary
from sonos_firmware.metadata_sweep import discover_profiles
from sonos_firmware.platform.model import Step, Target
from sonos_firmware.platform.registry import targets
from sonos_firmware.platform.runner import plan, run
from sonos_firmware.retry import select
from sonos_firmware.web_archive import archive_apps

ROOT = Path(__file__).resolve().parents[2]
REMOVED = {
    'limit', 'pages', 'max_assets', 'max_bytes', 'max_source_bytes',
    'max_entry_bytes', 'max_packages', 'max_manifests', 'max_sources',
    'max_total_bytes', 'max_files', 'max_wrappers', 'inventory_limit',
    'inventory_limits', 'discovery_limit', 'history_limit', 'profile_limit',
    'profile_count', 'model_max', 'inference_limit', 'pivot_limit',
    'reserve_bytes', 'min_free_bytes', 'deadline', 'max_depth', 'seconds',
}
FORBIDDEN_FLAGS = (
    '--limit', '--pages', '--max-assets', '--max-bytes',
    '--max-source-bytes', '--max-entry-bytes', '--max-packages',
    '--max-manifests', '--max-total-bytes', '--max-sources',
    '--inventory-limit', '--inventory-limits', '--discovery-limit',
    '--profile-limit', '--model-max', '--inference-limit',
    '--pivot-limit', '--reserve-bytes', '--seconds',
)


class NoWorkQuotas(unittest.TestCase):
    def test_no_production_function_has_a_work_quota_parameter(self):
        """Scan source, not just a shortlist of public function signatures."""
        bad = []
        for directory in ('src', 'scripts'):
            for path in (ROOT / directory).rglob('*.py'):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        args = node.args
                        names = [p.arg for p in [*args.posonlyargs, *args.args, *args.kwonlyargs]]
                        for name in REMOVED.intersection(names):
                            bad.append(f'{path.relative_to(ROOT)}:{node.lineno} {node.name}({name})')
        self.assertEqual(bad, [])

    def test_no_work_quota_flag_is_registered_with_any_parser(self):
        found = []
        for directory in ('src', 'scripts'):
            for path in (ROOT / directory).rglob('*.py'):
                tree = ast.parse(path.read_text())
                for node in ast.walk(tree):
                    if not isinstance(node, ast.Call):
                        continue
                    if not isinstance(node.func, ast.Attribute) or node.func.attr != 'add_argument':
                        continue
                    for arg in node.args:
                        if isinstance(arg, ast.Constant) and arg.value in FORBIDDEN_FLAGS:
                            found.append((str(path.relative_to(ROOT)), node.lineno, arg.value))
        self.assertEqual(found, [])

    def test_python_invocation_rejects_removed_quota_keywords(self):
        cases = (
            (discover_archives, ({},)),
            (discover_preserved, (ROOT, ROOT, 'owner/repo')),
            (inventory_package, (ROOT / 'missing.upd', ROOT / 'missing-dir')),
            (scan_binary, (ROOT / 'missing.bin',)),
            (discover_profiles, ({}, ROOT / 'missing', {})),
            (archive_apps, (('pro',), ROOT, ROOT / 'missing.json', 'owner/repo')),
            (select, ([], {})),
        )
        for function, args in cases:
            for keyword in ('limit', 'max_bytes', 'deadline'):
                with self.subTest(function=function.__name__, keyword=keyword):
                    with self.assertRaisesRegex(TypeError, 'unexpected keyword argument'):
                        function(*args, **{keyword: 1})

    def test_public_discovery_entrypoints_do_not_have_wildcard_quota_options(self):
        """Unknown arguments cannot bypass a strict discovery interface."""
        from sonos_firmware import archive_index, frontier, commoncrawl
        for function in (archive_index.discover_archives, archive_index.discover_sources,
                         frontier.discover, frontier.installer_manifest_roots):
            with self.subTest(function=function.__name__):
                self.assertIsNone(inspect.signature(function).parameters.get('kwargs'))
                self.assertFalse(any(param.kind is inspect.Parameter.VAR_KEYWORD
                                     for param in inspect.signature(function).parameters.values()))
        with self.assertRaisesRegex(TypeError, 'unexpected keyword argument'):
            archive_index.discover_archives({}, limit=3)

    def test_builtin_targets_reject_unknown_options_including_limits(self):
        builtin = targets(ROOT)
        for target in builtin.values():
            for keyword in ('limit', 'max_bytes', 'pages', 'seconds', 'reserve_bytes'):
                with self.subTest(target=target.name, keyword=keyword):
                    with self.assertRaisesRegex(ValueError, 'unsupported .* provider option'):
                        plan(target, ROOT, 'owner/repo', options={keyword: 1})

    def test_command_line_quota_flags_are_unrecognized(self):
        cases = (
            ('scripts/apps/sweep_android_store_history.py', (), '--limit'),
            ('scripts/gpl/archive_gpl_direct.py', ('discover',), '--limit'),
            ('scripts/gpl/audit_gpl_published_streams.py', (), '--reserve-bytes'),
            ('scripts/firmware/discover_firmware.py',
             ('--evidence-dir', '/tmp/sonos-quota-test', '--report', '/tmp/sonos-quota-test.json'),
             '--max-manifests'),
            ('scripts/gpl/discover_gpl_sitemaps.py', (), '--limit'),
        )
        for relative, required, option in cases:
            with self.subTest(script=relative, option=option):
                result = subprocess.run(
                    [sys.executable, str(ROOT / relative), *required, '--help'],
                    cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertNotIn(option, result.stdout)
                actual = subprocess.run(
                    [sys.executable, str(ROOT / relative), *required, option, '1'],
                    cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
                self.assertEqual(actual.returncode, 2, actual.stderr)
                self.assertIn('unrecognized arguments', actual.stderr)
                self.assertIn(option, actual.stderr)

    def test_actual_cli_set_limit_fails_before_planning(self):
        actual = subprocess.run([
            sys.executable, '-m', 'sonos_firmware.platform.cli', '--root', str(ROOT),
            'run', 'firmware', '--plan', '--repository', 'owner/repo', '--set', 'limit=999'],
            cwd=ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        self.assertEqual(actual.returncode, 2)
        self.assertIn('unsupported firmware provider option(s): limit', actual.stderr)

    def test_source_scan_can_try_all_five_published_mirrors(self):
        """Every published mirror is eligible for a verified acquisition."""
        import hashlib
        import zipfile
        import io
        from sonos_firmware.storage import atomic_json
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('classes.dex', b'/documents/gpl/22.17/gpl.html')
        data = stream.getvalue()
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            receipt = [
                {'release_asset': 'source.apk', 'release_tag': str(i),
                 'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data)}
                for i in range(5)
            ]
            atomic_json(root / 'data/apps/mobile-archive.json', {'artifacts': receipt})
            tried = []
            def acquire(src, destination, repository):
                tried.append(src['release_tag'])
                if src['release_tag'] != '4':
                    raise FileNotFoundError('offline mirror missing')
                destination.write_bytes(data)
            result = discover_preserved(root, root / 'work', 'owner/repo', acquire=acquire)
            self.assertEqual(tried, ['0', '1', '2', '3', '4'])
            self.assertEqual(result['status'], 'ok')
            self.assertIn('22.17', result['version_evidence'])

    def test_shared_clock_does_not_starve_evidence_based_discovery_sources(self):
        from dataclasses import asdict
        from unittest.mock import patch
        import urllib.error
        from sonos_firmware import frontier
        observed = ('https://update.sonos.com/firmware/Prod/'
                    '90.0-67171-v17.2-ZethjbGivZ-B123/update.upm')
        namespace = 'https://update.sonos.com/firmware/Prod/observed/^1.2-345'
        state = {
            'manifests': [{'url': observed, 'sha256': 'a' * 64, 'metadata': {},
                           'snapshot': 'missing.upm', 'bytes': 10}],
            'inferred_namespaces': {namespace: {'source': 'metadata-base', 'next_model': 0}},
        }
        seen_roots, seen_models = [], []
        def missing(url):
            seen_roots.append(url)
            raise urllib.error.HTTPError(url, 404, 'missing', {}, None)
        def probe(candidate):
            seen_models.append(candidate.url)
            return {**asdict(candidate), 'available': False, 'http_status': 404}
        def profile(candidate):
            raise urllib.error.HTTPError('https://update.sonos.com/', 503,
                                         'unavailable', {'Retry-After': '180'}, None)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.dict(os.environ, {'SONOS_WIND_DOWN_AT': str(time.time() + 1.5)}), \
             patch.object(frontier, 'protocol_roots', return_value=[]):
            report = frontier.discover(state, Path(temporary),
                fetch=missing, probe_fn=probe, metadata_fetch=lambda: (b'', {}),
                profile_fetch=profile, installer_pivots=[
                    {'build': '90.0-67171', 'app_version': '17.2', 'token': 'ZethjbGivZ'}])
        self.assertTrue(any('-B0/update.upm' in url or '-B1/update.upm' in url
                            for url in seen_roots), seen_roots)
        self.assertTrue(seen_models, 'the model probe must not starve behind unlimited pivot exploration')
        self.assertGreaterEqual(report['metadata_profiles']['attempted_this_run'], 1)
        self.assertGreaterEqual(report['inferred_namespaces'][namespace]['next_model'], 1)

    def test_workflow_uses_soft_signal_without_an_explicit_job_kill(self):
        workflow = (ROOT / '.github/workflows/archive.yml').read_text()
        self.assertIn('SONOS_WIND_DOWN_AT=', workflow)
        self.assertIn('4 * 60 * 60', workflow)
        self.assertNotIn('timeout-minutes:', workflow)
        runner = (ROOT / 'src/sonos_firmware/platform/runner.py').read_text()
        self.assertNotIn('os.killpg', runner)
        self.assertNotIn('process.wait(timeout=', runner)

    def test_four_hours_is_a_soft_signal_not_a_process_timeout(self):
        with patch.dict(os.environ, {'SONOS_WIND_DOWN_AT': str(4*3600+100)}):
            with patch.object(deadline.time, 'time', return_value=100):
                self.assertEqual(deadline.remaining(), 4*3600)
                self.assertFalse(deadline.winding_down())
            with patch.object(deadline.time, 'time', return_value=4*3600+100):
                self.assertTrue(deadline.winding_down())
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(deadline.remaining())
            self.assertFalse(deadline.winding_down())
            self.assertFalse(hasattr(deadline, 'monotonic'))
        self.assertFalse(hasattr(deadline, 'timeout'))

    def test_running_step_finishes_after_soft_cutoff_and_next_is_deferred(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / 'finished'
            late = directory / 'new-step'
            command = (sys.executable, '-c',
                       f'import time; from pathlib import Path; '
                       f'time.sleep(.15); Path({str(marker)!r}).touch()')
            next_command = (sys.executable, '-c',
                            f'from pathlib import Path; Path({str(late)!r}).touch()')
            target = Target('test-soft-stop', 'fixture', 'fixture', 1, ('data/test.json',))
            with patch.dict(os.environ, {'SONOS_WIND_DOWN_AT': str(time.time()+.04)}):
                report = run(
                    target, directory, 'owner/repo',
                    environment={**os.environ, 'GITHUB_ACTIONS': 'true', 'RUNNER_TEMP': temporary},
                    provider_map={'fixture': lambda context: [Step('finish', command), Step('defer', next_command)]})
            self.assertTrue(marker.exists(), report)
            self.assertFalse(late.exists(), report)
            self.assertEqual([r['status'] for r in report['steps']], ['ok', 'skipped'])
            self.assertEqual(report['status'], 'partial')
            self.assertIn('wind-down', report['pending'][0]['reason'])


if __name__ == '__main__':
    unittest.main()
