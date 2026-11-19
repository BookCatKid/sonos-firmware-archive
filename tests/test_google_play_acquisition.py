"""Anonymous delivery identity, profile coverage and durable throttle recovery."""
import importlib.util
import importlib.metadata
import json
import subprocess
import sys
import tempfile
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sonos_firmware import android_store, retry
from sonos_firmware.android_store import StoreError

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('play_sweep', ROOT/'scripts/sweep_android_store_history.py')
SWEEP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SWEEP)


class GoopdlAdapterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.client = Mock()
        self.client.details.return_value = types.SimpleNamespace(package='com.sonos.acr2', version_code=123,
                                                                 version_string='1.2.3')
        self.client.delivery.return_value = types.SimpleNamespace(version_code=123, additional_files=[])
        self.build = Mock(return_value=[types.SimpleNamespace(dest=self.root/'delivery'/'original.apk')])
        self.download = Mock(side_effect=self.write_files)
        modules = {
            'goopdl': types.ModuleType('goopdl'),
            'goopdl.client': types.SimpleNamespace(Client=Mock(return_value=self.client), Config=Mock()),
            'goopdl.api': types.SimpleNamespace(_build_specs=self.build),
            'goopdl.download': types.SimpleNamespace(download_batch=self.download,
                DownloadSpec=lambda **kwargs: types.SimpleNamespace(**kwargs)),
        }
        self.modules = modules
        mocked = patch.dict(sys.modules, modules)
        mocked.start()
        self.addCleanup(mocked.stop)

    def write_files(self, specs):
        for spec in specs:
            spec.dest.write_bytes(b'synthetic delivery')

    def test_anonymous_configuration_ignores_personal_environment_and_uses_private_worker_cache(self):
        with patch.dict('os.environ', {'GOOPDL_ACCOUNT_EMAIL': 'unused@example.invalid',
                                      'GOOPDL_AAS_TOKEN': 'unused-secret-token'}):
            android_store.play_version('com.sonos.acr2', self.root/'auth', 'armv7', 'old-device')
        options = self.modules['goopdl.client'].Config.call_args.kwargs
        self.assertIsNone(options['credentials'])
        self.assertEqual(options['config_dir'], (self.root/'auth').resolve())
        self.assertEqual(options['profile'], 'old-device')
        self.assertEqual((self.root/'auth').stat().st_mode & 0o777, 0o700)

    def test_all_delivery_parts_are_requested_and_downloaded_base_is_identifiable(self):
        output, code = android_store.acquire_play('com.sonos.acr2', self.root/'delivery', self.root/'auth',
                                                  version_code='123')
        self.assertEqual(code, '123')
        self.assertEqual((output/'base.apk').read_bytes(), b'synthetic delivery')
        self.assertEqual(self.build.call_args.args[-2:], (False, True))
        self.assertTrue(self.build.call_args.kwargs['dm'])
        self.client.delivery.assert_called_once_with('com.sonos.acr2', 123)

    def test_wrong_delivery_version_is_rejected_before_any_payload_download(self):
        self.client.delivery.return_value.version_code = 999
        with self.assertRaisesRegex(StoreError, 'requested 123, returned 999') as caught:
            android_store.acquire_play('com.sonos.acr2', self.root/'delivery', self.root/'auth', version_code='123')
        self.assertEqual(caught.exception.observed_version_code, '999')
        self.build.assert_not_called()
        self.download.assert_not_called()

    def test_signed_urls_and_tokens_do_not_escape_in_errors_but_retry_signals_do(self):
        error = RuntimeError('https://signed.invalid/payload?token=DO-NOT-RECORD')
        error.status_code, error.retry_after = 429, 90
        self.client.delivery.side_effect = error
        with self.assertRaises(StoreError) as caught:
            android_store.acquire_play('com.sonos.acr2', self.root/'delivery', self.root/'auth', version_code='123')
        self.assertEqual(str(caught.exception), 'RuntimeError')
        self.assertEqual(caught.exception.retry_after, 90)
        self.assertEqual(caught.exception.status_code, 429)
        self.download.assert_not_called()

    def test_profile_enumeration_is_not_capped_and_interleaves_architectures(self):
        names = [(str(n), {}) for n in range(12)]
        with patch.dict(sys.modules, {'goopdl.profiles': types.SimpleNamespace(get_priority_profiles=lambda arch: names)}):
            profiles = android_store.play_profiles()
        self.assertEqual(len(profiles), 60)
        self.assertEqual(len({p['arch'] for p in profiles[:5]}), 5)
        self.assertEqual({p['profile'] for p in profiles}, {str(n) for n in range(12)})

    def test_cdn_429_status_is_preserved_when_upstream_retains_only_an_error_label(self):
        error = android_store.safe_error(RuntimeError('base.apk: HTTP 429'))
        self.assertEqual(error.status_code, 429)
        self.assertEqual(str(error), 'RuntimeError')

    def test_apkpure_throttle_is_detected_even_when_apkeep_exits_zero(self):
        with patch.object(android_store.subprocess, 'run', return_value=subprocess.CompletedProcess(
                ['apkeep'], 0, 'HTTP 429 Too Many Requests; Retry-After: 81')):
            with self.assertRaises(StoreError) as caught:
                android_store.run_apkeep(['apkeep'])
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.retry_after, 81)


class Clock:
    def __init__(self):
        self.current = datetime(2030, 1, 1, tzinfo=timezone.utc)
        self.elapsed = 0
        self.sleeps = []

    def now(self, tz=None):
        return self.current

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.current += timedelta(seconds=seconds)
        self.elapsed += seconds


class PlaySweepTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger = self.root/'state.json'
        self.receipt = self.root/'receipt.json'
        self.clock = Clock()
        self.profiles = [{'arch': 'arm64', 'profile': 'one'}, {'arch': 'armv7', 'profile': 'two'}]
        for mocked in (
            patch.object(SWEEP, 'play_profiles', return_value=self.profiles),
            patch.object(SWEEP, 'datetime', types.SimpleNamespace(now=self.clock.now, fromisoformat=datetime.fromisoformat)),
            patch.object(SWEEP.time, 'monotonic', side_effect=lambda: self.clock.elapsed),
            patch.object(SWEEP.time, 'sleep', side_effect=self.clock.sleep),
            patch.object(retry, '_now', side_effect=lambda now=None: now or self.clock.current),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.argv = ['sweep', '--source', 'google-play-direct', '--package', 'com.sonos.acr2',
                     '--repo', 'fresh/archive', '--ledger', str(self.ledger), '--receipt', str(self.receipt),
                     '--limit', '1', '--discovery-limit', '1', '--seconds', '100']

    def run_sweep(self, acquire, discover=None):
        with patch.object(sys, 'argv', self.argv), \
             patch.object(SWEEP, 'play_version', side_effect=discover or (lambda *args:
                 {'version_code': '123', 'version_name': '1.2.3'})), \
             patch.object(SWEEP, 'run_acquisition', side_effect=acquire):
            return SWEEP.main()

    def test_empty_destination_discovers_and_attempts_exact_code_without_any_existing_releases(self):
        calls = []
        def acquire(command, **kwargs):
            state = json.loads(self.ledger.read_text())
            self.assertEqual(state['pending_selectors'][0]['version_selector'], '123')
            calls.append(command)
            return types.SimpleNamespace(returncode=0, stdout='verified publication')
        self.run_sweep(acquire)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][calls[0].index('--version-code')+1], '123')
        self.assertIn('fresh/archive', calls[0])
        self.assertEqual(json.loads(self.ledger.read_text())['discovery_pending_total'], 1)

    def test_429_waits_then_retries_same_delivery_instead_of_discarding_it(self):
        calls = []
        def acquire(command, **kwargs):
            calls.append((self.clock.elapsed, command[command.index('--play-profile')+1]))
            result = Path(command[command.index('--result')+1])
            if len(calls) == 1:
                result.write_text(json.dumps({'errors': [{'error': 'RateLimitedError', 'status_code': 429, 'retry_after': 3}]}))
                return types.SimpleNamespace(returncode=1, stdout='throttled')
            return types.SimpleNamespace(returncode=0, stdout='verified')
        self.run_sweep(acquire)
        self.assertEqual(calls, [(0, 'one'), (3, 'one')])
        self.assertEqual(self.clock.sleeps, [3])
        state = json.loads(self.ledger.read_text())
        self.assertEqual(state['attempts'][0]['status'], 'archived')
        self.assertEqual(state['pending_total'], 0)

    def test_long_cooldown_survives_worker_exit_and_blocks_other_profile_requests(self):
        def throttled(*args):
            raise StoreError('RateLimitedError', status_code=429, retry_after=900)
        acquire = Mock(side_effect=AssertionError('source cooling down'))
        self.assertEqual(self.run_sweep(acquire, throttled), 1)
        state = json.loads(self.ledger.read_text())
        self.assertEqual(state['next_request_at'], (self.clock.current+timedelta(seconds=900)).isoformat())
        later_listing = Mock(side_effect=AssertionError('must respect saved source cooldown'))
        self.assertEqual(self.run_sweep(acquire, later_listing), 1)
        later_listing.assert_not_called()
        acquire.assert_not_called()
        self.assertEqual(self.clock.sleeps, [])

    def test_different_offered_code_is_retained_as_a_lead_without_fulfilling_original_request(self):
        def mismatch(command, **kwargs):
            Path(command[command.index('--result')+1]).write_text(json.dumps({'errors': [{
                'error': 'GooglePlayVersionMismatch', 'observed_version_code': '999'}]}))
            return types.SimpleNamespace(returncode=1, stdout='mismatch')
        self.run_sweep(mismatch)
        state = json.loads(self.ledger.read_text())
        self.assertEqual({row['version_selector'] for row in state['pending_selectors']}, {'123', '999'})
        self.assertEqual(state['attempts'][0]['status'], 'failed')

    def test_invalid_retry_after_cannot_break_checkpoint_and_long_valid_delay_is_respected(self):
        for value in ('invalid', float('inf'), float('nan'), -1, 1e200):
            with self.subTest(value=value):
                state = {}
                self.assertTrue(SWEEP.cooldown(state, {'status_code': 429, 'retry_after': value}))
                self.assertEqual(state['next_request_at'], (self.clock.current+timedelta(seconds=60)).isoformat())
        state = {}
        SWEEP.cooldown(state, {'status_code': 429, 'retry_after': 3*86400})
        self.assertEqual(state['next_request_at'], (self.clock.current+timedelta(days=3)).isoformat())

    def test_timed_out_attempt_is_checkpointed_and_kept_pending(self):
        def timeout(command, **kwargs):
            self.assertLessEqual(kwargs['timeout'], 100)
            self.clock.elapsed += 100
            return types.SimpleNamespace(returncode=124, stdout='StoreAcquisitionTimedOut')
        self.assertEqual(self.run_sweep(timeout), 1)
        state = json.loads(self.ledger.read_text())
        record, = state['attempts']
        self.assertEqual(record['exit_code'], 124)
        self.assertEqual(record['status'], 'failed')
        self.assertEqual(record['next_attempt_at'], (self.clock.current+timedelta(hours=1)).isoformat())
        self.assertEqual(state['pending_total'], 1)

    def test_unattempted_profiles_and_historical_code_hints_survive_listing_outage(self):
        self.receipt.write_text(json.dumps({'artifacts': [{'package': 'com.sonos.acr2', 'version_code': '7'}]}))
        acquired = []
        def acquire(command, **kwargs):
            acquired.append((command[command.index('--version-code')+1], command[command.index('--play-profile')+1]))
            return types.SimpleNamespace(returncode=1, stdout='unavailable')
        self.run_sweep(acquire)
        def down(*args):
            raise StoreError('NetworkError')
        self.run_sweep(acquire, down)
        self.assertEqual(acquired, [('123', 'one'), ('7', 'one')])
        pending = json.loads(self.ledger.read_text())['pending_selectors']
        self.assertIn(('7', 'two'), [(p['version_selector'], p['profile']) for p in pending])


class ProcessBudgetTests(unittest.TestCase):
    def test_timeout_stops_nested_acquisition_processes(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = f"import time; from pathlib import Path; time.sleep(3); Path({str(root/'escaped')!r}).touch()"
            parent = (f"import subprocess, sys, time; from pathlib import Path; "
                      f"subprocess.Popen([sys.executable, '-c', {child!r}]); "
                      f"Path({str(root/'ready')!r}).touch(); time.sleep(30)")
            result = android_store.run_acquisition([sys.executable, '-c', parent], cwd=root, timeout=2)
            self.assertEqual(result.returncode, 124)
            self.assertTrue((root/'ready').exists())
            time.sleep(1.5)
            self.assertFalse((root/'escaped').exists())


@unittest.skipUnless(importlib.util.find_spec('goopdl') is not None, 'install the google-play extra for SDK contract tests')
class PinnedGoopdlContractTests(unittest.TestCase):
    def test_dispenser_refusal_is_distinguished_from_a_play_api_refusal(self):
        from goopdl.api import PlayAPIError, RateLimitedError
        from goopdl.auth import DispenserUnavailableError, DispenserRateLimitedError
        error = PlayAPIError('sensitive upstream detail must not enter receipts', status_code=403)
        error.__cause__ = DispenserUnavailableError(403)
        sanitized = android_store.safe_error(error)
        self.assertEqual(str(sanitized), 'GooglePlayTokenDispenserUnavailable')
        self.assertEqual(sanitized.status_code, 403)
        play_error = android_store.safe_error(PlayAPIError('another private detail', status_code=403))
        self.assertEqual(str(play_error), 'PlayAPIError')
        self.assertEqual(play_error.status_code, 403)
        limited = RateLimitedError('private throttle detail', retry_after=75)
        limited.__cause__ = DispenserRateLimitedError(75)
        sanitized = android_store.safe_error(limited)
        self.assertEqual(str(sanitized), 'GooglePlayTokenDispenserRateLimited')
        self.assertEqual(sanitized.status_code, 429)
        self.assertEqual(sanitized.retry_after, 75)

    def test_pinned_sdk_delivers_all_advertised_part_types_through_our_adapter(self):
        from goopdl.api import DeliveryResult, SplitInfo, DexMetadata, AdditionalFile
        dist = importlib.metadata.distribution('goopdl')
        source = json.loads(dist.read_text('direct_url.json'))
        self.assertEqual(source['vcs_info']['commit_id'], android_store.GOOPDL_REVISION)
        delivery = DeliveryResult(version_code=42, download_url='https://fixture.invalid/base',
            splits=[SplitInfo(name='arm64', url='https://fixture.invalid/split')],
            dex_metadata=DexMetadata(url='https://fixture.invalid/dex'), additional_files=[
                AdditionalFile(file_type=0, version_code=42, url='https://fixture.invalid/obb'),
                AdditionalFile(file_type=2, version_code=42, url='https://fixture.invalid/pack'),
                AdditionalFile(file_type=2, version_code=42, url='https://fixture.invalid/other-pack')])
        client = Mock()
        client.delivery.return_value = delivery
        def download(specs):
            for spec in specs:
                spec.dest.write_bytes(b'synthetic delivery part')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(android_store, 'play_client', return_value=client), \
                 patch('goopdl.download.download_batch', side_effect=download):
                output, code = android_store.acquire_play('com.sonos.acr2', root/'delivery', root/'auth', version_code=42)
            self.assertEqual(code, '42')
            self.assertTrue((output/'base.apk').exists())
            self.assertEqual(len(list(output.iterdir())), 6)


if __name__ == '__main__':
    unittest.main()
