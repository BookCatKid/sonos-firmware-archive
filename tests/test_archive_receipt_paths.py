import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


from sonos_firmware.app_archive import receipt_path


class ArchiveReceiptPathTests(unittest.TestCase):
    def test_relative_discovery_path_inside_repo_is_accepted(self):
        previous = Path.cwd()
        try:
            os.chdir(ROOT)
            path = Path("mobile-app-discovery.json")
            self.assertEqual(receipt_path(path, ROOT), "mobile-app-discovery.json")
        finally:
            os.chdir(previous)

    def test_external_discovery_path_is_kept_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            self.assertEqual(receipt_path(path, ROOT), str(path))

    def test_committed_archive_receipts_reference_committed_discovery(self):
        for name in ("mobile-archive.json", "desktop-archive.json"):
            receipt = json.loads((ROOT / "data/apps" / name).read_text())
            discovery = Path(receipt["discovery"])
            self.assertFalse(discovery.is_absolute())
            self.assertTrue((ROOT / discovery).is_file(), f"missing discovery for {name}: {discovery}")


if __name__ == "__main__":
    unittest.main()
