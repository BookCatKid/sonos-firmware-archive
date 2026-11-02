import struct
import unittest
import hashlib
import json
from pathlib import Path

from sonos_firmware.metadata import BASE_URI_TYPE, MANIFEST_URI_TYPE, UPS_MAGIC, parse_update_metadata


def record(record_type: int, body: bytes, unknown: int = 0) -> bytes:
    size = 16 + len(body)
    return struct.pack("<IIII", UPS_MAGIC, record_type, size, unknown) + body


class MetadataTests(unittest.TestCase):
    def test_parses_uri_records(self):
        data = record(BASE_URI_TYPE, b"http://example.test/^1.0\0") + record(
            MANIFEST_URI_TYPE, b"http://example.test/update.upm\0"
        )
        records = parse_update_metadata(data)
        self.assertEqual(records[0]["uri"], "http://example.test/^1.0")
        self.assertEqual(records[1]["uri"], "http://example.test/update.upm")

    def test_rejects_truncated_record(self):
        data = struct.pack("<IIII", UPS_MAGIC, BASE_URI_TYPE, 99, 0)
        with self.assertRaisesRegex(ValueError, "invalid UPS record size"):
            parse_update_metadata(data)

    def test_repository_snapshot_is_redacted_and_byte_exact(self):
        root = Path(__file__).resolve().parents[1]
        receipt = json.loads(
            (root / "data/metadata/default-1-1-2026-09-03.json").read_text(
                encoding="utf-8"
            )
        )
        raw = (root / "data/metadata/default-1-1-2026-09-03.ups").read_bytes()
        self.assertEqual(hashlib.sha256(raw).hexdigest(), receipt["response"]["sha256"])
        self.assertEqual(parse_update_metadata(raw), receipt["records"])
        serialized_profile = json.dumps(receipt["request_profile"]).lower()
        for private_field in ("serial", "sonosid", "householdid"):
            self.assertNotIn(private_field, serialized_profile)


if __name__ == "__main__":
    unittest.main()
