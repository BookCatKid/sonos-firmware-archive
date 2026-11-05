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


class ModernMonitorTests(unittest.TestCase):
    def test_modern_manifest_enters_existing_discovery_path(self):
        from unittest.mock import patch
        from sonos_firmware.monitor import run_monitor
        xml = b'''<update_manifest version="1.1" system_version="98.0-82170" default_version="98.0-82170"><image model="27" version="98.0-82170">https://update-firmware.sonos.com/firmware/Prod/new/98.0-82170-1-27.upd</image></update_manifest>'''
        canonical = 'https://update.sonos.com/firmware/Prod/new/update.upm'
        def probes(candidates, workers, **kwargs):
            return [dict(vars(c), available=True) for c in candidates]
        with patch('sonos_firmware.monitor.fetch_metadata_with_retries', return_value=(b'', {})), patch('sonos_firmware.monitor.fetch_modern_manifest', return_value=(xml, {'url': canonical})), patch('sonos_firmware.monitor.fetch_bytes', return_value=b''), patch('sonos_firmware.monitor.probe_all', side_effect=probes):
            result = run_monitor({'packages': [], 'source_manifests': []})
        self.assertEqual(result['ups']['manifest_urls'], [])
        self.assertEqual(result['manifests'][0]['url'], canonical)
        self.assertEqual(result['available_uncatalogued_candidates'][0]['version'], '98.0-82170')
        self.assertTrue(result['change_detected'])


    def test_modern_outage_still_reports_s1(self):
        from unittest.mock import patch
        from sonos_firmware.monitor import run_monitor
        def probes(candidates, workers, **kwargs):
            return [dict(vars(c), available=True) for c in candidates]
        with patch('sonos_firmware.monitor.fetch_metadata_with_retries', return_value=(b'', {})), \
                patch('sonos_firmware.monitor.fetch_modern_manifest', side_effect=Exception('s2 down')), \
                patch('sonos_firmware.monitor.fetch_bytes', return_value=b''), \
                patch('sonos_firmware.monitor.probe_all', side_effect=probes), \
                patch('sonos_firmware.monitor.time.sleep'):
            result = run_monitor({'packages': [], 'source_manifests': []})
        self.assertEqual(result['modern_error'], 's2 down')
        self.assertFalse(result['change_detected'])


if __name__ == "__main__":
    unittest.main()
