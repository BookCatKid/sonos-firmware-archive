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
    def test_live_redirects_accept_new_first_party_distribution_hosts(self):
        for name, suffix in [('discover_desktop_installers', 'exe'), ('discover_mobile_installers', 'apk')]:
            m = load(name)
            helper = m.check_direct_redirects if 'desktop' in name else m.resolve_latest_redirect
            location = 'https://new-installers.sonos.com/new-layout/Sonos_100.2.'+suffix
            session = Mock()
            session.get.return_value = Mock(status_code=302, headers={'location': location})
            with self.subTest(name=name), patch.object(m.tls_requests, 'Session', return_value=session):
                result = helper(include_history=False)
            rows = result if isinstance(result, list) else [result]
            self.assertTrue(all(row['location'] == location for row in rows))

    def test_live_redirects_stop_before_requesting_private_hosts(self):
        for name in ['discover_desktop_installers', 'discover_mobile_installers']:
            m = load(name)
            helper = m.check_direct_redirects if 'desktop' in name else m.resolve_latest_redirect
            session = Mock()
            session.get.return_value = Mock(status_code=302, headers={'location': 'https://127.0.0.1/landing'})
            with self.subTest(name=name), patch.object(m.tls_requests, 'Session', return_value=session):
                result = helper(include_history=False)
            rows = result if isinstance(result, list) else [result]
            self.assertTrue(all(row['location'] is None and row.get('error') for row in rows))
            expected_calls = 4 if 'desktop' in name else 1
            self.assertEqual(session.get.call_count, expected_calls)

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
class HistoricalFallbackTests(unittest.TestCase):
    def test_history_helpers_still_try_archive_fallback(self):
        for name in ['discover_desktop_installers','discover_mobile_installers']:
            m=load(name);helper=m.check_direct_redirects if 'desktop' in name else m.resolve_latest_redirect
            session=Mock();session.get.return_value=Mock(status_code=200,headers={})
            opener=Mock()
            with patch.object(m.tls_requests,'Session',return_value=session),patch.object(m.urllib.request,'build_opener',return_value=opener):helper(include_history=True)
            self.assertTrue(opener.open.called)
