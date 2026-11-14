import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import yaml
ROOT = Path(__file__).resolve().parents[1]
def load(name):
    spec=importlib.util.spec_from_file_location(name,ROOT/'scripts'/(name+'.py'))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class RedirectHistoryTests(unittest.TestCase):
    def test_current_helpers_never_use_archive_when_live_missing_or_errors(self):
        for name in ['discover_desktop_installers','discover_mobile_installers']:
            m=load(name);helper=m.check_direct_redirects if 'desktop' in name else m.resolve_latest_redirect
            for fail in [False,True]:
                with self.subTest(name=name,fail=fail):
                    session=Mock();session.get.return_value=Mock(status_code=200,headers={})
                    if fail:session.get.side_effect=OSError('offline')
                    opener=Mock();opener.open.side_effect=AssertionError('archive request in current mode')
                    with patch.object(m.tls_requests,'Session',return_value=session),patch.object(m.urllib.request,'build_opener',return_value=opener):result=helper(include_history=False)
                    opener.open.assert_not_called()
                    rows=result if isinstance(result,list) else [result]
                    self.assertTrue(all(row['location'] is None for row in rows))
    def test_cli_forwards_scope_to_helper(self):
        for name,helper in [('discover_desktop_installers','check_direct_redirects'),('discover_mobile_installers','resolve_latest_redirect')]:
            m=load(name)
            with self.subTest(name=name),tempfile.TemporaryDirectory() as d:
                with patch.object(sys,'argv',['x','--current-only','--output',str(Path(d)/'out')]),patch.object(m,helper,return_value=[] if 'desktop' in name else {}) as call:self.assertEqual(m.main(),0)
                call.assert_called_once_with(include_history=False)
class GeneralIncompletePropagationTests(unittest.TestCase):
    def test_nonfetch_incompleteness_keeps_directory_report_incomplete(self):
        m=load('check_catalog_firmware_directories')
        for reason in ['unsupported host','unrecognized manifest syntax']:
            with self.subTest(reason=reason),tempfile.TemporaryDirectory() as d:
                d=Path(d);(d/'catalog').write_text('{"packages":[]}');(d/'monitor').write_text(json.dumps({'status':'ok','discovery_incomplete':True,'discovery_graph':{'blocked':False,'limits_reached':False,'failures':[],'directories':[]},'manifests':[{'metadata':{'warnings':[reason]}}]}))
                argv=['x','--catalog',str(d/'catalog'),'--monitor-report',str(d/'monitor'),'--output',str(d/'out'),'--seeds',str(d/'none'),'--delay','0']
                with patch.object(sys,'argv',argv),patch.object(m,'probe') as probe:self.assertEqual(m.main(),1)
                probe.assert_not_called();report=json.loads((d/'out').read_text());self.assertTrue(report['incomplete']);self.assertIn('monitor reported incomplete discovery',report['incomplete_reasons']);self.assertEqual(report['monitor_incomplete_evidence']['manifests'][0]['metadata']['warnings'],[reason])


class HistoricalFallbackTests(unittest.TestCase):
    def test_history_helpers_still_try_archive_fallback(self):
        for name in ['discover_desktop_installers','discover_mobile_installers']:
            m=load(name);helper=m.check_direct_redirects if 'desktop' in name else m.resolve_latest_redirect
            session=Mock();session.get.return_value=Mock(status_code=200,headers={})
            opener=Mock()
            with patch.object(m.tls_requests,'Session',return_value=session),patch.object(m.urllib.request,'build_opener',return_value=opener):helper(include_history=True)
            self.assertTrue(opener.open.called)
