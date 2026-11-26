"""Anonymous delivery identity, profile coverage and durable throttle recovery."""
import importlib.util
import importlib.metadata
import json
import sys
import tempfile
import types
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from sonos_firmware import android_store, retry
from sonos_firmware.android_store import StoreError

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('play_sweep', ROOT/'scripts/apps/sweep_android_store_history.py')
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
            patch.object(SWEEP, 'winding_down', side_effect=lambda: self.clock.elapsed >= 100),
            patch.object(SWEEP, 'remaining', side_effect=lambda: max(0, 100-self.clock.elapsed)),
            patch.object(retry, '_now', side_effect=lambda now=None: now or self.clock.current),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)
        self.argv = ['sweep', '--source', 'google-play-direct', '--package', 'com.sonos.acr2',
                     '--repo', 'fresh/archive', '--ledger', str(self.ledger), '--receipt', str(self.receipt)]

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
        self.assertEqual(len(calls), 2)
        self.assertTrue(all(command[command.index('--version-code')+1] == '123' for command in calls))
        self.assertTrue(all('fresh/archive' in command for command in calls))
        self.assertEqual(json.loads(self.ledger.read_text())['discovery_pending_total'], 0)

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
        self.assertEqual(calls, [(0, 'one'), (3, 'one'), (3, 'two')])
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


@unittest.skipUnless(importlib.util.find_spec('goopdl') is not None, 'install the google-play extra for SDK contract tests')
class PinnedGoopdlContractTests(unittest.TestCase):


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
