import tempfile
import unittest
from pathlib import Path

from sonos_firmware.discovery import parse_manifest


class DiscoveryTests(unittest.TestCase):
    def test_expands_placeholder_and_default_models(self):
        xml = """<?xml version='1.0'?>
        <update_manifest version='1.1' revision='r' system_version='2.0' default_version='2.0'
          base_url='http://example.test/fw/^2.0' swgen='2'>
          <supported_models><model_list swgen='2'>8,17.3,17.5</model_list></supported_models>
          <update_list><image model='9' version='1.0'>http://example.test/old/^1.0</image></update_list>
        </update_manifest>"""
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.upm"
            path.write_text(xml)
            metadata, candidates = parse_manifest(path)
        self.assertEqual(metadata["revision"], "r")
        urls = {candidate.url for candidate in candidates}
        self.assertIn("https://example.test/old/1.0-1-9.upd", urls)
        self.assertIn("https://example.test/fw/2.0-1-8.upd", urls)
        self.assertIn("https://example.test/fw/2.0-1-17.upd", urls)
        self.assertEqual(len(urls), 3)

    def test_tolerates_truncated_signature_comment(self):
        xml = "<update_manifest version='1.1'><update_list/></update_manifest><!-- SIGNATURE:abc"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "update.upm"
            path.write_text(xml)
            metadata, candidates = parse_manifest(path)
        self.assertEqual(metadata["manifest_version"], "1.1")
        self.assertEqual(candidates, [])


if __name__ == "__main__":
    unittest.main()
