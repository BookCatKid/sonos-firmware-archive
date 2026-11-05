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


class ModernManifestTests(unittest.TestCase):
    def response(self, location='http://update.sonos.com/firmware/Prod/current/update.upm'):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.read.return_value = b'<update_manifest></update_manifest>'
        response.status = 200
        response.headers = {'Content-Location': location} if location else {}
        return response

    def test_anonymous_query_and_canonical_location(self):
        from unittest.mock import patch
        from urllib.parse import parse_qs, urlsplit
        from sonos_firmware.metadata import fetch_modern_manifest
        with patch('sonos_firmware.metadata.urllib.request.urlopen', return_value=self.response()) as call:
            body, receipt = fetch_modern_manifest()
        query = parse_qs(urlsplit(call.call_args.args[0].full_url).query)
        self.assertEqual(query, {'sonosid': ['Anonymous'], 'householdid': ['nohhid'], 'cmaj': ['81'], 'cmin': ['0'], 'cbld': ['52']})
        self.assertEqual(receipt['url'], 'https://update.sonos.com/firmware/Prod/current/update.upm')
        self.assertEqual(body, b'<update_manifest></update_manifest>')
        self.assertEqual(receipt['sha256'], hashlib.sha256(body).hexdigest())

    def test_normalizes_mixed_case_scheme(self):
        from unittest.mock import patch
        from sonos_firmware.metadata import fetch_modern_manifest
        with patch('sonos_firmware.metadata.urllib.request.urlopen', return_value=self.response('HTTP://update.sonos.com/firmware/Prod/current/update.upm')):
            body, receipt = fetch_modern_manifest()
        self.assertEqual(receipt['url'], 'https://update.sonos.com/firmware/Prod/current/update.upm')

    def test_rejects_foreign_content_location(self):
        from unittest.mock import patch
        from sonos_firmware.metadata import fetch_modern_manifest
        with patch('sonos_firmware.metadata.urllib.request.urlopen', return_value=self.response('https://untrusted.invalid/firmware/a.upm')):
            with self.assertRaisesRegex(ValueError, 'unexpected modern manifest'):
                fetch_modern_manifest()

    def test_http_failure_is_not_silently_ignored(self):
        from unittest.mock import patch
        from urllib.error import HTTPError
        from sonos_firmware.metadata import fetch_modern_manifest
        with patch('sonos_firmware.metadata.urllib.request.urlopen', side_effect=HTTPError('u', 404, 'Not Found', {}, None)):
            with self.assertRaises(HTTPError):
                fetch_modern_manifest()

    def test_missing_canonical_location_fails_closed(self):
        from unittest.mock import patch
        from sonos_firmware.metadata import fetch_modern_manifest
        with patch('sonos_firmware.metadata.urllib.request.urlopen', return_value=self.response(None)):
            with self.assertRaisesRegex(ValueError, 'missing Content-Location'):
                fetch_modern_manifest()


if __name__ == "__main__":
    unittest.main()
