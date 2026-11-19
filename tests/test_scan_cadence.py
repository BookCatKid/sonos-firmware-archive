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
   with patch.object(sys,'argv',['x','--output',str(out),'--current-only']),patch.object(m,'winget_candidates') as winget,patch.object(m,'homebrew_candidates') as brew,patch.object(m,'check_direct_redirects',return_value=[]):
    self.assertEqual(m.main(),0)
   self.assertFalse(winget.called);self.assertFalse(brew.called)
   self.assertTrue(json.loads(out.read_text())['current_only'])
 def test_current_mobile_skips_wayback(self):
  m=load('discover_mobile_installers')
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'out.json'
   with patch.object(sys,'argv',['x','--output',str(out),'--current-only']),patch.object(m.urllib.request,'urlopen',side_effect=AssertionError('history fetch')),patch.object(m,'resolve_latest_redirect',return_value={}):
    self.assertEqual(m.main(),0)
   self.assertEqual(json.loads(out.read_text())['candidate_total'],0)
 def test_target_cadences_are_configured_independently(self):
  from sonos_firmware.platform.registry import targets
  configured=targets(ROOT)
  self.assertEqual(configured['web'].interval_hours,1)
  self.assertEqual(configured['firmware'].interval_hours,6)
  self.assertEqual(configured['gpl-audit'].interval_hours,24)
  self.assertFalse(configured['google-play'].manual)

class TraversalCadenceTests(unittest.TestCase):
 def test_current_skips_history_but_follows_current_package_sibling(self):
  from sonos_firmware.crawl import crawl_manifests
  base='https://update.sonos.com/firmware/Prod/current/'
  sibling='https://update.sonos.com/firmware/Prod/milestone/'
  xml=('<update_manifest version="1.1"><image model="8" version="98.0-12345">'+sibling+'98.0-12345-1-8.upd</image><link>../history/update.upm</link></update_manifest>').encode()
  cat={'source_manifests':[{'source_url':base+'old.upm'}],'packages':[{'source_url':base+'old.exe'}]}
  calls=[]
  def fetch(url):
   calls.append(url);return b'<update_manifest version="1.1"></update_manifest>'
  with tempfile.TemporaryDirectory() as d:
   r=crawl_manifests(cat,[{'url':base+'update.upm','body':xml}],[base+'seed.upm'],Path(d),include_history=False,fetch=fetch,delay=0)
  self.assertEqual(calls,[sibling+'update.upm'])
  self.assertEqual(len(r['candidates']),1)
  self.assertFalse(r['failures'])
 def test_history_retains_catalog_seeds_and_manifest_links(self):
  from sonos_firmware.crawl import crawl_manifests
  base='https://update.sonos.com/firmware/Prod/current/'
  xml=b'<update_manifest version="1.1"><link>../history/update.upm</link></update_manifest>'
  calls=[]
  def fetch(url):
   calls.append(url);return b'<update_manifest version="1.1"></update_manifest>'
  with tempfile.TemporaryDirectory() as d:
   crawl_manifests({'source_manifests':[{'source_url':base+'old.upm'}]},[{'url':base+'update.upm','body':xml}],[base+'seed.upm'],Path(d),include_history=True,fetch=fetch,delay=0)
  self.assertEqual(set(calls),{base+'old.upm',base+'seed.upm','https://update.sonos.com/firmware/Prod/history/update.upm'})
