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

    def test_empty_metadata_response_is_a_source_failure(self):
        from unittest.mock import MagicMock, patch
        from sonos_firmware.metadata import fetch_update_metadata
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b''
        with patch('sonos_firmware.metadata.open_sonos', return_value=response):
            with self.assertRaisesRegex(ValueError, 'empty UPS'):
                fetch_update_metadata()

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
        root = Path(__file__).resolve().parents[2]
        if not (root/'data/metadata/default-1-1-2026-09-03.json').exists():
            self.skipTest('Optional historical UPS snapshot is absent; generated parser fixtures still run')
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


class ModernManifestTests(unittest.TestCase):
    def response(self, location='http://update.sonos.com/firmware/Prod/current/update.upm'):
        from unittest.mock import MagicMock
        from sonos_firmware.metadata import MODERN_MANIFEST_BASE
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'<update_manifest></update_manifest>'
        response.status = 200
        response.geturl.return_value = MODERN_MANIFEST_BASE
        response.headers = {'Content-Location': location} if location else {}
        return response


    def test_rejects_foreign_content_location(self):
        from unittest.mock import patch
        from sonos_firmware.metadata import fetch_modern_manifest
        with patch('sonos_firmware.metadata.open_sonos', return_value=self.response('https://untrusted.invalid/firmware/a.upm')):
            with self.assertRaisesRegex(ValueError, 'unexpected modern manifest'):
                fetch_modern_manifest()

    def test_accepts_new_sonos_content_location_and_relative_signed_paths(self):
        from unittest.mock import patch
        from sonos_firmware.metadata import fetch_modern_manifest, MODERN_MANIFEST_BASE
        for location, expected in (
            ('https://new-firmware.sonos.com/new-layout/update.upm?variant=1',
             'https://new-firmware.sonos.com/new-layout/update.upm?variant=1'),
            ('relative.upm?variant=1', MODERN_MANIFEST_BASE.rsplit('/', 1)[0]+'/relative.upm?variant=1'),
        ):
            with self.subTest(location=location), patch('sonos_firmware.metadata.open_sonos', return_value=self.response(location)):
                _, receipt = fetch_modern_manifest()
                self.assertEqual(receipt['url'], expected)


    def test_missing_canonical_location_fails_closed(self):
        from unittest.mock import patch
        from sonos_firmware.metadata import fetch_modern_manifest
        with patch('sonos_firmware.metadata.open_sonos', return_value=self.response(None)):
            with self.assertRaisesRegex(ValueError, 'missing Content-Location'):
                fetch_modern_manifest()


if __name__ == "__main__":
    unittest.main()
