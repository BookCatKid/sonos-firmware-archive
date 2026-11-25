"""APK inspection shares tooling across stores and official-host publications."""
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import apk, installers
from sonos_firmware.android_store import StoreError


class ApkInspectionTests(unittest.TestCase):
    def test_signature_verifier_retains_all_certificates_and_store_pin_check(self):
        first, second = '1'*64, '2'*64
        output = (f'verified=true\nv2=true\nsigner.1.sha256={first}\nsigner.1.subject=CN=First\n'
                  f'signer.2.sha256={second}\nsigner.2.subject=CN=Second\n')
        with patch.object(apk.subprocess, 'check_output', return_value=output):
            record = apk.verify_apk(Path('fixture.apk'))
            self.assertEqual(record['schemes'], ['v2'])
            self.assertEqual(record['signers'], [{'sha256': first, 'subject': 'CN=First'},
                                                  {'sha256': second, 'subject': 'CN=Second'}])
            with self.assertRaises(StoreError):
                apk.verify_apk(Path('fixture.apk'), '3'*64)

    def test_invalid_signature_or_malformed_certificate_is_rejected(self):
        for output in ('verified=false\nsigner.1.sha256='+'1'*64,
                       'verified=true\nsigner.1.sha256=invalid',
                       'verified=true\nsigner.1.sha256='+'1'*64+'\nsigner.2.sha256=invalid'):
            with self.subTest(output=output), patch.object(apk.subprocess, 'check_output', return_value=output):
                with self.assertRaises(StoreError):
                    apk.verify_apk(Path('fixture.apk'))

    def inspect(self, candidate=None, metadata=None):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'fixture.apk'
            path.write_bytes(b'synthetic APK')
            with patch.object(apk, 'verify_apk', return_value={'verified': True, 'signer_sha256': '1'*64}), \
                 patch.object(apk, 'apk_metadata', return_value=metadata or {
                     'package': 'com.sonos.acr2', 'version_code': '123', 'version_name': '1.2', 'split': None}):
                result = apk.inspect_official(candidate or {}, path)
            self.assertEqual(list(path.parent.iterdir()), [path])
            return result

    def test_official_certificate_is_observed_separately_from_play_pin(self):
        record = self.inspect({'version': 'internal-build-label'})
        self.assertTrue(record['verified'])
        self.assertFalse(record['matches_store_signer'])
        self.assertEqual(record['advertised_version'], 'internal-build-label')
        self.assertEqual(record['package'], 'com.sonos.acr2')
        self.assertEqual(record['version_code'], '123')
        self.assertEqual(record['version_name'], '1.2')

    def test_explicit_package_and_version_expectations_must_match(self):
        for candidate, error in (({'package': 'different.app'}, 'APKPackageMismatch'),
                                 ({'expected_version_code': '124'}, 'APKVersionMismatch'),
                                 ({'expected_version_name': '2.0'}, 'APKVersionMismatch')):
            with self.subTest(candidate=candidate), self.assertRaisesRegex(StoreError, error):
                self.inspect(candidate)
        self.assertTrue(self.inspect({'expected_package': 'com.sonos.acr2',
                                      'expected_version_code': '123', 'expected_version_name': '1.2'})['verified'])

    def test_new_package_id_is_recorded_without_an_app_whitelist(self):
        record = self.inspect(metadata={'package': 'new.official.app', 'version_code': '1', 'version_name': '1'})
        self.assertEqual(record['package'], 'new.official.app')
        self.assertIsNone(record['matches_store_signer'])

    def test_invalid_manifest_identity_is_rejected(self):
        for metadata in ({'package': 'bad id', 'version_code': '1'},
                         {'package': 'fixture.app', 'version_code': 'unknown'},
                         {'package': 'fixture.app', 'version_code': '0'}):
            with self.subTest(metadata=metadata), self.assertRaisesRegex(StoreError, 'APKMetadataInvalid'):
                self.inspect(metadata=metadata)

    def test_mobile_factory_uses_shared_inspector_and_tool_revision(self):
        mobile = installers.source(default_platform='fireos-android-apk')
        self.assertIs(mobile.inspect, apk.inspect_official)
        self.assertEqual(mobile.inspection_revision, apk.inspection_revision())
        self.assertIsNone(installers.source(default_platform='windows').inspect)

    def test_missing_optional_java_defers_acquisition_before_downloading(self):
        source = installers.source(default_platform='fireos-android-apk')
        with patch.object(installers.shutil, 'which', return_value=None), \
             patch.object(installers, 'open_advertised') as download:
            with self.assertRaisesRegex(ValueError, 'APKInspectionUnavailable'):
                source.acquire({'url': 'https://update.sonos.com/fixture.apk'}, Path('unused.apk'))
        download.assert_not_called()


if __name__ == '__main__':
    unittest.main()
