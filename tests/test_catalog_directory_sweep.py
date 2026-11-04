import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


class DirectorySweepTests(unittest.TestCase):
    def test_includes_speaker_gold_and_stops_at_challenge(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/check_catalog_firmware_directories.py'
        spec = importlib.util.spec_from_file_location('directory_sweep', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as directory:
            catalog = Path(directory) / 'catalog.json'
            output = Path(directory) / 'report.json'
            catalog.write_text(json.dumps({'packages': [{
                'id': '91.0-69300-1-20', 'version': '91.0-69300',
                'artifact_type': 'upd', 'artifact_status': 'preserved',
                'source_url': 'https://update-firmware.sonos.com/firmware/Gold/known/91.0-69300-1-20.upd',
            }]}))
            def probe(c):
                return dict(vars(c), available=c.package_model == 1, http_status=429 if c.package_model == 2 else 200)
            with patch.object(sys, 'argv', [str(script), '--catalog', str(catalog), '--output', str(output), '--seeds', str(Path(directory)/'none'), '--delay', '0']), patch.object(module, 'probe', side_effect=probe):
                self.assertEqual(module.main(), 1)
            report = json.loads(output.read_text())
            self.assertEqual(report['directory_total'], 1)
            self.assertEqual(report['candidate_total'], 3)
            self.assertTrue(report['incomplete'])
            self.assertEqual(report['uncatalogued_available'][0]['package_model'], 1)
