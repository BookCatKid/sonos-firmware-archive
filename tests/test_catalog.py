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


if __name__ == "__main__":
    unittest.main()
