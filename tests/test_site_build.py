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
