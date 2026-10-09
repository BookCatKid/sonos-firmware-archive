import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_site", ROOT / "scripts" / "build_site.py")
BUILD_SITE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(BUILD_SITE)


class SiteBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = BUILD_SITE.build_payload(ROOT)
        cls.records = cls.payload["records"]
        cls.by_id = {record["id"]: record for record in cls.records}

    def test_version_key_orders_numeric_firmware_versions(self):
        versions = ["9.5-100", "10.0-2", "90.00", "89.01", "96.0-79160", "97.2-81220"]
        ordered = sorted(versions, key=BUILD_SITE.version_key)
        self.assertEqual(
            ["9.5-100", "10.0-2", "89.01", "90.00", "96.0-79160", "97.2-81220"],
            ordered,
        )

    def test_update_version_key_orders_legacy_controller_tokens(self):
        versions = [
            "classic-111", "classic-361b", "classic-92", "classic-33",
            "classic-31", "classic-1341",
        ]
        ordered = sorted(versions, key=BUILD_SITE.update_version_key)
        self.assertEqual(
            [
                "classic-31", "classic-33", "classic-361b",
                "classic-92", "classic-111", "classic-1341",
            ],
            ordered,
        )

    def test_payload_is_large_and_ids_are_unique(self):
        ids = [record["id"] for record in self.records]
        self.assertGreater(len(ids), 2000)
        self.assertEqual(len(ids), len(set(ids)))

    def test_every_gap_resolves_and_is_incomplete(self):
        incomplete = {"missing", "partial", "metadata-only", "blocked"}
        for record_id in self.payload["gaps"]:
            self.assertIn(record_id, self.by_id)
            self.assertIn(self.by_id[record_id]["status"], incomplete)

    def test_new_macos_installer_is_preserved(self):
        matches = [
            r for r in self.records
            if r["kind"] == "desktop-installer"
            and r["platform"] == "macos"
            and r["version"] == "90.0-82050"
        ]
        self.assertEqual(1, len(matches))
        self.assertEqual("preserved", matches[0]["status"])
        self.assertTrue(matches[0]["release_url"])
        self.assertTrue(matches[0]["sha256"])

    def test_current_ios_binary_is_not_claimed_preserved(self):
        matches = [
            r for r in self.records
            if r["kind"] == "ios-app-metadata"
            and r["family"] == "s2"
            and r["version"] == "90.00"
        ]
        self.assertEqual(1, len(matches))
        self.assertEqual("metadata-only", matches[0]["status"])
        self.assertIsNone(matches[0]["release_url"])

    def test_known_mobile_gap_stays_missing(self):
        matches = [
            r for r in self.records
            if r["kind"] == "official-apk"
            and r["version"] == "1305"
            and r["status"] == "missing"
        ]
        self.assertEqual(1, len(matches))
        self.assertIn("unrecoverable", matches[0]["subtitle"])

    def test_firmware_matrix_contains_preserved_and_missing_cells(self):
        cells = self.payload["firmware_matrix"]["cells"]
        statuses = {
            cell["status"]
            for version_cells in cells.values()
            for cell in version_cells.values()
        }
        self.assertIn("preserved", statuses)
        self.assertIn("missing", statuses)

    def test_negative_firmware_probes_are_not_archive_gaps(self):
        gap_ids = set(self.payload["gaps"])
        probes = [
            r for r in self.records
            if r["kind"] == "firmware-package"
            and r.get("availability") == "negative-probe"
        ]
        self.assertGreater(len(probes), 100)
        for record in probes:
            self.assertEqual("unavailable-probe", record["status"])
            self.assertNotIn(record["id"], gap_ids)

        exact_missing = [
            r for r in self.records
            if r["kind"] == "firmware-package"
            and r.get("availability") == "exact-missing"
        ]
        self.assertGreaterEqual(len(exact_missing), 1)
        for record in exact_missing:
            self.assertEqual("missing", record["status"])
            self.assertIn(record["id"], gap_ids)

    def test_firmware_decryption_state_is_exposed(self):
        firmware = [r for r in self.records if r["kind"] == "firmware-package"]
        preserved = [r for r in firmware if r.get("availability") == "preserved"]
        self.assertGreater(len(preserved), 500)
        for record in preserved:
            self.assertIn("decryption_state", record)
            self.assertIn("components_extracted", record)
            self.assertIn("filesystem_indexed", record)

        decrypted = [r for r in preserved if r.get("decryption_state") == "decrypted"]
        self.assertGreater(len(decrypted), 50)
        for record in decrypted:
            self.assertTrue(record["decrypted"])
            self.assertTrue(record["source_encrypted"])
            self.assertTrue(record["components_extracted"])

        matrix = self.payload["firmware_matrix"]
        matrix_states = {
            cell.get("decryption_state")
            for version_cells in matrix["cells"].values()
            for cell in version_cells.values()
        }
        self.assertIn("decrypted", matrix_states)
        self.assertIn("encrypted", matrix_states)
        self.assertGreater(self.payload["summary"]["firmware_preserved_packages"],
                           self.payload["summary"]["firmware_exact_gaps"])

    def test_updates_tracks_choose_adjacent_releases(self):
        tracks = {track["id"]: track for track in self.payload["updates"]["tracks"]}
        self.assertIn("macos-s2-desktop", tracks)
        macos = tracks["macos-s2-desktop"]
        self.assertGreater(len(macos["items"]), 4)
        self.assertEqual("90.0-82050", macos["items"][0]["version"])
        self.assertEqual("90.0-81181", macos["items"][0]["previous_version"])
        self.assertTrue(macos["items"][0]["compare_left"])
        self.assertTrue(macos["items"][0]["compare_right"])

        firmware = tracks["firmware-speaker"]
        self.assertGreater(len(firmware["items"]), 20)
        comparable = [
            item for item in firmware["items"]
            if item.get("compare_left") and item.get("compare_right")
        ]
        self.assertGreater(len(comparable), 10)
        self.assertTrue(comparable[0].get("compare_model"))

    def test_latest_macos_smart_diff_decodes_packed_resources(self):
        key = "macos:s2:90.0-81181:90.0-82050"
        index = self.payload["updates"]["smart_diffs"][key]
        self.assertEqual("smart-app-diff", index["kind"])
        self.assertGreater(index["summary"]["semantically_analyzed_changed_files"], 10)
        self.assertNotIn("files", index)
        self.assertEqual(
            "app-diffs/macos-s2-90.0-81181_to_90.0-82050.json",
            index["asset"],
        )

        diff_path = ROOT / "data" / index["asset"]
        diff = BUILD_SITE.load(diff_path, {})
        packed = [
            row for row in diff["files"]["changed"]
            if row.get("semantic", {}).get("type") == "sonos-sclib-resource-db"
        ]
        self.assertEqual(1, len(packed))
        semantic = packed[0]["semantic"]
        self.assertEqual(186923, semantic["strings"]["counts"]["same"])
        self.assertEqual(116, semantic["images"]["counts"]["same"])
        self.assertEqual(271, semantic["jsons"]["counts"]["same"])
        self.assertEqual(0, sum(
            semantic["strings"]["counts"].get(name, 0)
            for name in ("added", "removed", "changed")
        ))

    def test_smart_diff_reports_stay_compact_and_do_not_duplicate_symbol_inventories(self):
        reports = sorted((ROOT / "data" / "app-diffs").glob("*.json"))
        self.assertGreaterEqual(len(reports), 2)
        for path in reports:
            self.assertLess(path.stat().st_size, 1_500_000, path.name)
            report = BUILD_SITE.load(path, {})
            for row in report.get("files", {}).get("changed", []):
                semantic = row.get("semantic") or {}
                if semantic.get("type") != "mach-o":
                    continue
                self.assertNotIn("exported_symbols", semantic.get("old", {}))
                self.assertNotIn("exported_symbols", semantic.get("new", {}))
                self.assertIn("old_exported_symbol_count", semantic)
                self.assertIn("new_exported_symbol_count", semantic)

    def test_compare_payload_contains_structural_manifests(self):
        compare = self.payload["compare"]
        self.assertGreater(len(compare["firmware_sections"]), 500)
        self.assertGreaterEqual(len(compare["filesystems"]), 3)
        self.assertGreater(len(compare["raw_receipts"]), 100)
        self.assertGreaterEqual(len(compare["precomputed"]), 2)

    def test_android_children_reference_existing_parent(self):
        for record in self.records:
            if record["kind"] != "android-component":
                continue
            self.assertTrue(record["parent_id"])
            self.assertIn(record["parent_id"], self.by_id)
            self.assertIn(record["id"], self.by_id[record["parent_id"]]["children"])

    def test_research_receipts_are_indexed_without_embedding_full_bodies(self):
        research = [r for r in self.records if r["category"] == "research"]
        self.assertGreater(len(research), 100)
        self.assertTrue(any(r["kind"] == "discovery-receipt" for r in research))
        self.assertTrue(any(r["kind"] == "decryption-receipt" for r in research))
        for record in research:
            detail = self.payload["details"].get(record["id"], {})
            self.assertNotIn("candidates", detail)
            self.assertIn("source_urls", record)

    def test_key_view_contains_no_private_key_material(self):
        key_records = [r for r in self.records if r["category"] == "keys"]
        self.assertGreater(len(key_records), 20)
        serialized = str({
            record["id"]: self.payload["details"].get(record["id"], {})
            for record in key_records
        })
        self.assertNotIn("BEGIN PRIVATE KEY", serialized)
        self.assertNotIn("BEGIN RSA PRIVATE KEY", serialized)

    def test_unique_preserved_bytes_are_nonzero(self):
        summary = self.payload["summary"]
        self.assertGreater(summary["unique_preserved_blobs"], 500)
        self.assertGreater(summary["unique_preserved_bytes"], 1_000_000_000)


if __name__ == "__main__":
    unittest.main()
