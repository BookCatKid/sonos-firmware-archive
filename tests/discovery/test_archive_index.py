import unittest
import ssl
import urllib.error
from datetime import timedelta
from tests.apps.test_app_archive import NOW
from sonos_firmware import retry
from unittest.mock import Mock, patch
from sonos_firmware.archive_index import discover_sources as production_discover, fetch_index


def scan_index(previous, fetch=fetch_index, *, requests=None, **kwargs):
    if requests is None:
        return production_discover(previous, fetch=fetch, **kwargs)
    attempted = 0
    def fetch_page(url):
        nonlocal attempted
        attempted += 1
        return fetch(url)
    with patch('sonos_firmware.archive_index.winding_down', side_effect=lambda: attempted >= requests):
        return production_discover(previous, fetch=fetch_page, **kwargs)


class ArchiveIndexTransportTests(unittest.TestCase):


    def test_query_changes_do_not_bypass_provider_retry_after(self):
        with patch.object(retry, '_now', return_value=NOW):
            first = scan_index({}, requests=1, fetch=Mock(side_effect=urllib.error.HTTPError(
                'https://web.archive.org/cdx', 429, 'later', {'Retry-After': '7200'}, None)))
            fetch = Mock(return_value=[])
            second = scan_index(first, requests=1, extensions=('upm', 'upd', 'bin'), fetch=fetch)
        fetch.assert_not_called()
        self.assertEqual(second['index_retry'], first['index_retry'])


    def test_certificate_failure_uses_verified_browser_transport(self):
        failure = urllib.error.URLError(ssl.SSLCertVerificationError('fixture certificate chain'))
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = iter([b'[["timestamp","original","digest"]]'])
        with patch('urllib.request.urlopen', side_effect=failure), \
             patch('curl_cffi.requests.get', return_value=response) as fallback:
            self.assertEqual(fetch_index('https://web.archive.org/cdx/search/cdx?fixture'),
                             [['timestamp', 'original', 'digest']])
        self.assertNotEqual(fallback.call_args.kwargs.get('verify'), False)
        response.close.assert_called_once()


    def test_browser_fallback_accepts_large_valid_index_pages(self):
        response = Mock(status_code=200, headers={})
        import json
        rows = [['timestamp', 'original', 'digest'], *[['20200101000000', 'https://update.sonos.com/a.upd', 'sha'] for _ in range(30000)]]
        response.iter_content.return_value = iter([json.dumps(rows).encode()])
        with patch('urllib.request.urlopen', side_effect=ssl.SSLError('fixture TLS failure')), \
             patch('curl_cffi.requests.get', return_value=response):
            self.assertEqual(fetch_index('https://web.archive.org/cdx'), rows)
        response.close.assert_called_once()


class ArchiveIndexTests(unittest.TestCase):


    def test_rate_limit_preserves_cursor_and_honors_retry_after_across_runs(self):
        with patch.object(retry, '_now', return_value=NOW):
            first = scan_index({}, requests=1, fetch=lambda url: [
                ['timestamp', 'original', 'digest'], [], ['saved-page']])
            def limited(url):
                raise urllib.error.HTTPError(url, 429, 'slow down', {'Retry-After': '75'}, None)
            failed = scan_index(first, fetch=limited, requests=1)
        with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=74)), \
             patch('urllib.request.urlopen'):
            waiting = scan_index(failed, fetch=lambda url: self.fail('retried before Retry-After'), requests=1)
        self.assertEqual(waiting['status'], 'partial')
        self.assertEqual(waiting['cursors'], first['cursors'])
        requests = []
        with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=76)):
            recovered = scan_index(waiting, fetch=lambda url: requests.append(url) or [], requests=1)
        self.assertEqual(recovered['status'], 'ok')
        self.assertIn('resumeKey=saved-page', requests[0])
        self.assertEqual(recovered['errors'], [])


    def test_direct_packages_and_static_capture_variants_are_discovered_without_manifests(self):
        rows = [['timestamp', 'original', 'digest'],
                ['20200101000000', 'http://update.sonos.com/firmware/history/1.2-345-1-17.upd', 'first'],
                ['20200101000000', 'http://update.sonos.com/releases/static.upd', 'old'],
                ['20210101000000', 'http://update.sonos.com/releases/static.upd', 'new']]
        result = scan_index({}, fetch=lambda url: rows, requests=1)
        known = result['artifacts']['https://update.sonos.com/firmware/history/1.2-345-1-17.upd']
        self.assertEqual((known['version'], known['package_model']), ('1.2-345', 17))
        static = result['artifacts']['https://update.sonos.com/releases/static.upd']
        self.assertIsNone(static['package_model'])
        self.assertEqual(len(static['captures']), 2)
        self.assertEqual({row['original_url'] for row in static['captures'].values()},
                         {'http://update.sonos.com/releases/static.upd'})
        self.assertNotIn('collapse=digest', result['observations'][0]['url'])
        self.assertNotIn('collapse=urlkey', result['observations'][0]['url'])
        repeated = scan_index(result, fetch=lambda url: rows, requests=1)
        self.assertEqual(len(repeated['artifacts']['https://update.sonos.com/releases/static.upd']['captures']), 2)


    def test_discovers_only_official_manifest_paths_and_keeps_resume_keys(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return [['timestamp', 'original', 'digest'],
                    ['20200101000000', 'http://update.sonos.com:80/firmware/Prod/history/update.upm', 'digest'],
                    ['20200101000000', 'https://outside.test/update.upm', 'bad'], [], ['next page']]
        result = scan_index({}, fetch=fetch, requests=1)
        self.assertEqual(list(result['entries']), ['https://update.sonos.com/firmware/Prod/history/update.upm'])
        self.assertEqual(result['cursors']['sonos.com'], 'next page')
        self.assertEqual(result['next_host'], 0)
        self.assertIn('showResumeKey=true', seen[0])


if __name__ == '__main__':
    unittest.main()
