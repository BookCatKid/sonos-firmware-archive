import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "decrypt_pending_firmware", ROOT / "scripts/decrypt_pending_firmware.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

RECIPIENT = "a" * 40
PACKAGE = {
    "id": "99.0-1-8",
    "filename": "99.0-1-8.upd",
    "bytes": 4,
    "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",  # b"test"
    "release_tag": "firmware-99.0-1",
}
COMPONENT = {
    "package_id": "99.0-1-8",
    "section_index": 7,
    "section_type": 3,
    "kind": "preinstall",
    "filename": "99.0-1-8-preinstall.sh",
    "bytes": 4,
    "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "source_encrypted": True,
    "recipient_id": RECIPIENT,
}


class DecryptPendingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.catalog = base / "catalog.json"
        self.ledger = base / "ledger.json"
        self.receipts = base / "receipts"
        self.raw = base / "raw"
        self.staging = base / "staging"
        self.keys = base / "keys"
        for directory in (self.receipts, self.raw, self.staging, self.keys):
            directory.mkdir()
        (self.keys / "model8.pem").write_bytes(b"fake-key")
        self.upd_manifests = base / "upd"
        self.upd_manifests.mkdir()
        self.catalog.write_text(json.dumps({"packages": [PACKAGE], "raw_images": []}))
        self.ledger.write_text(json.dumps({
            "recovered_keys": {RECIPIENT: {
                "package_model": 8,
                "coverage_packages": [{"package_id": "99.0-1-8", "artifact_status": "preserved"}],
            }},
            "missing_keys": [],
        }))
        (self.staging / PACKAGE["filename"]).write_bytes(b"test")

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, *extra):
        argv = [
            "decrypt_pending_firmware.py",
            "--key-directory", str(self.keys),
            "--staging", str(self.staging),
            "--catalog", str(self.catalog),
            "--ledger", str(self.ledger),
            "--raw-directory", str(self.raw),
            "--receipt-directory", str(self.receipts),
            "--upd-manifest-directory", str(self.upd_manifests),
        ]
        with mock.patch("sys.argv", argv + list(extra)):
            return MODULE.main()

    def test_no_pending_is_noop(self):
        (self.receipts / "99.0-1-8.json").write_text("{}")
        with mock.patch.object(MODULE, "extract_components") as extract:
            self.assertEqual(self._run(), 0)
        extract.assert_not_called()

    def test_pending_package_is_decrypted_and_receipted(self):
        def fake_extract(source, output, key):
            (output / COMPONENT["filename"]).write_bytes(b"test")
            return [COMPONENT]
        with mock.patch.object(MODULE, "extract_components", side_effect=fake_extract) as extract:
            self.assertEqual(self._run(), 0)
        self.assertEqual(extract.call_args[0][2], self.keys / "model8.pem")
        receipt = json.loads((self.receipts / "99.0-1-8.json").read_text())
        self.assertEqual(receipt["source"], PACKAGE["filename"])
        self.assertEqual(receipt["components"], [COMPONENT])
        self.assertFalse((self.staging / PACKAGE["filename"]).exists())

    def test_mixed_plaintext_components_are_accepted(self):
        plaintext_component = dict(COMPONENT, filename="99.0-1-8-extra-section-09.bin",
                                   section_index=9, source_encrypted=False, recipient_id=None)
        def fake_extract(source, output, key):
            for component in (COMPONENT, plaintext_component):
                (output / component["filename"]).write_bytes(b"test")
            return [COMPONENT, plaintext_component]
        with mock.patch.object(MODULE, "extract_components", side_effect=fake_extract):
            self.assertEqual(self._run(), 0)
        receipt = json.loads((self.receipts / "99.0-1-8.json").read_text())
        self.assertEqual(len(receipt["components"]), 2)

    def test_recipient_mismatch_is_recorded_without_a_receipt(self):
        bad = dict(COMPONENT, recipient_id="b" * 40)
        with mock.patch.object(MODULE, "extract_components", return_value=[bad]):
            self.assertEqual(self._run(), 1)
        self.assertFalse((self.receipts / "99.0-1-8.json").exists())

    def test_missing_key_skips_without_failure(self):
        (self.keys / "model8.pem").unlink()
        (self.upd_manifests / (PACKAGE['id'] + '.json')).write_text(json.dumps({'sections': [
            {'index': 7, 'section_type': 4, 'encrypted': True, 'recipient_id': RECIPIENT}]}))
        with mock.patch.object(MODULE, "extract_components") as extract, \
             mock.patch.object(MODULE, 'download_verified') as download:
            self.assertEqual(self._run(), 0)
        extract.assert_not_called()
        download.assert_not_called()
        state = json.loads((self.catalog.parent / 'discovery/firmware-extraction.json').read_text())
        self.assertEqual(state['repositories']['BookCatKid/sonos-firmware-archive'][PACKAGE['id']]['status'], 'blocked')
        self.assertFalse((self.receipts / "99.0-1-8.json").exists())

    def test_legacy_plaintext_can_extract_despite_unavailable_model_key(self):
        (self.keys / 'model8.pem').unlink()
        (self.upd_manifests / (PACKAGE['id'] + '.json')).write_text(json.dumps({'sections': [
            {'index': 0, 'section_type': 6, 'encrypted': False}, {'index': 1, 'section_type': 4, 'encrypted': True}]}))
        with mock.patch.object(MODULE, 'extract_components', return_value=[{
            **COMPONENT, 'section_index': 0, 'source_encrypted': False, 'recipient_id': None}]) as extract:
            self.assertEqual(self._run(), 0)
        self.assertIsNone(extract.call_args.args[2])
        self.assertTrue(extract.call_args.kwargs['skip_encrypted'])

    def test_legacy_partial_receipt_does_not_suppress_newly_available_decryption(self):
        prior = {**COMPONENT, 'section_index': 0, 'section_type': 6, 'filename': 'old-kernel.bin',
                 'source_encrypted': False, 'recipient_id': None}
        (self.receipts / (PACKAGE['id'] + '.json')).write_text(json.dumps({'components': [prior]}))
        (self.upd_manifests / (PACKAGE['id'] + '.json')).write_text(json.dumps({'sections': [
            {'index': 0, 'section_type': 6, 'encrypted': False},
            {'index': 1, 'section_type': 4, 'encrypted': True, 'recipient_id': RECIPIENT}]}))
        current = {**COMPONENT, 'section_index': 1, 'section_type': 4, 'filename': 'new-rootfs.bin'}
        with mock.patch.object(MODULE, 'extract_components', return_value=[current]) as extract:
            self.assertEqual(self._run(), 0)
        extract.assert_called_once()
        self.assertEqual(extract.call_args.args[2], self.keys / 'model8.pem')

    def test_source_hash_mismatch_is_recorded_without_a_receipt(self):
        (self.staging / PACKAGE["filename"]).write_bytes(b"bogus")

        def fake_download(*args, **kwargs):
            # corrupt staged file is discarded, then "re-downloaded" still bad
            (self.staging / PACKAGE["filename"]).write_bytes(b"bogus")

        with mock.patch.object(MODULE, "download_verified", side_effect=fake_download) as run:
            self.assertEqual(self._run(), 1)
        run.assert_called()

    def test_failed_attempt_backs_off_and_newly_ready_key_retries_immediately(self):
        with mock.patch.object(MODULE, 'extract_components', side_effect=ValueError('bad fixture')) as extract:
            self.assertEqual(self._run(), 1)
            self.assertEqual(self._run(), 0)
            extract.assert_called_once()
        state_path = self.catalog.parent / 'discovery/firmware-extraction.json'
        state = json.loads(state_path.read_text())
        state['repositories']['BookCatKid/sonos-firmware-archive'][PACKAGE['id']]['revision']['key_available'] = False
        state_path.write_text(json.dumps(state))
        with mock.patch.object(MODULE, 'extract_components', return_value=[COMPONENT]) as extract:
            self.assertEqual(self._run(), 0)
            extract.assert_called_once()

    def test_failed_package_does_not_block_another_and_budget_includes_failure(self):
        second = {**PACKAGE, 'id': 'another-package', 'filename': 'another-package.upd'}
        self.catalog.write_text(json.dumps({'packages': [PACKAGE, second], 'raw_images': []}))
        ledger = json.loads(self.ledger.read_text())
        ledger['recovered_keys'][RECIPIENT]['coverage_packages'].append({'package_id': second['id'], 'artifact_status': 'preserved'})
        self.ledger.write_text(json.dumps(ledger))
        (self.staging / second['filename']).write_bytes(b'test')
        calls = []
        def extract(source, output, key):
            calls.append(source.stem)
            if source.stem == PACKAGE['id']:
                raise ValueError('broken first fixture')
            return [{**COMPONENT, 'package_id': second['id']}]
        with mock.patch.object(MODULE, 'extract_components', side_effect=extract):
            self.assertEqual(self._run('--max-packages', '1'), 1)
            self.assertEqual(calls, [PACKAGE['id']])
            self.assertEqual(self._run('--max-packages', '1'), 0)
        self.assertEqual(calls, [PACKAGE['id'], second['id']])
        self.assertTrue((self.receipts / (second['id'] + '.json')).exists())


PLAINTEXT_PACKAGE = {
    "id": "36.4-1-6",
    "filename": "36.4-1-6.upd",
    "bytes": 4,
    "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",  # b"test"
    "release_tag": "firmware-36.4-1",
    "artifact_status": "preserved",
    "artifact_type": "upd",
}
PLAINTEXT_COMPONENT = {
    "package_id": "36.4-1-6",
    "section_index": 6,
    "section_type": 4,
    "kind": "rootfs",
    "filename": "36.4-1-6-rootfs.squashfs",
    "bytes": 4,
    "sha256": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
    "source_encrypted": False,
    "recipient_id": None,
}


class PlaintextExtractionTests(unittest.TestCase):
    """Preserved UPDs outside the key ledger still owe raw components."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.catalog = base / "catalog.json"
        self.ledger = base / "ledger.json"
        self.receipts = base / "receipts"
        self.raw = base / "raw"
        self.staging = base / "staging"
        self.keys = base / "keys"
        self.upd_manifests = base / "upd"
        for directory in (self.receipts, self.raw, self.staging, self.keys, self.upd_manifests):
            directory.mkdir()
        self.ledger.write_text(json.dumps({"recovered_keys": {}, "missing_keys": []}))
        (self.staging / PLAINTEXT_PACKAGE["filename"]).write_bytes(b"test")

    def tearDown(self):
        self.tmp.cleanup()

    def _catalog(self, *packages):
        self.catalog.write_text(json.dumps({"packages": list(packages), "raw_images": []}))

    def _manifest(self, package_id, sections):
        (self.upd_manifests / f"{package_id}.json").write_text(json.dumps({"sections": sections}))

    def _run(self):
        argv = [
            "decrypt_pending_firmware.py",
            "--key-directory", str(self.keys),
            "--staging", str(self.staging),
            "--catalog", str(self.catalog),
            "--ledger", str(self.ledger),
            "--raw-directory", str(self.raw),
            "--receipt-directory", str(self.receipts),
            "--upd-manifest-directory", str(self.upd_manifests),
        ]
        with mock.patch("sys.argv", argv):
            return MODULE.main()

    def test_plaintext_package_extracted_without_key(self):
        self._catalog(PLAINTEXT_PACKAGE)
        self._manifest("36.4-1-6", [
            {"index": 4, "section_type": 3, "encrypted": False},
            {"index": 5, "section_type": 6, "encrypted": False},
            {"index": 6, "section_type": 4, "encrypted": False},
        ])
        def fake_extract(source, output, key, skip_encrypted=False):
            self.assertIsNone(key)
            self.assertTrue(skip_encrypted)
            (output / PLAINTEXT_COMPONENT["filename"]).write_bytes(b"test")
            return [PLAINTEXT_COMPONENT]
        with mock.patch.object(MODULE, "extract_components", side_effect=fake_extract):
            self.assertEqual(self._run(), 0)
        receipt = json.loads((self.receipts / "36.4-1-6.json").read_text())
        self.assertEqual(receipt["components"], [PLAINTEXT_COMPONENT])
        self.assertFalse((self.staging / PLAINTEXT_PACKAGE["filename"]).exists())

    def test_keyless_completion_preserves_previously_decrypted_section_records(self):
        package = {**PLAINTEXT_PACKAGE, 'extraction_policy': 'all-sections'}
        self._catalog(package)
        self._manifest(package['id'], [{'index': 0, 'section_type': 99, 'encrypted': False},
                                      {'index': 1, 'section_type': 4, 'encrypted': True}])
        prior = {**PLAINTEXT_COMPONENT, 'section_index': 1, 'source_encrypted': True}
        (self.receipts / (package['id'] + '.json')).write_text(json.dumps({'components': [prior]}))
        current = {**PLAINTEXT_COMPONENT, 'section_index': 0, 'filename': 'new-plaintext.bin', 'section_type': 99}
        with mock.patch.object(MODULE, 'extract_available_components', return_value={
            'components': [current], 'skipped': [{'section_index': 1, 'reason': 'no key'}]}):
            self.assertEqual(self._run(), 0)
        receipt = json.loads((self.receipts / (package['id'] + '.json')).read_text())
        self.assertEqual({row['section_index']: row for row in receipt['components']}, {0: current, 1: prior})
        self.assertEqual(receipt['source_sha256'], package['sha256'])

    def test_variant_fetches_exact_published_asset_and_extracts_under_catalog_id(self):
        import hashlib
        from test_upd import section
        body = section(99, 0, b'unknown plaintext') + section(4, 4, b'encrypted fixture')
        package = {**PLAINTEXT_PACKAGE, 'id': 'variant--content-hash', 'filename': 'advertised.upd',
                   'release_asset': 'advertised--immutable-hash.upd', 'extraction_policy': 'all-sections',
                   'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        self._catalog(package)
        self._manifest(package['id'], [{'index': 0, 'section_type': 99, 'encrypted': False},
                                       {'index': 1, 'section_type': 4, 'encrypted': True}])
        def download(source, output, repository):
            self.assertEqual(source['release_asset'], package['release_asset'])
            self.assertEqual(output.name, package['id'] + '.upd')
            output.write_bytes(body)
        with mock.patch.object(MODULE, 'download_verified', side_effect=download):
            self.assertEqual(self._run(), 0)
        receipt = json.loads((self.receipts / (package['id'] + '.json')).read_text())
        self.assertEqual(len(receipt['components']), 1)
        self.assertEqual(receipt['components'][0]['package_id'], package['id'])
        self.assertEqual(receipt['components'][0]['section_type'], 99)

    def test_partial_receipt_retries_when_a_matching_model_key_becomes_available(self):
        package = {**PLAINTEXT_PACKAGE, 'extraction_policy': 'all-sections'}
        self._catalog(package)
        self._manifest(package['id'], [{'index': 0, 'section_type': 99, 'encrypted': False},
                                      {'index': 1, 'section_type': 4, 'encrypted': True}])
        (self.receipts / (package['id'] + '.json')).write_text(json.dumps({'components': [{**PLAINTEXT_COMPONENT, 'section_index': 0}]}))
        self.ledger.write_text(json.dumps({'recovered_keys': {RECIPIENT: {'package_model': 17,
            'packages': [{'package_id': package['id'], 'artifact_status': 'preserved'}]}}}))
        (self.keys / 'model17.pem').write_bytes(b'fixture-key')
        with mock.patch.object(MODULE, 'extract_available_components', return_value={
            'components': [PLAINTEXT_COMPONENT], 'skipped': []}) as extract:
            self.assertEqual(self._run(), 0)
        self.assertEqual(extract.call_args.args[2], self.keys / 'model17.pem')

    def test_mixed_sections_extract_plaintext_keep_encrypted_pending(self):
        self._catalog(PLAINTEXT_PACKAGE)
        self._manifest("36.4-1-6", [
            {"index": 4, "section_type": 3, "encrypted": False},
            {"index": 5, "section_type": 6, "encrypted": True},
        ])
        with mock.patch.object(MODULE, "extract_components", return_value=[PLAINTEXT_COMPONENT]) as extract:
            self.assertEqual(self._run(), 0)
        self.assertTrue(extract.call_args[1]["skip_encrypted"])
        self.assertTrue((self.receipts / "36.4-1-6.json").exists())

    def test_fully_encrypted_package_without_key_is_left_alone(self):
        self._catalog(PLAINTEXT_PACKAGE)
        self._manifest("36.4-1-6", [
            {"index": 5, "section_type": 6, "encrypted": True},
            {"index": 6, "section_type": 4, "encrypted": True},
        ])
        with mock.patch.object(MODULE, "extract_components") as extract:
            self.assertEqual(self._run(), 0)
        extract.assert_not_called()
        self.assertFalse((self.receipts / "36.4-1-6.json").exists())

    def test_existing_receipt_is_not_reextracted(self):
        self._catalog(PLAINTEXT_PACKAGE)
        self._manifest("36.4-1-6", [{"index": 6, "section_type": 4, "encrypted": False}])
        (self.receipts / "36.4-1-6.json").write_text(json.dumps({'components': [PLAINTEXT_COMPONENT]}))
        with mock.patch.object(MODULE, "extract_components") as extract:
            self.assertEqual(self._run(), 0)
        extract.assert_not_called()

    def test_keyless_extraction_must_not_yield_decrypted_content(self):
        self._catalog(PLAINTEXT_PACKAGE)
        self._manifest("36.4-1-6", [{"index": 6, "section_type": 4, "encrypted": False}])
        bad = dict(PLAINTEXT_COMPONENT, source_encrypted=True, recipient_id="c" * 40)
        with mock.patch.object(MODULE, "extract_components", return_value=[bad]):
            self.assertEqual(self._run(), 1)
        self.assertFalse((self.receipts / "36.4-1-6.json").exists())

    def test_ledger_packages_still_take_their_key_path(self):
        self._catalog(PLAINTEXT_PACKAGE, PACKAGE)
        self._manifest("36.4-1-6", [{"index": 6, "section_type": 4, "encrypted": False}])
        (self.keys / "model8.pem").write_bytes(b"fake-key")
        self.ledger.write_text(json.dumps({
            "recovered_keys": {RECIPIENT: {
                "package_model": 8,
                "coverage_packages": [{"package_id": "99.0-1-8", "artifact_status": "preserved"}],
            }},
            "missing_keys": [],
        }))
        (self.staging / PACKAGE["filename"]).write_bytes(b"test")
        def fake_extract(source, output, key, skip_encrypted=False):
            component = COMPONENT if key is not None else PLAINTEXT_COMPONENT
            (output / component["filename"]).write_bytes(b"test")
            return [component]
        with mock.patch.object(MODULE, "extract_components", side_effect=fake_extract):
            self.assertEqual(self._run(), 0)
        self.assertTrue((self.receipts / "99.0-1-8.json").exists())
        self.assertTrue((self.receipts / "36.4-1-6.json").exists())


if __name__ == "__main__":
    unittest.main()
