import tempfile
import unittest
from pathlib import Path

from sonos_firmware.extract import extract_components, extract_available_components
from test_upd import section


class ExtractTests(unittest.TestCase):
    def test_all_sections_preserves_unknown_types_and_reports_blocked_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'variant--hash.upd'
            package.write_bytes(section(99, 0, b'first') + section(99, 0, b'second') +
                                section(4, 4, b'encrypted fixture') + section(6, 0, b'kernel'))
            result = extract_available_components(package, root / 'raw')
            self.assertEqual([row['section_index'] for row in result['components']], [0, 1, 3])
            self.assertEqual([row['section_index'] for row in result['skipped']], [2])
            self.assertEqual(len({row['filename'] for row in result['components']}), 3)
            self.assertTrue(all(row['package_id'] == 'variant--hash' for row in result['components']))
            self.assertEqual((root / 'raw' / result['components'][1]['filename']).read_bytes(), b'second')

    def test_extracts_named_plaintext_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "34.16-37101-1-9.upd"
            package.write_bytes(section(3, 0, b"#!/bin/sh\n") + section(6, 0, b"kernel"))
            records = extract_components(package, root / "raw")

            self.assertEqual([item["kind"] for item in records], ["preinstall", "kernel"])
            self.assertEqual((root / "raw" / "34.16-37101-1-9-kernel.uImage").read_bytes(), b"kernel")
            self.assertFalse(records[0]["source_encrypted"])

    def test_can_skip_wrapped_component_and_extract_later_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "mixed.upd"
            package.write_bytes(section(3, 4, b"wrapped") + section(6, 0, b"kernel"))
            records = extract_components(package, root / "raw", skip_encrypted=True)

            self.assertEqual([item["kind"] for item in records], ["kernel"])
            self.assertEqual((root / "raw" / "mixed-kernel.uImage").read_bytes(), b"kernel")


if __name__ == "__main__":
    unittest.main()
