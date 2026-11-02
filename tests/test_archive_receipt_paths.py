import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


MOBILE = load("archive_mobile_installers")
DESKTOP = load("archive_desktop_installers")


class ArchiveReceiptPathTests(unittest.TestCase):
    def test_relative_discovery_path_inside_repo_is_accepted(self):
        previous = Path.cwd()
        try:
            os.chdir(ROOT)
            path = Path("mobile-app-discovery.json")
            self.assertEqual(MOBILE.receipt_path(path), "mobile-app-discovery.json")
            self.assertEqual(DESKTOP.receipt_path(path), "mobile-app-discovery.json")
        finally:
            os.chdir(previous)

    def test_external_discovery_path_is_kept_stable(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "discovery.json"
            self.assertEqual(MOBILE.receipt_path(path), str(path))
            self.assertEqual(DESKTOP.receipt_path(path), str(path))

    def test_committed_archive_receipts_reference_committed_discovery(self):
        for name in ("mobile-archive.json", "desktop-archive.json"):
            receipt = json.loads((ROOT / "data/apps" / name).read_text())
            discovery = Path(receipt["discovery"])
            self.assertFalse(discovery.is_absolute())
            self.assertTrue((ROOT / discovery).is_file(), f"missing discovery for {name}: {discovery}")


if __name__ == "__main__":
    unittest.main()
