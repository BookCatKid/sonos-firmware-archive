import tempfile
import unittest
from pathlib import Path

from sonos_firmware.extract import extract_components
from test_upd import section


class ExtractTests(unittest.TestCase):
    def test_extracts_named_plaintext_components(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / "34.16-37101-1-9.upd"
            package.write_bytes(section(3, 0, b"#!/bin/sh\n") + section(6, 0, b"kernel"))
            records = extract_components(package, root / "raw")

            self.assertEqual([item["kind"] for item in records], ["preinstall", "kernel"])
            self.assertEqual((root / "raw" / "34.16-37101-1-9-kernel.uImage").read_bytes(), b"kernel")
            self.assertFalse(records[0]["source_encrypted"])


if __name__ == "__main__":
    unittest.main()
