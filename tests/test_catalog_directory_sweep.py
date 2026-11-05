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


class MonitorReportGateTests(unittest.TestCase):
    def load(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/check_catalog_firmware_directories.py'
        spec = importlib.util.spec_from_file_location('directory_sweep_gate', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return script, module

    def catalog(self, directory):
        catalog = Path(directory) / 'catalog.json'
        catalog.write_text(json.dumps({'packages': [{
            'id': '91.0-69300-1-20', 'version': '91.0-69300',
            'artifact_type': 'upd', 'artifact_status': 'preserved',
            'source_url': 'https://update-firmware.sonos.com/firmware/Gold/known/91.0-69300-1-20.upd',
        }]}))
        return catalog

    def test_monitor_error_skips_sweep(self):
        script, module = self.load()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'
            monitor = Path(directory) / 'monitor.json'
            monitor.write_text(json.dumps({'status': 'error', 'error': 'boom'}))
            argv = [str(script), '--catalog', str(self.catalog(directory)), '--output', str(output),
                    '--seeds', str(Path(directory) / 'none'), '--monitor-report', str(monitor), '--delay', '0']
            with patch.object(sys, 'argv', argv), patch.object(module, 'probe') as probe:
                self.assertEqual(module.main(), 1)
            self.assertEqual(probe.call_count, 0)
            report = json.loads(output.read_text())
            self.assertTrue(report['incomplete'])
            self.assertIn('monitor error', report['error'])

    def test_traversal_limit_marks_sweep_incomplete(self):
        script, module = self.load()
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / 'report.json'
            monitor = Path(directory) / 'monitor.json'
            monitor.write_text(json.dumps({
                'status': 'ok',
                'discovery_graph': {'blocked': False, 'limits_reached': True, 'failures': [], 'directories': []},
            }))
            argv = [str(script), '--catalog', str(self.catalog(directory)), '--output', str(output),
                    '--seeds', str(Path(directory) / 'none'), '--monitor-report', str(monitor),
                    '--delay', '0', '--model-min', '20', '--model-max', '20']
            def probe(c):
                return dict(vars(c), available=False, http_status=404)
            with patch.object(sys, 'argv', argv), patch.object(module, 'probe', side_effect=probe):
                self.assertEqual(module.main(), 1)
            report = json.loads(output.read_text())
            self.assertTrue(report['incomplete'])
            self.assertIn('manifest limit', report['incomplete_reasons'][0])

