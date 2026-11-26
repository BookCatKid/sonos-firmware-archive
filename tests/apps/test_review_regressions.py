import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
ROOT = Path(__file__).resolve().parents[2]
def load(name):
    spec=importlib.util.spec_from_file_location(name,next((ROOT / 'scripts').rglob(name + '.py')))
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m
class RedirectHistoryTests(unittest.TestCase):

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
