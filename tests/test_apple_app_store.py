import importlib.util
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "check_new_apple_apps", ROOT / "scripts/check_new_apple_apps.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)


class AppleAppStoreMonitorTests(unittest.TestCase):
    def test_version_change_is_detected(self):
        baseline = {
            "listings": [{
                "track_id": 1,
                "family": "s2",
                "name": "Sonos",
                "version": "1.0",
                "current_version_release_date": "2026-01-01T00:00:00Z",
                "minimum_os_version": "18.0",
                "file_size_bytes": 100,
                "release_notes": "old",
                "track_view_url": "https://example.test",
            }]
        }
        current = {
            "listings": [{
                **baseline["listings"][0],
                "version": "1.1",
                "release_notes": "new",
            }]
        }
        changes = MODULE.changed(current, baseline)
        self.assertEqual(len(changes), 1)
        self.assertIn("version", changes[0]["changed_fields"])
        self.assertIn("release_notes", changes[0]["changed_fields"])

    def test_identical_listing_is_not_a_change(self):
        listing = {
            "track_id": 1,
            "family": "s1",
            "name": "Sonos S1 Controller",
            "version": "11.16.1",
            "current_version_release_date": "2026-02-25T14:37:57Z",
            "minimum_os_version": "12.0",
            "file_size_bytes": 108355584,
            "release_notes": "notes",
            "track_view_url": "https://example.test",
        }
        self.assertEqual(
            MODULE.changed({"listings": [listing]}, {"listings": [dict(listing)]}),
            [],
        )


if __name__ == "__main__":
    unittest.main()
