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

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = 'com.sonos.acr2'
SPEC = importlib.util.spec_from_file_location('apkpure_sweep', ROOT/'scripts/sweep_android_store_history.py')
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

    def test_non_utf8_content_parameters_keep_their_exact_asset_identity(self):
        rows = [row(asset={'url': 'https://data.winudf.com/APK/fixture?content='+value+'&token=rotate',
                           'type': 'APK'}) for value in ('%FF', '%FE')]
        result = apkpure.parse_versions({'retcode': 0, 'version_list': rows}, PACKAGE)
        self.assertEqual(len(result['variants']), 2)
        self.assertEqual({v['asset_identity'] for v in result['variants']},
            {'https://data.winudf.com/APK/fixture?content=%FF', 'https://data.winudf.com/APK/fixture?content=%FE'})

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

    def test_malformed_row_does_not_discard_independent_valid_variants(self):
        document = {'retcode': 0, 'version_list': [row(), None, row(package_name='other.package'),
                    row(version_code='not-a-code'), row(native_code='not-a-list'),
                    row(asset={'url': 'file:///private/file', 'type': 'APK'})]}
        result = apkpure.parse_versions(document, PACKAGE)
        self.assertEqual(len(result['variants']), 1)
        self.assertEqual(len(result['errors']), 5)

    def test_unknown_asset_type_is_retained_for_later_inspection_support(self):
        result = apkpure.parse_versions({'retcode': 0, 'version_list': [row(
            asset={'url': 'https://fixture.invalid/asset', 'type': 'new-format'})]}, PACKAGE)
        variant, = result['variants']
        self.assertEqual(variant['asset_type'], 'NEW-FORMAT')
        with patch.object(apkpure, 'Session') as session:
            with self.assertRaisesRegex(StoreError, 'UnsupportedAPKPureAssetType'):
                apkpure.acquire_variant(variant['asset_url'], variant['asset_type'], Path('/unused'))
        session.assert_not_called()

    def test_failed_or_malformed_listing_is_not_a_successful_discovery(self):
        for document in ({}, {'retcode': 1, 'version_list': [row()]},
                         {'retcode': 0, 'version_list': 'invalid'}):
            with self.subTest(document=document), self.assertRaises(StoreError):
                apkpure.parse_versions(document, PACKAGE)

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

    def test_unknown_schema_is_cached_before_parsing_for_later_reinterpretation(self):
        document = {'new-version-schema': [{'code': 123}]}
        response = Mock(status_code=200, headers={})
        response.json.return_value = document
        with tempfile.TemporaryDirectory() as temporary, patch.object(apkpure, 'Session') as session:
            session.return_value.__enter__.return_value.get.return_value = response
            with self.assertRaises(StoreError):
                apkpure.discover_versions(PACKAGE, observations=Path(temporary))
            self.assertEqual(json.loads((Path(temporary)/(PACKAGE+'--sdk-29.json')).read_text()), document)

    def test_unwritable_cache_reports_partial_work_without_discarding_valid_variants(self):
        response = Mock(status_code=200, headers={})
        response.json.return_value = {'retcode': 0, 'version_list': [row()]}
        with patch.object(apkpure, 'Session') as session, patch.object(apkpure, 'atomic_json', side_effect=PermissionError):
            session.return_value.__enter__.return_value.get.return_value = response
            result = apkpure.discover_versions(PACKAGE, observations=Path('/unused'))
        self.assertEqual(len(result['variants']), 1)
        self.assertEqual(result['errors'], [{'source': 'observation-cache', 'error': 'PermissionError'}])

    def test_asset_acquisition_streams_only_the_advertised_variant(self):
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = iter([b'synthetic ', b'APK'])
        with tempfile.TemporaryDirectory() as temporary, patch.object(apkpure, 'Session') as session:
            client = session.return_value.__enter__.return_value
            client.get.return_value = response
            path = apkpure.acquire_variant('https://fixture.invalid/variant', 'APK', Path(temporary))
            self.assertEqual(path.read_bytes(), b'synthetic APK')
            self.assertEqual(path.suffix, '.apk')
        self.assertEqual(client.get.call_args.args[0], 'https://fixture.invalid/variant')
        self.assertTrue(client.get.call_args.kwargs['stream'])
        response.close.assert_called_once()

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
        self.argv = ['sweep', '--package', PACKAGE, '--limit', '1', '--ledger', str(self.ledger),
                     '--receipt', str(self.receipt), '--repo', 'fresh/archive']
        self.parsed = apkpure.parse_versions({'retcode': 0, 'version_list': [row(), row('armeabi-v7a')]}, PACKAGE)
        mocked = patch.object(SWEEP, 'discover_sdk_levels', return_value={'levels': [29], 'errors': []})
        mocked.start()
        self.addCleanup(mocked.stop)

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
            'observed_variants': dict(zip(keys, [old, other])), 'pending_selectors': [old, other],
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
            acquired.append(command[command.index('--variant-arch')+1])
            self.assertEqual(command[command.index('--version-code')+1], '123')
            return types.SimpleNamespace(returncode=1, stdout='fixture unavailable')
        self.assertEqual(self.run_sweep(acquire=acquire), 1)
        def outage(*args, **kwargs):
            raise StoreError('MetadataUnavailable')
        self.assertEqual(self.run_sweep(metadata=outage, acquire=acquire), 1)
        self.assertEqual(acquired, ['arm64-v8a', 'armeabi-v7a'])
        self.assertEqual(len(json.loads(self.ledger.read_text())['pending_selectors']), 3)

    def test_metadata_only_variants_supply_google_hints_without_any_receipts(self):
        self.run_sweep()
        self.assertFalse(self.receipt.exists())
        self.argv = ['sweep', '--source', 'google-play-direct', '--package', PACKAGE, '--limit', '1',
            '--ledger', str(self.root/'play.json'), '--receipt', str(self.receipt), '--metadata-hints', str(self.ledger)]
        with patch.object(SWEEP, 'play_profiles', return_value=[{'arch': 'arm64', 'profile': 'device'}]), \
             patch.object(SWEEP, 'play_version', side_effect=StoreError('ListingUnavailable')):
            self.run_sweep()
        pending = json.loads((self.root/'play.json').read_text())['pending_selectors']
        self.assertEqual({v['version_selector'] for v in pending}, {'123'})
        self.assertFalse(self.receipt.exists())

    def test_cached_metadata_is_reinterpreted_when_live_source_is_unavailable(self):
        observations = self.root/'observations'
        observations.mkdir()
        (observations/(PACKAGE+'.json')).write_text(json.dumps({'retcode': 0, 'version_list': [row()]}))
        self.argv += ['--observations', str(observations)]
        self.run_sweep(metadata=Mock(side_effect=StoreError('Offline')))
        pending = json.loads(self.ledger.read_text())['pending_selectors']
        self.assertTrue(any(v.get('asset_url') for v in pending))

    def test_bad_variant_keeps_valid_work_and_reports_partial_discovery(self):
        self.parsed = apkpure.parse_versions({'retcode': 0, 'version_list': [row(), None]}, PACKAGE)
        self.run_sweep()
        state = json.loads(self.ledger.read_text())
        self.assertEqual(state['status'], 'partial')
        self.assertTrue(any(v.get('variant') for v in state['pending_selectors']))
        self.assertTrue(any(v.get('error') == 'APKPureDiscoveryIncomplete' for v in state['discovery'].values()))

    def test_unreadable_optional_cache_does_not_block_live_metadata_or_acquisition(self):
        observations = self.root/'observations'
        observations.mkdir()
        path = observations/(PACKAGE+'.json')
        path.write_text('{}')
        original = Path.read_text
        def read_text(target, *args, **kwargs):
            if target == path:
                raise PermissionError('fixture cache permission failure')
            return original(target, *args, **kwargs)
        self.argv += ['--observations', str(observations)]
        with patch.object(Path, 'read_text', read_text):
            self.assertEqual(self.run_sweep(), 1)
        state = json.loads(self.ledger.read_text())
        self.assertEqual(len(state['attempts']), 1)
        self.assertTrue(state['attempts'][0].get('asset_url'))

    def test_unavailable_apkeep_does_not_block_structured_asset_acquisition(self):
        acquired = []
        with patch.object(sys, 'argv', self.argv), \
             patch.object(SWEEP, 'discover_apkpure', return_value=self.parsed), \
             patch.object(SWEEP, 'discover_versions', side_effect=FileNotFoundError('apkeep')), \
             patch.object(SWEEP, 'run_acquisition', side_effect=lambda command, **kwargs:
                 (acquired.append(command) or types.SimpleNamespace(returncode=1, stdout='fixture failure'))):
            self.assertEqual(SWEEP.main(), 1)
        self.assertEqual(len(acquired), 1)
        self.assertIn('--asset-url', acquired[0])
        state = json.loads(self.ledger.read_text())
        self.assertEqual(state['status'], 'partial')
        self.assertEqual(state['discovery_errors'][0]['error'], 'FileNotFoundError')



class SDKMetadataTests(unittest.TestCase):
    def test_sdk_index_discovers_numeric_levels_including_future_previews_without_version_list(self):
        xml = '''<repository xmlns:r="urn:fixture">
          <remotePackage path="platforms;android-21"><r:details><api-level>21</api-level></r:details></remotePackage>
          <remotePackage path="platforms;android-36-ext1"><r:details><api-level>36</api-level></r:details></remotePackage>
          <remotePackage path="platforms;android-preview"><r:details><api-level>99</api-level></r:details></remotePackage>
          <remotePackage path="system-images;unused"><api-level>999</api-level></remotePackage>
        </repository>'''
        self.assertEqual(apkpure.parse_sdk_levels(xml), [21, 36, 99])

    def test_requested_sdk_controls_headers_and_keeps_observation_caches_separate(self):
        response = Mock(status_code=200, headers={})
        response.json.return_value = {'retcode': 0, 'version_list': [row(sdk_version='21', target_sdk_version='36')]}
        with tempfile.TemporaryDirectory() as temporary, patch.object(apkpure, 'Session') as session:
            client = session.return_value.__enter__.return_value
            client.get.return_value = response
            result = apkpure.discover_versions(PACKAGE, sdk=36, observations=Path(temporary))
            self.assertTrue((Path(temporary)/(PACKAGE+'--sdk-36.json')).exists())
            self.assertFalse((Path(temporary)/(PACKAGE+'--sdk-29.json')).exists())
        header = json.loads(client.get.call_args.kwargs['headers']['Ual-Access-ProjectA'])
        self.assertEqual(header['device_info']['os_ver'], '36')
        self.assertEqual(result['sdk_hints'], [21, 36])
        self.assertEqual(result['variants'][0]['sdk_context'], 36)

    def test_successful_empty_profile_is_an_observation_not_an_app_wide_failure(self):
        result = apkpure.parse_versions({'retcode': 0, 'version_list': []}, PACKAGE, sdk=2)
        self.assertEqual(result, {'variants': [], 'errors': [], 'sdk_hints': []})


class SDKContextSweepTests(APKPureSweepFixture, unittest.TestCase):
    def test_rotating_tickets_share_one_observation_across_sdk_contexts(self):
        def metadata(package, **kwargs):
            sdk = kwargs['sdk']
            return apkpure.parse_versions({'retcode': 0, 'version_list': [row(asset={
                'url': f'https://data.winudf.com/APK/fixture?token=ticket{sdk}&k=key{sdk}', 'type': 'APK'})]},
                package, sdk=sdk)
        with patch.object(SWEEP, 'discover_sdk_levels', return_value={'levels': [21, 29, 36], 'errors': []}):
            self.run_sweep(metadata=metadata)
        state = json.loads(self.ledger.read_text())
        observation, = state['observed_variants'].values()
        self.assertEqual(observation['sdk_contexts'], [21, 29, 36])
        self.assertEqual(len([v for v in state['pending_selectors'] if v.get('variant')]), 1)

    def test_new_and_old_sdk_contexts_expose_independent_version_leads(self):
        def metadata(package, **kwargs):
            sdk = kwargs['sdk']
            code = {29: '123', 36: '124', 21: '7'}[sdk]
            return apkpure.parse_versions({'retcode': 0, 'version_list': [row(
                version_code=code, version_name='version-'+code,
                asset={'url': 'https://fixture.invalid/'+code, 'type': 'APK'})]}, package, sdk=sdk)
        with patch.object(SWEEP, 'discover_sdk_levels', return_value={'levels': [21, 29, 36], 'errors': []}):
            self.run_sweep(metadata=metadata)
        state = json.loads(self.ledger.read_text())
        self.assertEqual({v['version_code'] for v in state['observed_variants'].values()}, {'7', '123', '124'})
        self.assertEqual(state['discovery_pending_total'], 0)

    def test_many_contexts_share_one_asset_observation_and_retain_every_context(self):
        def metadata(package, **kwargs):
            return apkpure.parse_versions({'retcode': 0, 'version_list': [row()]}, package, sdk=kwargs['sdk'])
        with patch.object(SWEEP, 'discover_sdk_levels', return_value={'levels': [21, 29, 36, 99], 'errors': []}):
            self.run_sweep(metadata=metadata)
        state = json.loads(self.ledger.read_text())
        observation, = state['observed_variants'].values()
        self.assertEqual(observation['sdk_contexts'], [21, 29, 36, 99])
        records = [record for key, record in state['discovery'].items() if 'apkpure-api' in key]
        self.assertEqual(len(records), 4)
        self.assertTrue(all(len(record['variants']) == 1 and 'versions' not in record for record in records))

    def test_index_429_does_not_block_independent_apkpure_discovery(self):
        with patch.object(SWEEP, 'discover_sdk_levels', side_effect=StoreError(
                'IndexRateLimited', status_code=429, retry_after=900)):
            self.run_sweep()
        state = json.loads(self.ledger.read_text())
        self.assertEqual(state['sdk_index']['error'], 'IndexRateLimited')
        self.assertNotIn('next_request_at', state)
        self.assertEqual(len(state['observed_variants']), 2)
        self.assertEqual(len(state['attempts']), 1)

    def test_sdk_hints_outside_the_index_are_pending_work(self):
        self.parsed = apkpure.parse_versions({'retcode': 0, 'version_list': [row(target_sdk_version='99')]}, PACKAGE)
        self.run_sweep()
        state = json.loads(self.ledger.read_text())
        self.assertEqual(state['sdk_hints'], [99])
        self.assertEqual(state['discovery_pending_total'], 1)

    def test_empty_contexts_are_refreshed_periodically_without_enqueueing_fake_deliveries(self):
        def metadata(package, **kwargs):
            rows = [] if kwargs['sdk'] == 2 else [row()]
            return apkpure.parse_versions({'retcode': 0, 'version_list': rows}, package, sdk=kwargs['sdk'])
        with patch.object(SWEEP, 'discover_sdk_levels', return_value={'levels': [2, 29], 'errors': []}):
            self.run_sweep(metadata=metadata)
        state = json.loads(self.ledger.read_text())
        record = state['discovery'][PACKAGE+'|||apkpure-api|sdk=2']
        self.assertEqual(record['compatible_variant_count'], 0)
        self.assertNotIn('error', record)
        self.assertGreater(datetime.fromisoformat(record['next_attempt_at']), datetime.now(timezone.utc)+timedelta(days=6))
        self.assertEqual(len(state['observed_variants']), 1)


if __name__ == '__main__':
    unittest.main()
