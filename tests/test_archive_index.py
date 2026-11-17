import unittest
import ssl
import urllib.error
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
from sonos_firmware.archive_index import discover_sources, fetch_index


class ArchiveIndexTransportTests(unittest.TestCase):
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

    def test_http_rate_limit_is_not_retried_as_a_tls_problem(self):
        failure = urllib.error.HTTPError('https://web.archive.org/cdx', 429, 'rate limited', {}, None)
        with patch('urllib.request.urlopen', side_effect=failure), \
             patch('curl_cffi.requests.get') as fallback:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                fetch_index('https://web.archive.org/cdx')
        self.assertEqual(raised.exception.code, 429)
        fallback.assert_not_called()

    def test_browser_fallback_preserves_the_index_size_bound(self):
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = iter([b'x' * 2_000_001])
        with patch('urllib.request.urlopen', side_effect=ssl.SSLError('fixture TLS failure')), \
             patch('curl_cffi.requests.get', return_value=response):
            with self.assertRaisesRegex(ValueError, 'exceeds 2 MB'):
                fetch_index('https://web.archive.org/cdx')
        response.close.assert_called_once()


class ArchiveIndexTests(unittest.TestCase):
    def test_installer_suffixes_are_case_insensitive_without_changing_source_urls(self):
        url = 'https://update-software.sonos.com/software/Sonos.EXE'
        result = discover_sources({}, pages=1, roots={'software': 'update-software.sonos.com/'},
            extensions=('exe',), allowed=lambda source: urlsplit(source).hostname == 'update-software.sonos.com',
            fetch=lambda request: [['timestamp', 'original', 'digest'], ['20200101000000', url, 'capture']])
        self.assertIn(url, result['artifacts'])
        self.assertIn('%3Fi%3A', result['observations'][0]['url'])

    def test_installer_index_uses_the_same_capture_and_pagination_contract(self):
        root = 'update-software.sonos.com/software/'
        url = 'https://'+root+'Sonos.exe'
        def allowed(source):
            return source.startswith('https://'+root)
        def fetch(request):
            return [['timestamp', 'original', 'digest'], ['20200101000000', url, 'old'],
                    ['20210101000000', url, 'new'],
                    ['20200101000000', 'https://outside.test/software/Sonos.exe', 'outside'], [], ['resume']]
        result = discover_sources({}, fetch=fetch, pages=1, roots={'software': root},
                                  extensions=('exe', 'dmg'), allowed=allowed)
        self.assertEqual(set(result['artifacts']), {url})
        self.assertEqual(len(result['artifacts'][url]['captures']), 2)
        self.assertEqual(result['cursors'], {'software': 'resume'})
        self.assertEqual(result['status'], 'partial')

    def test_changed_index_query_resets_cursors_without_erasing_captures(self):
        root = 'update-software.sonos.com/software/'
        url = 'https://'+root+'Sonos.exe'
        allowed = lambda source: urlsplit(source).hostname == 'update-software.sonos.com'
        first = discover_sources({}, pages=1, roots={'software': root}, extensions=('exe',), allowed=allowed,
            fetch=lambda request: [['timestamp', 'original', 'digest'], ['20200101000000', url, 'old'], [], ['resume']])
        requests = []
        second = discover_sources(first, pages=1, roots={'software': root}, extensions=('exe', 'dmg'), allowed=allowed,
                                  fetch=lambda request: requests.append(request) or [])
        self.assertNotIn('resumeKey=', requests[0])
        self.assertIn(url, second['artifacts'])
        self.assertNotEqual(first['query_revision'], second['query_revision'])

    def test_direct_packages_and_static_capture_variants_are_discovered_without_manifests(self):
        rows = [['timestamp', 'original', 'digest'],
                ['20200101000000', 'http://update.sonos.com/firmware/history/1.2-345-1-17.upd', 'first'],
                ['20200101000000', 'http://update.sonos.com/releases/static.upd', 'old'],
                ['20210101000000', 'http://update.sonos.com/releases/static.upd', 'new']]
        result = discover_sources({}, fetch=lambda url: rows, pages=1)
        known = result['artifacts']['https://update.sonos.com/firmware/history/1.2-345-1-17.upd']
        self.assertEqual((known['version'], known['package_model']), ('1.2-345', 17))
        static = result['artifacts']['https://update.sonos.com/releases/static.upd']
        self.assertIsNone(static['package_model'])
        self.assertEqual(len(static['captures']), 2)
        self.assertEqual({row['original_url'] for row in static['captures'].values()},
                         {'http://update.sonos.com/releases/static.upd'})
        self.assertIn('collapse=digest', result['observations'][0]['url'])
        self.assertNotIn('collapse=urlkey', result['observations'][0]['url'])
        repeated = discover_sources(result, fetch=lambda url: rows, pages=1)
        self.assertEqual(len(repeated['artifacts']['https://update.sonos.com/releases/static.upd']['captures']), 2)

    def test_query_upgrade_resets_manifest_only_cursors_but_keeps_observations(self):
        previous = {'entries': {'https://update.sonos.com/firmware/old/update.upm': {'digest': 'old'}},
                    'cursors': {'update.sonos.com': 'old-query-token'}}
        urls = []
        result = discover_sources(previous, fetch=lambda url: urls.append(url) or [], pages=1)
        self.assertNotIn('resumeKey=', urls[0])
        self.assertEqual(result['entries'][next(iter(previous['entries']))]['digest'], 'old')

    def test_legacy_capture_survives_a_new_capture_of_the_same_manifest_url(self):
        url = 'https://update.sonos.com/firmware/update.upm'
        previous = {'entries': {url: {'timestamp': '20100101000000', 'digest': 'old'}}}
        result = discover_sources(previous, pages=1, fetch=lambda request: [
            ['timestamp', 'original', 'digest'], ['20200101000000', url, 'new']])
        self.assertEqual({row['digest'] for row in result['entries'][url]['captures'].values()}, {'old', 'new'})

    def test_unfinished_pages_keep_domain_discovery_partial(self):
        result = discover_sources({}, pages=1, fetch=lambda url: [
            ['timestamp', 'original', 'digest'], [], ['resume']])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['pending_hosts'], ['sonos.com'])
        requests = []
        result = discover_sources(result, pages=5, fetch=lambda url: requests.append(url) or [])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['pending_hosts'], [])
        self.assertEqual(len(requests), 1)
        self.assertIn('resumeKey=resume', requests[0])

    def test_malformed_rows_do_not_poison_the_rest_of_a_page(self):
        result = discover_sources({}, pages=1, fetch=lambda url: [
            ['timestamp', 'original', 'digest'], ['malformed'],
            ['20200101000000', 'https://update.sonos.com/firmware/latest.upd', 'valid']])
        self.assertIn('https://update.sonos.com/firmware/latest.upd', result['artifacts'])
        self.assertEqual(len(result['errors']), 1)
    def test_discovers_only_official_manifest_paths_and_keeps_resume_keys(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return [['timestamp', 'original', 'digest'],
                    ['20200101000000', 'http://update.sonos.com:80/firmware/Prod/history/update.upm', 'digest'],
                    ['20200101000000', 'https://outside.test/update.upm', 'bad'], [], ['next page']]
        result = discover_sources({}, fetch=fetch, pages=1)
        self.assertEqual(list(result['entries']), ['https://update.sonos.com/firmware/Prod/history/update.upm'])
        self.assertEqual(result['cursors']['sonos.com'], 'next page')
        self.assertEqual(result['next_host'], 0)
        self.assertIn('showResumeKey=true', seen[0])

    def test_failed_index_preserves_existing_discoveries(self):
        def fetch(url): raise TimeoutError('unavailable')
        previous = discover_sources({}, fetch=lambda url: [
            ['timestamp', 'original', 'digest'],
            ['20200101000000', 'https://update.sonos.com/firmware/old/update.upm', 'known'], [], ['resume']], pages=1)
        result = discover_sources(previous, fetch=fetch, pages=1)
        self.assertEqual(result['entries'][next(iter(previous['entries']))]['digest'], 'known')
        self.assertEqual(result['cursors'], previous['cursors'])
        self.assertEqual(len(result['errors']), 1)

    def test_encoded_cdx_resume_key_is_not_double_encoded(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return []
        previous = discover_sources({}, fetch=lambda url: [
            ['timestamp', 'original', 'digest'], [], ['org%2Carchive%29%2F+19980109140106%21']], pages=1)
        discover_sources(previous, fetch=fetch, pages=1)
        self.assertIn('resumeKey=org%2Carchive%29%2F+19980109140106%21', urls[0])
        self.assertNotIn('%252C', urls[0])


if __name__ == '__main__':
    unittest.main()
