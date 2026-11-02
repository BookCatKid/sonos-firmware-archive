import unittest

from sonos_firmware.monitor import candidate_is_preserved, summarize_changes


def catalog(status="preserved"):
    return {
        "source_manifests": [
            {
                "version": "1.2-3",
                "default_version": "1.2-3",
                "source_url": "https://update.sonos.com/current.upm",
                "sha256": "abc",
            }
        ],
        "packages": [
            {
                "version": "1.2-3",
                "package_model": 5,
                "filename": "1.2-3-1-5.upd",
                "source_url": "https://update.sonos.com/1.2-3-1-5.upd",
                "artifact_status": status,
            }
        ],
    }


class MonitorTests(unittest.TestCase):
    def test_http_manifest_url_matches_known_https_url(self):
        result = summarize_changes(
            catalog(),
            [{"url": "http://update.sonos.com/current.upm", "sha256": "abc", "metadata": {"system_version": "1.2-3", "default_version": "1.2-3"}}],
            [],
            [],
            [],
        )
        self.assertFalse(result["change_detected"])

    def test_package_version_counts_when_old_manifest_row_has_no_default(self):
        fixture = catalog()
        fixture["source_manifests"][0].pop("default_version")
        result = summarize_changes(
            fixture,
            [{"url": "https://update.sonos.com/current.upm", "sha256": "abc", "metadata": {"system_version": "1.2-3", "default_version": "1.2-3"}}],
            [],
            [],
            [],
        )
        self.assertEqual(result["unknown_versions"], [])

    def test_preserved_candidate_matches_stable_identity(self):
        candidate = {
            "version": "1.2-3",
            "package_model": 5,
            "filename": "1.2-3-1-5.upd",
            "url": "https://update-firmware.sonos.com/elsewhere/1.2-3-1-5.upd",
        }
        self.assertTrue(candidate_is_preserved(candidate, catalog()["packages"]))

    def test_available_uncatalogued_candidate_is_a_change(self):
        current = [{
            "version": "2.0-4",
            "package_model": 6,
            "filename": "2.0-4-1-6.upd",
            "url": "https://update.sonos.com/2.0-4-1-6.upd",
            "available": True,
        }]
        result = summarize_changes(catalog(), [], current, [], [])
        self.assertTrue(result["change_detected"])
        self.assertEqual(result["available_uncatalogued_candidates"], current)

    def test_revived_missing_candidate_is_a_change(self):
        result = summarize_changes(
            catalog("missing-cdn"), [], [], [{"url": "https://example.test/a", "available": True}], []
        )
        self.assertTrue(result["change_detected"])
        self.assertEqual(len(result["known_missing_now_available"]), 1)

    def test_new_release_note_version_is_a_change(self):
        result = summarize_changes(catalog(), [], [], [], [], {"1.2-3", "99.1-12345"})
        self.assertTrue(result["change_detected"])
        self.assertEqual(result["unknown_release_note_versions"], ["99.1-12345"])


if __name__ == "__main__":
    unittest.main()
