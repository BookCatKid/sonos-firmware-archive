"""Android delivery sets cross the same verified-publication seam as other targets."""
import hashlib
import importlib.util
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from sonos_firmware import releases, retry
from test_app_archive import FakeGitHub

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('store_publication', ROOT/'scripts/archive_android_store_apps.py')
STORE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(STORE)


class AndroidStorePublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.apk = self.root/'base.apk'
        self.apk.write_bytes(b'synthetic APK delivery')
        self.receipt = self.root/'data/apps/android-store-archive.json'
        self.github = FakeGitHub()
        self.uploads = []
        self.signer = STORE.APPS['com.sonos.acr2']['signer_sha256']
        for mocked in (
            patch.object(releases, 'remote_assets', side_effect=self.github.assets),
            patch.object(releases.subprocess, 'run', side_effect=self.github.command),
            patch.object(releases, 'public_release', side_effect=lambda repo, tag: {'id': tag}),
            patch.object(releases, 'upload_streaming', side_effect=self.upload),
            patch.object(releases.time, 'sleep'),
            patch.object(STORE, 'unpack_delivery', return_value=(
                {'package': 'com.sonos.acr2', 'version_code': '1', 'version_name': '1.0'}, [self.apk])),
            patch.object(STORE.subprocess, 'check_output', side_effect=self.verify),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def verify(self, command, **kwargs):
        self.assertEqual(Path(command[0]).name, 'verify-apk')
        return f'verified=true\nsigner.1.sha256={self.signer}\nv2=true\n'

    def upload(self, repo, tag, name, path):
        body = path.read_bytes()
        self.github.releases[(repo, tag)][name] = {
            'size': len(body), 'digest': 'sha256:'+hashlib.sha256(body).hexdigest(),
            'browser_download_url': f'https://github.com/{repo}/releases/download/{tag}/{name}'}
        self.uploads.append(name)

    def run_archive(self, selector=None):
        with patch.object(sys, 'argv', ['archive_android_store_apps.py', '--source', 'apkpure-recovery',
                '--package', 'com.sonos.acr2', '--input', str(self.apk), '--receipt', str(self.receipt),
                '--repo', 'fresh/archive', *(['--version-selector', selector] if selector else [])]):
            return STORE.main()

    def test_first_signed_delivery_creates_release_and_publishes_full_hash_receipts(self):
        self.assertEqual(self.github.releases, {})
        self.assertEqual(self.run_archive(), 0)
        component = json.loads(self.receipt.read_text())['artifacts'][0]['components'][0]
        self.assertTrue(component['verified'])
        self.assertEqual(component['status'], 'verified')
        self.assertEqual(component['repository'], 'fresh/archive')
        self.assertIn(hashlib.sha256(self.apk.read_bytes()).hexdigest(), component['release_asset'])
        self.assertEqual(len(self.uploads), 1)

    def test_untrusted_signer_cannot_create_a_release_or_upload(self):
        self.signer = '0'*64
        self.assertEqual(self.run_archive(), 1)
        self.assertEqual(self.github.releases, {})
        self.assertEqual(self.uploads, [])
        self.assertFalse(self.receipt.exists())

    def test_missing_server_digest_requires_hash_verification_before_registration(self):
        digest = hashlib.sha256(self.apk.read_bytes()).hexdigest()
        name = f'com.sonos.acr2--1.0--base--{digest}.apk'
        self.github.releases[('fresh/archive', 'apps-google-play-recovery-s2')] = {name: {
            'size': self.apk.stat().st_size, 'digest': None,
            'browser_download_url': 'https://example.invalid/fixture.apk'}}
        with patch.object(releases.urllib.request, 'urlopen', return_value=io.BytesIO(b'wrong bytes')):
            self.assertEqual(self.run_archive(), 1)
        self.assertEqual(self.uploads, [])
        document = json.loads(self.receipt.read_text())
        self.assertEqual(document['artifacts'], [])
        self.assertEqual(document['pending_total'], 1)

    def test_lost_response_is_recovered_without_original_input_or_store_acquisition(self):
        def upload_then_lose_response(*args):
            self.upload(*args)
            raise TimeoutError('response lost after GitHub stored the delivery')
        with patch.object(releases, 'upload_streaming', side_effect=upload_then_lose_response):
            self.assertEqual(self.run_archive('1.0'), 1)
        document = json.loads(self.receipt.read_text())
        self.assertEqual(document['artifacts'], [])
        self.assertEqual(len(document['pending_captures']), 1)
        self.apk.unlink()
        later = datetime.now(timezone.utc)+timedelta(hours=2)
        with patch.object(retry, '_now', return_value=later), \
             patch.object(STORE, 'acquire', side_effect=AssertionError('must reconcile before acquisition')) as acquire, \
             patch.object(STORE, 'unpack_delivery', side_effect=AssertionError('no original package remains')) as unpack:
            self.assertEqual(self.run_archive('1.0'), 0)
        acquire.assert_not_called()
        unpack.assert_not_called()
        document = json.loads(self.receipt.read_text())
        self.assertEqual(document['pending_captures'], [])
        self.assertEqual(len(document['artifacts']), 1)
        self.assertEqual(document['artifacts'][0]['components'][0]['status'], 'verified')
        self.assertEqual(len(self.uploads), 1)

    def test_changed_bytes_at_same_version_are_preserved_as_distinct_deliveries(self):
        self.assertEqual(self.run_archive(), 0)
        self.apk.write_bytes(b'changed synthetic APK with the same version code')
        self.assertEqual(self.run_archive(), 0)
        records = json.loads(self.receipt.read_text())['artifacts']
        self.assertEqual(len(records), 2)
        self.assertEqual({row['version_code'] for row in records}, {'1'})
        self.assertEqual(len({row['delivery_sha256'] for row in records}), 2)

    def test_selector_aliases_are_retained_when_same_delivery_is_deduplicated(self):
        self.assertEqual(self.run_archive('1.0'), 0)
        self.assertEqual(self.run_archive('alias'), 0)
        record, = json.loads(self.receipt.read_text())['artifacts']
        self.assertEqual(record['source_version_selectors'], ['1.0', 'alias'])
        self.assertEqual(len(self.uploads), 1)

    def test_empty_delivery_cannot_be_registered_as_a_preserved_app(self):
        with patch.object(STORE, 'unpack_delivery', return_value=(
                {'package': 'com.sonos.acr2', 'version_code': '1', 'version_name': '1.0'}, [])):
            self.assertEqual(self.run_archive(), 1)
        self.assertEqual(self.github.releases, {})
        self.assertFalse(self.receipt.exists())

    def test_one_package_failure_does_not_block_another_package(self):
        second = self.root/'second.apk'
        second.write_bytes(b'other synthetic package')
        def unpack(source, scratch):
            if source == self.apk:
                raise OSError('first package unavailable')
            return {'package': 'com.sonos.acr', 'version_code': '1', 'version_name': '1.0'}, [second]
        self.signer = STORE.APPS['com.sonos.acr']['signer_sha256']
        with patch.object(STORE, 'unpack_delivery', side_effect=unpack), \
             patch.object(sys, 'argv', ['archive_android_store_apps.py', '--source', 'apkpure-recovery',
                 '--input', str(self.apk), '--input', str(second), '--repo', 'fresh/archive',
                 '--receipt', str(self.receipt)]):
            self.assertEqual(STORE.main(), 1)
        artifact, = json.loads(self.receipt.read_text())['artifacts']
        self.assertEqual(artifact['package'], 'com.sonos.acr')

    def test_partial_delivery_stays_pending_when_original_inputs_are_unavailable(self):
        extra = self.root/'metadata.dm'
        extra.write_bytes(b'synthetic delivery metadata')
        metadata = {'package': 'com.sonos.acr2', 'version_code': '1', 'version_name': '1.0'}
        def fail_second(repo, tag, name, path):
            if path == extra:
                raise OSError('second component did not upload')
            self.upload(repo, tag, name, path)
        with patch.object(STORE, 'unpack_delivery', return_value=(metadata, [self.apk, extra])), \
             patch.object(releases, 'upload_streaming', side_effect=fail_second):
            self.assertEqual(self.run_archive('1.0'), 1)
        self.apk.unlink()
        extra.unlink()
        later = datetime.now(timezone.utc)+timedelta(hours=2)
        with patch.object(retry, '_now', return_value=later), \
             patch.object(STORE, 'unpack_delivery', side_effect=OSError('original delivery unavailable')):
            self.assertEqual(self.run_archive('1.0'), 1)
        document = json.loads(self.receipt.read_text())
        self.assertEqual(document['artifacts'], [])
        self.assertEqual(len(document['pending_captures']), 1)
        self.assertEqual(len(document['pending_captures'][0]['artifact']['components']), 2)
        self.assertEqual(len(self.uploads), 1)

    def test_pending_selector_alias_recovers_without_reacquiring_the_original(self):
        def upload_then_lose_response(*args):
            self.upload(*args)
            raise TimeoutError('upload response lost')
        with patch.object(releases, 'upload_streaming', side_effect=upload_then_lose_response):
            self.assertEqual(self.run_archive('1.0'), 1)
        self.github.inventory_failure = OSError('inventory temporarily unavailable')
        self.assertEqual(self.run_archive('alias'), 1)
        self.apk.unlink()
        with patch.object(retry, '_now', return_value=datetime.now(timezone.utc)+timedelta(hours=2)), \
             patch.object(STORE, 'unpack_delivery', side_effect=AssertionError('no original delivery')) as unpack:
            self.assertEqual(self.run_archive('alias'), 0)
        unpack.assert_not_called()
        artifact, = json.loads(self.receipt.read_text())['artifacts']
        self.assertEqual(artifact['source_version_selectors'], ['1.0', 'alias'])

    def test_recovery_does_not_skip_changed_explicit_input_at_same_selector(self):
        def upload_then_lose_response(*args):
            self.upload(*args)
            raise TimeoutError('upload response lost')
        with patch.object(releases, 'upload_streaming', side_effect=upload_then_lose_response):
            self.assertEqual(self.run_archive('1.0'), 1)
        self.apk.write_bytes(b'new bytes supplied under the same selector')
        with patch.object(retry, '_now', return_value=datetime.now(timezone.utc)+timedelta(hours=2)):
            self.assertEqual(self.run_archive('1.0'), 0)
        records = json.loads(self.receipt.read_text())['artifacts']
        self.assertEqual(len(records), 2)
        self.assertEqual(len({row['delivery_sha256'] for row in records}), 2)
        self.assertEqual(len(self.uploads), 2)
