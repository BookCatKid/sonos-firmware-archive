import tempfile
import unittest
from pathlib import Path

from sonos_firmware.discovery import parse_manifest, expand_metadata_base


class DiscoveryTests(unittest.TestCase):
    def test_metadata_base_preserves_observed_path_and_query_with_no_release_list(self):
        rows = expand_metadata_base('http://update.sonos.com/firmware/Gold/opaque/^1.2-345?locale=en', [17, 84])
        self.assertEqual([row.package_model for row in rows], [17, 84])
        self.assertEqual(rows[1].url, 'https://update.sonos.com/firmware/Gold/opaque/1.2-345-1-84.upd?locale=en')
        self.assertEqual(rows[1].filename, '1.2-345-1-84.upd')
        self.assertEqual(rows[1].version, '1.2-345')

    def test_metadata_base_rejects_untrusted_authorities_and_unrecognized_templates(self):
        for uri in ('https://evil.test/firmware/x/^1.2-3',
                    'https://user:pass@update.sonos.com/firmware/x/^1.2-3',
                    'https://update.sonos.com:444/firmware/x/^1.2-3',
                    'https://update.sonos.com/firmware/x/^unknown',
                    'https://update.sonos.com/firmware/x/^1.2-3/extra'):
            with self.subTest(uri=uri):
                self.assertEqual(expand_metadata_base(uri, []), [])


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


if __name__ == "__main__":
    unittest.main()
