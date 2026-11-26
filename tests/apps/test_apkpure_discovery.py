"""Metadata-first APKPure discovery retains ABI variants and retry signals."""
import importlib.util
import hashlib
import json
import tempfile
import types
import sys
import unittest
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path
from unittest.mock import Mock, patch

from sonos_firmware import apkpure
from sonos_firmware.android_store import StoreError

ROOT = Path(__file__).resolve().parents[2]
PACKAGE = 'com.sonos.acr2'
SPEC = importlib.util.spec_from_file_location('apkpure_sweep', ROOT/'scripts/apps/sweep_android_store_history.py')
SWEEP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SWEEP)


def row(arch='arm64-v8a', **changes):
    value = {'package_name': PACKAGE, 'version_name': '1.2.3-release+build', 'version_code': '123',
             'version_id': 'one-version', 'native_code': [arch],
             'asset': {'url': 'https://fixture.invalid/delivery?arch='+arch, 'type': 'XAPK'}}
    value.update(changes)
    return value


class APKPureMetadataTests(unittest.TestCase):
    def test_rotating_winudf_tickets_do_not_create_new_asset_variants(self):
        def ticket(token, key, arch='arm64-v8a'):
            return row(arch, asset={'url': 'https://data.winudf.com/APK/fixture?package_name='+PACKAGE+
                       '&token='+token+'&k='+key+'&filename=Sonos.apk', 'type': 'APK'})
        parsed = apkpure.parse_versions({'retcode': 0, 'version_list': [ticket('old', 'one'),
            ticket('new', 'two'), ticket('other', 'three', 'armeabi-v7a')]}, PACKAGE)
        self.assertEqual(len(parsed['variants']), 2)
        arm = next(v for v in parsed['variants'] if v['arch'] == 'arm64-v8a')
        self.assertIn('token=new', arm['asset_url'])
        fresh, = apkpure.parse_versions({'retcode': 0, 'version_list': [ticket('later', 'four')]}, PACKAGE)['variants']
        self.assertEqual(arm['variant'], fresh['variant'])

    def test_content_selectors_and_unknown_host_parameters_remain_distinct(self):
        for host, query in [('data.winudf.com', 'content'), ('new-cdn.example', 'token')]:
            rows = [row(asset={'url': f'https://{host}/APK/fixture?{query}={value}', 'type': 'APK'})
                    for value in ('one', 'two')]
            parsed = apkpure.parse_versions({'retcode': 0, 'version_list': rows}, PACKAGE)
            self.assertEqual(len(parsed['variants']), 2)


    def test_same_version_code_retains_every_architecture_and_distinct_asset(self):
        first = row()
        second = row('armeabi-v7a')
        third = row(asset={'url': 'https://fixture.invalid/alternative', 'type': 'APK'})
        result = apkpure.parse_versions({'retcode': 0, 'version_list': [first, second, third, first]}, PACKAGE)
        self.assertEqual(result['errors'], [])
        self.assertEqual(len(result['variants']), 3)
        self.assertEqual({v['version_code'] for v in result['variants']}, {'123'})
        self.assertEqual(len({v['variant'] for v in result['variants']}), 3)
        self.assertEqual({v['arch'] for v in result['variants']}, {'arm64-v8a', 'armeabi-v7a'})


    def test_protocol_fetch_caches_original_metadata_without_downloading_assets(self):
        document = {'retcode': 0, 'version_list': [row()], 'unfamiliar_field': {'preserve': True}}
        response = Mock(status_code=200, headers={})
        response.json.return_value = document
        with tempfile.TemporaryDirectory() as temporary, patch.object(apkpure, 'Session') as session:
            client = session.return_value.__enter__.return_value
            client.get.return_value = response
            result = apkpure.discover_versions(PACKAGE, observations=Path(temporary), timeout=9)
            self.assertEqual(json.loads((Path(temporary)/(PACKAGE+'--sdk-29.json')).read_text()), document)
        self.assertEqual(len(result['variants']), 1)
        self.assertEqual(client.get.call_count, 1)
        self.assertTrue(client.get.call_args.args[0].startswith(apkpure.API+'?'))
        self.assertEqual(client.get.call_args.kwargs['headers'], apkpure.HEADERS)
        self.assertEqual(client.get.call_args.kwargs['timeout'], 9)
        response.iter_content.assert_not_called()

    def test_429_retains_numeric_and_http_date_retry_after(self):
        for header in ('81', format_datetime(datetime.now(timezone.utc)+timedelta(seconds=81))):
            with self.subTest(header=header), patch.object(apkpure, 'Session') as session:
                session.return_value.__enter__.return_value.get.return_value = Mock(
                    status_code=429, headers={'Retry-After': header})
                with self.assertRaises(StoreError) as caught:
                    apkpure.discover_versions(PACKAGE)
                self.assertEqual(caught.exception.status_code, 429)
                self.assertGreater(caught.exception.retry_after, 78)
                self.assertLessEqual(caught.exception.retry_after, 81)


    def test_unwritable_cache_reports_partial_work_without_discarding_valid_variants(self):
        response = Mock(status_code=200, headers={})
        response.json.return_value = {'retcode': 0, 'version_list': [row()]}
        with patch.object(apkpure, 'Session') as session, patch.object(apkpure, 'atomic_json', side_effect=PermissionError):
            session.return_value.__enter__.return_value.get.return_value = response
            result = apkpure.discover_versions(PACKAGE, observations=Path('/unused'))
        self.assertEqual(len(result['variants']), 1)
        self.assertEqual(result['errors'], [{'source': 'observation-cache', 'error': 'PermissionError'}])


    def test_throttled_download_never_writes_a_delivery_and_closes_connection(self):
        response = Mock(status_code=429, headers={'Retry-After': '7'})
        with tempfile.TemporaryDirectory() as temporary, patch.object(apkpure, 'Session') as session:
            session.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaises(StoreError) as caught:
                apkpure.acquire_variant('https://fixture.invalid/variant', 'XAPK', Path(temporary))
            self.assertEqual(list(Path(temporary).iterdir()), [])
        self.assertEqual(caught.exception.retry_after, 7)
        response.iter_content.assert_not_called()
        response.close.assert_called_once()


class APKPureSweepFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.ledger, self.receipt = self.root/'state.json', self.root/'receipt.json'
        self.argv = ['sweep', '--package', PACKAGE, '--ledger', str(self.ledger),
                     '--receipt', str(self.receipt), '--repo', 'fresh/archive']
        self.parsed = apkpure.parse_versions({'retcode': 0, 'version_list': [row(), row('armeabi-v7a')]}, PACKAGE)
        mocked = patch.object(SWEEP, 'discover_sdk_levels', return_value={'levels': [29], 'errors': []})
        mocked.start()
        self.addCleanup(mocked.stop)
        # Model a workflow with 100 seconds until cooperative wind-down.
        cutoff = patch.object(SWEEP, 'remaining', return_value=100)
        cutoff.start()
        self.addCleanup(cutoff.stop)

    def run_sweep(self, metadata=None, acquire=None):
        with patch.object(sys, 'argv', self.argv), \
             patch.object(SWEEP, 'discover_apkpure', side_effect=metadata or (lambda *args, **kwargs: self.parsed)), \
             patch.object(SWEEP, 'discover_versions', return_value=['older-selector']), \
             patch.object(SWEEP, 'run_acquisition', side_effect=acquire or (lambda *args, **kwargs:
                 types.SimpleNamespace(returncode=1, stdout='fixture failure'))):
            return SWEEP.main()

class APKPureSweepTests(APKPureSweepFixture, unittest.TestCase):
    def test_old_ticket_observations_merge_without_redownloading_a_verified_asset(self):
        old = apkpure.parse_versions({'retcode': 0, 'version_list': [row(asset={
            'url': 'https://data.winudf.com/APK/fixture?token=old&k=old', 'type': 'APK'})]}, PACKAGE)['variants'][0]
        old.pop('version_id')
        old.pop('asset_identity')
        old['variant'] = 'one-version-'+hashlib.sha256(old['asset_url'].encode()).hexdigest()
        other = {**old, 'asset_url': old['asset_url'].replace('old', 'other'), 'sdk_context': 36}
        other['variant'] = 'one-version-'+hashlib.sha256(other['asset_url'].encode()).hexdigest()
        keys = [PACKAGE+':'+lead['variant'] for lead in (old, other)]
        self.ledger.write_text(json.dumps({'repository': 'fresh/archive',
            'observed_variants': dict(zip(keys, [old, other], strict=True)), 'pending_selectors': [old, other],
            'discovery': {PACKAGE+'|||apkpure-api|sdk=29': {'variants': keys}},
            'attempts': [{**old, 'status': 'archived'}]}))
        self.receipt.write_text(json.dumps({'artifacts': [{'package': PACKAGE,
            'source_kind': 'apkpure-recovery', 'source_version_selector': old['version_selector'],
            'delivery_profiles': [{'arch': old['arch'], 'variant': old['variant']}],
            'release_url': 'https://github.com/fresh/archive/releases/download/tag/verified.apk'}]}))
        self.parsed = apkpure.parse_versions({'retcode': 0, 'version_list': [row(asset={
            'url': 'https://data.winudf.com/APK/fixture?token=fresh&k=fresh', 'type': 'APK'})]}, PACKAGE)
        acquired = []
        self.run_sweep(acquire=lambda command, **kwargs: acquired.append(command) or
                       types.SimpleNamespace(returncode=1, stdout='unrelated selector unavailable'))
        state = json.loads(self.ledger.read_text())
        observation, = state['observed_variants'].values()
        self.assertEqual(observation['sdk_contexts'], [29, 36])
        self.assertFalse(any(v.get('variant') for v in state['pending_selectors']))
        self.assertTrue(all('--asset-url' not in command for command in acquired))
        references = state['discovery'][PACKAGE+'|||apkpure-api|sdk=29']['variants']
        self.assertEqual(references, [PACKAGE+':'+observation['variant']])

    def test_variants_are_checkpointed_before_acquisition_and_remain_distinct_after_outage(self):
        acquired = []
        def acquire(command, **kwargs):
            state = json.loads(self.ledger.read_text())
            variants = [v for v in state['pending_selectors'] if v.get('variant')]
            self.assertEqual(len(variants), 2)
            if '--variant-arch' in command:
                acquired.append(command[command.index('--variant-arch')+1])
                self.assertEqual(command[command.index('--version-code')+1], '123')
            return types.SimpleNamespace(returncode=1, stdout='fixture unavailable')
        self.assertEqual(self.run_sweep(acquire=acquire), 1)
        def outage(*args, **kwargs):
            raise StoreError('MetadataUnavailable')
        self.assertEqual(self.run_sweep(metadata=outage, acquire=acquire), 1)
        self.assertEqual(acquired, ['arm64-v8a', 'armeabi-v7a'])
        self.assertEqual(len(json.loads(self.ledger.read_text())['pending_selectors']), 3)


if __name__ == '__main__':
    unittest.main()
