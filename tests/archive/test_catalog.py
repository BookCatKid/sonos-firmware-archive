import unittest
import json
from pathlib import Path

import jsonschema


class CatalogTests(unittest.TestCase):


    def test_diagnostic_evidence_is_redacted(self):
        root = Path(__file__).resolve().parents[2]
        catalog = json.loads((root / "data/catalog.json").read_text(encoding="utf-8"))
        diagnostic = [
            record
            for record in catalog.get("evidence", [])
            if record["source_type"] == "public-issue-diagnostic"
        ]
        self.assertTrue(all(record["redacted"] is True for record in diagnostic))


    def test_exact_firmware_url_evidence_is_cataloged(self):
        root = Path(__file__).resolve().parents[2]
        catalog = json.loads((root / "data/catalog.json").read_text(encoding="utf-8"))
        reports = [
            record
            for record in catalog.get("evidence", [])
            if record["source_type"] == "public-device-repair-report"
        ]
        self.assertTrue(all(record.get('version') for record in reports))
        self.assertTrue(all(record["update_url"].endswith(".upd") for record in reports))
        self.assertTrue(all(record["redacted"] is True for record in reports))

    def test_product_specific_release_note_requires_products(self):
        root = Path(__file__).resolve().parents[2]
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
