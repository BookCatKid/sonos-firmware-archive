import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
def load(name):
 spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/f'{name}.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class CadenceTests(unittest.TestCase):
 def test_current_desktop_skips_all_history(self):
  m=load('discover_desktop_installers')
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'out.json'
   with patch.object(sys,'argv',['x','--output',str(out),'--current-only']),patch.object(m,'winget_candidates') as winget,patch.object(m,'homebrew_candidates') as brew,patch.object(m,'wayback_candidates') as wayback,patch.object(m,'check_direct_redirects',return_value=[]):
    self.assertEqual(m.main(),0)
   self.assertFalse(winget.called);self.assertFalse(brew.called);self.assertFalse(wayback.called)
   self.assertTrue(json.loads(out.read_text())['current_only'])
 def test_current_mobile_skips_wayback(self):
  m=load('discover_mobile_installers')
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'out.json'
   with patch.object(sys,'argv',['x','--output',str(out),'--current-only']),patch.object(m.urllib.request,'urlopen',side_effect=AssertionError('history fetch')),patch.object(m,'resolve_latest_redirect',return_value={}):
    self.assertEqual(m.main(),0)
   self.assertEqual(json.loads(out.read_text())['candidate_total'],0)
 def test_current_monitor_skips_missing_retries_but_checks_feed(self):
  from sonos_firmware.monitor import run_monitor
  xml=b'<update_manifest version="1.1"><image model="8" version="98.0-82170">https://update.sonos.com/firmware/Prod/current/98.0-82170-1-8.upd</image></update_manifest>'
  cat={'packages':[{'artifact_status':'missing-cdn','version':'1.0-12345','filename':'1.0-12345-1-8.upd','package_model':8,'source_url':'https://update.sonos.com/firmware/Prod/old/1.0-12345-1-8.upd'}],'source_manifests':[]}
  with patch('sonos_firmware.monitor.fetch_metadata_with_retries',return_value=(b'',{})),patch('sonos_firmware.monitor.parse_update_metadata',return_value=[{'type':14,'uri':'https://update.sonos.com/firmware/Prod/current/update.upm'}]),patch('sonos_firmware.monitor.fetch_bytes',side_effect=[xml,b'']),patch('sonos_firmware.monitor.probe_all',side_effect=lambda cs,w:[dict(vars(c),available=True) for c in cs]):
   report=run_monitor(cat,include_history=False)
  self.assertEqual(report['current_candidate_total'],1);self.assertEqual(report['known_missing_checked_total'],0)
 def test_workflow_owners_and_weekly_scope(self):
  s=(ROOT/'.github/workflows/monitor-firmware.yml').read_text();a=(ROOT/'.github/workflows/auto-preserve.yml').read_text()
  self.assertIn('43 7 * * 0',s);self.assertIn('--current-only',s)
  self.assertNotIn('check_new_web_apps.py',s);self.assertNotIn('check_new_web_apps.py',a);self.assertNotIn('archive_web_apps.py',a)
  self.assertIn('13 * * * *',(ROOT/'.github/workflows/archive-web-apps.yml').read_text())
