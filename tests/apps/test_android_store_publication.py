"""Android delivery sets cross the same verified-publication seam as other targets."""
import hashlib
import importlib.util
import io
import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

from sonos_firmware import releases, retry
from tests.apps.test_app_archive import FakeGitHub

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('store_publication', ROOT/'scripts/apps/archive_android_store_apps.py')
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
            patch.object(STORE.shutil, 'which', return_value='/fixture/tool'),
            patch.object(releases, 'remote_assets', side_effect=self.github.assets),
            patch.object(releases.subprocess, 'run', side_effect=self.github.command),
            patch.object(releases, 'public_release', side_effect=lambda repo, tag: {'id': tag}),
            patch.object(releases, 'upload_streaming', side_effect=self.upload),
            patch.object(releases.time, 'sleep'),
            patch.object(STORE, 'unpack_delivery', return_value=(
                {'package': 'com.sonos.acr2', 'version_code': '1', 'version_name': '1.0'}, [self.apk])),
            patch.object(STORE, 'apk_metadata', return_value={
                'package': 'com.sonos.acr2', 'version_code': '1', 'version_name': '1.0'}),
            patch.object(subprocess, 'check_output', side_effect=self.verify),
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
        with patch.object(sys, 'argv', ['apps/archive_android_store_apps.py', '--source', 'apkpure-recovery',
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


    def test_empty_delivery_cannot_be_registered_as_a_preserved_app(self):
        with patch.object(STORE, 'unpack_delivery', return_value=(
                {'package': 'com.sonos.acr2', 'version_code': '1', 'version_name': '1.0'}, [])):
            self.assertEqual(self.run_archive(), 1)
        self.assertEqual(self.github.releases, {})
        self.assertFalse(self.receipt.exists())


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


    def test_requested_google_play_code_must_match_actual_apk_before_publication(self):
        with patch.object(sys, 'argv', ['archive', '--source', 'google-play-direct',
                '--package', 'com.sonos.acr2', '--version-code', '2', '--input', str(self.apk),
                '--receipt', str(self.receipt), '--repo', 'fresh/archive']):
            self.assertEqual(STORE.main(), 1)
        self.assertEqual(self.uploads, [])
        self.assertEqual(self.github.releases, {})


    def variant_args(self, variant, *, include_input=True):
        return ['archive', '--source', 'apkpure-recovery', '--package', 'com.sonos.acr2',
                '--asset-url', 'https://fixture.invalid/'+variant, '--asset-type', 'APK',
                '--variant-arch', 'arm64-v8a', '--variant', variant, '--version-code', '1',
                '--version-selector', '1.0', '--receipt', str(self.receipt), '--repo', 'fresh/archive',
                *(['--input', str(self.apk)] if include_input else [])]


    def test_recovering_one_variant_does_not_skip_a_different_variant_at_same_version(self):
        def upload_then_lose_response(*args):
            self.upload(*args)
            raise TimeoutError('upload response lost')
        with patch.object(releases, 'upload_streaming', side_effect=upload_then_lose_response), \
             patch.object(sys, 'argv', self.variant_args('one')):
            self.assertEqual(STORE.main(), 1)
        with patch.object(retry, '_now', return_value=datetime.now(timezone.utc)+timedelta(hours=2)), \
             patch.object(STORE, 'acquire_variant', return_value=self.apk) as acquire, \
             patch.object(sys, 'argv', self.variant_args('two', include_input=False)):
            self.assertEqual(STORE.main(), 0)
        acquire.assert_called_once()
        artifact, = json.loads(self.receipt.read_text())['artifacts']
        self.assertEqual({profile['variant'] for profile in artifact['delivery_profiles']}, {'one', 'two'})


class XAPKContentsTests(unittest.TestCase):
    def delivery(self, root):
        import zipfile
        path = root/'fixture.xapk'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('manifest.json', json.dumps({'package_name': 'com.sonos.acr2',
                'version_code': '1', 'version_name': '1.0'}))
            archive.writestr('base.apk', b'synthetic base')
            archive.writestr('split.apk', b'synthetic split')
            archive.writestr('Android/obb/com.sonos.acr2/main.1.com.sonos.acr2.obb', b'synthetic expansion')
        return path


    def test_xapk_wrapper_cannot_mislabel_the_actual_apk(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.delivery(root)
            with patch.object(STORE, 'apk_metadata', return_value={
                    'package': 'com.sonos.acr2', 'version_code': '2', 'version_name': '2.0'}):
                with self.assertRaisesRegex(RuntimeError, 'manifest does not match'):
                    STORE.unpack_delivery(path, root)


    def test_xapk_paths_cannot_escape_worker_extraction_directory(self):
        import zipfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = self.delivery(root)
            with zipfile.ZipFile(path, 'a') as archive:
                archive.writestr('../escaped.json', '{}')
            with self.assertRaisesRegex(RuntimeError, 'unsafe component path'):
                STORE.unpack_delivery(path, root)
            self.assertFalse((root/'escaped.json').exists())
