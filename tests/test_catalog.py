import unittest
import json
from pathlib import Path

import jsonschema


from sonos_firmware.catalog import validate_catalog


class CatalogTests(unittest.TestCase):
    def test_evidence_schema(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "schemas/evidence.schema.json").read_text(encoding="utf-8"))
        catalog = json.loads((root / "data/catalog.json").read_text(encoding="utf-8"))
        jsonschema.validate(catalog.get("evidence", []), schema)

    def test_repository_catalog(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_catalog(root), [])

    def test_diagnostic_evidence_is_redacted(self):
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / "data/catalog.json").read_text(encoding="utf-8"))
        diagnostic = [
            record
            for record in catalog.get("evidence", [])
            if record["source_type"] == "public-issue-diagnostic"
        ]
        self.assertEqual(len(diagnostic), 3)
        self.assertTrue(all(record["redacted"] is True for record in diagnostic))

    def test_official_release_note_evidence_is_cataloged(self):
        root = Path(__file__).resolve().parents[1]
        catalog = json.loads((root / "data/catalog.json").read_text(encoding="utf-8"))
        release_notes = [
            record
            for record in catalog.get("evidence", [])
            if record["source_type"] == "first-party-release-notes"
        ]
        self.assertEqual(len(release_notes), 37)
        self.assertIn("97.1-80312", {record["version"] for record in release_notes})
        self.assertTrue(all(record["redacted"] is True for record in release_notes))

    def test_product_specific_release_note_requires_products(self):
        root = Path(__file__).resolve().parents[1]
        schema = json.loads((root / "schemas/evidence.schema.json").read_text())
        record = {
            "id": "test",
            "kind": "evidenced-version",
            "version": "1.0-1",
            "release_scope": "product-specific",
            "source_type": "first-party-release-notes",
            "source_url": "https://example.test/release-notes",
            "observed": "2026-09-08",
            "redacted": True,
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate([record], schema)


if __name__ == "__main__":
    unittest.main()
