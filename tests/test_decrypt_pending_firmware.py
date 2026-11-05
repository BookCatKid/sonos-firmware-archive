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
                                   source_encrypted=False, recipient_id=None)
        def fake_extract(source, output, key):
            for component in (COMPONENT, plaintext_component):
                (output / component["filename"]).write_bytes(b"test")
            return [COMPONENT, plaintext_component]
        with mock.patch.object(MODULE, "extract_components", side_effect=fake_extract):
            self.assertEqual(self._run(), 0)
        receipt = json.loads((self.receipts / "99.0-1-8.json").read_text())
        self.assertEqual(len(receipt["components"]), 2)

    def test_recipient_mismatch_aborts(self):
        bad = dict(COMPONENT, recipient_id="b" * 40)
        with mock.patch.object(MODULE, "extract_components", return_value=[bad]):
            with self.assertRaises(ValueError):
                self._run()
        self.assertFalse((self.receipts / "99.0-1-8.json").exists())

    def test_missing_key_skips_without_failure(self):
        (self.keys / "model8.pem").unlink()
        with mock.patch.object(MODULE, "extract_components") as extract:
            self.assertEqual(self._run(), 0)
        extract.assert_not_called()
        self.assertFalse((self.receipts / "99.0-1-8.json").exists())

    def test_source_hash_mismatch_aborts(self):
        (self.staging / PACKAGE["filename"]).write_bytes(b"bogus")

        def fake_download(*args, **kwargs):
            # corrupt staged file is discarded, then "re-downloaded" still bad
            (self.staging / PACKAGE["filename"]).write_bytes(b"bogus")

        with mock.patch.object(MODULE.subprocess, "run", side_effect=fake_download) as run:
            with self.assertRaises(ValueError):
                self._run()
        run.assert_called()


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
        (self.receipts / "36.4-1-6.json").write_text("{}")
        with mock.patch.object(MODULE, "extract_components") as extract:
            self.assertEqual(self._run(), 0)
        extract.assert_not_called()

    def test_keyless_extraction_must_not_yield_decrypted_content(self):
        self._catalog(PLAINTEXT_PACKAGE)
        self._manifest("36.4-1-6", [{"index": 6, "section_type": 4, "encrypted": False}])
        bad = dict(PLAINTEXT_COMPONENT, source_encrypted=True, recipient_id="c" * 40)
        with mock.patch.object(MODULE, "extract_components", return_value=[bad]):
            with self.assertRaises(ValueError):
                self._run()
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
