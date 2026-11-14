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

from sonos_firmware import releases
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

    def run_archive(self):
        with patch.object(sys, 'argv', ['archive_android_store_apps.py', '--source', 'apkpure-recovery',
                '--package', 'com.sonos.acr2', '--input', str(self.apk), '--receipt', str(self.receipt),
                '--repo', 'fresh/archive']):
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
        with self.assertRaisesRegex(RuntimeError, 'signature verification failed'):
            self.run_archive()
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
            with self.assertRaisesRegex(RuntimeError, 'different content'):
                self.run_archive()
        self.assertEqual(self.uploads, [])
        self.assertFalse(self.receipt.exists())
