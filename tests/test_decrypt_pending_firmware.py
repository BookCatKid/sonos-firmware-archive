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


if __name__ == "__main__":
    unittest.main()
