import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "archive_monitor_firmware", ROOT / "scripts/archive_monitor_firmware.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class AutoArchiveMonitorTests(unittest.TestCase):
    def test_official_firmware_url_is_strict(self):
        self.assertTrue(MODULE.official_firmware_url(
            "https://update.sonos.com/firmware/Prod/x/1.upd"
        ))
        self.assertTrue(MODULE.official_firmware_url(
            "https://update-firmware.sonos.com/firmware/Prod/x/1.upd"
        ))
        self.assertFalse(MODULE.official_firmware_url(
            "http://update.sonos.com/firmware/Prod/x/1.upd"
        ))
        self.assertFalse(MODULE.official_firmware_url(
            "https://update-software.sonos.com/software/x/app.apk"
        ))
        self.assertFalse(MODULE.official_firmware_url(
            "https://example.com/firmware/Prod/x/1.upd"
        ))

    def test_collect_candidates_deduplicates_stable_identity(self):
        first = {
            "version": "99.0-12345",
            "package_model": 8,
            "filename": "99.0-12345-1-8.upd",
            "url": "https://update.sonos.com/firmware/Prod/a/99.0-12345-1-8.upd",
            "available": True,
        }
        alternate = {
            **first,
            "url": "https://update-firmware.sonos.com/firmware/Prod/a/99.0-12345-1-8.upd",
        }
        unsafe = {
            **first,
            "package_model": 9,
            "filename": "99.0-12345-1-9.upd",
            "url": "https://example.com/firmware/99.0-12345-1-9.upd",
        }
        monitor = {
            "available_uncatalogued_candidates": [alternate],
            "known_missing_now_available": [first, unsafe],
        }
        directory = {"uncatalogued_available": [first]}
        result = MODULE.collect_candidates(monitor, directory)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["package_model"], 8)

    def test_manifest_name_distinguishes_source_urls(self):
        base = {
            "sha256": "a" * 64,
            "metadata": {"system_version": "99.0-12345"},
        }
        one = MODULE.manifest_name({**base, "url": "https://update.sonos.com/firmware/a/update.upm"})
        two = MODULE.manifest_name({**base, "url": "https://update.sonos.com/firmware/b/update.upm"})
        self.assertNotEqual(one, two)
        self.assertTrue(one.endswith(".upm"))


if __name__ == "__main__":
    unittest.main()
