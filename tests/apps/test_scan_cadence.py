import importlib.util,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
def load(name):
 spec=importlib.util.spec_from_file_location(name,next((ROOT / 'scripts').rglob(f'{name}.py')));m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

class CadenceTests(unittest.TestCase):
 def test_current_desktop_skips_all_history(self):
  m=load('discover_desktop_installers')
  with tempfile.TemporaryDirectory() as d:
   out=Path(d)/'out.json'
   with patch.object(sys,'argv',['x','--output',str(out),'--current-only']),patch.object(m,'winget_candidates') as winget,patch.object(m,'homebrew_candidates') as brew,patch.object(m,'check_direct_redirects',return_value=[]):
    self.assertEqual(m.main(),0)
   self.assertFalse(winget.called);self.assertFalse(brew.called)
   self.assertTrue(json.loads(out.read_text())['current_only'])

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
