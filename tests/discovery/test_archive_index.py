import unittest
import ssl
import urllib.error
from datetime import timedelta
from tests.apps.test_app_archive import NOW
from sonos_firmware import retry
from unittest.mock import Mock, patch
from urllib.parse import urlsplit
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
    def test_adding_a_query_keeps_an_existing_query_resume_key(self):
        from urllib.parse import parse_qs
        roots = {'software': 'update-software.sonos.com/software/',
                 'unvisited': 'update.sonos.com/unvisited/'}
        first = scan_index({}, roots=roots, requests=1, extensions=('exe',),
            fetch=lambda url: [['timestamp', 'original', 'digest'], [], ['resume']])
        first['next_host'] = 0
        for checkpoint in (first, {key: value for key, value in first.items() if key != 'query_specs'}):
            requests = []
            second = scan_index(checkpoint, roots={**roots, 'firmware': 'update.sonos.com/firmware/'},
                requests=1, extensions=('exe',), fetch=lambda url: requests.append(url) or [])
            self.assertEqual(parse_qs(urlsplit(requests[0]).query)['resumeKey'], ['resume'])
            self.assertIn('firmware', second['pending_hosts'])

    def test_paused_query_does_not_consume_the_request_budget(self):
        roots = {'software': 'update-software.sonos.com/software/', 'firmware': 'update.sonos.com/firmware/'}
        with patch.object(retry, '_now', return_value=NOW):
            first = scan_index({}, roots=roots, requests=1,
                fetch=Mock(side_effect=TimeoutError('temporary')))
            first['next_host'] = 0
            requests = []
            second = scan_index(first, roots=roots, requests=1,
                fetch=lambda url: requests.append(url) or [])
        self.assertEqual(len(requests), 1)
        self.assertIn('firmware', requests[0])
        self.assertIn('firmware', second['completed_hosts'])

    def test_query_changes_do_not_bypass_provider_retry_after(self):
        with patch.object(retry, '_now', return_value=NOW):
            first = scan_index({}, requests=1, fetch=Mock(side_effect=urllib.error.HTTPError(
                'https://web.archive.org/cdx', 429, 'later', {'Retry-After': '7200'}, None)))
            fetch = Mock(return_value=[])
            second = scan_index(first, requests=1, extensions=('upm', 'upd', 'bin'), fetch=fetch)
        fetch.assert_not_called()
        self.assertEqual(second['index_retry'], first['index_retry'])

    def test_changed_filters_preserve_same_source_retry_deadline(self):
        with patch.object(retry, '_now', return_value=NOW):
            first = scan_index({}, requests=1, fetch=Mock(side_effect=urllib.error.HTTPError(
                'https://web.archive.org/cdx', 503, 'later', {'Retry-After': '7200'}, None)))
            fetch = Mock(return_value=[])
            second = scan_index(first, requests=1, extensions=('upm', 'upd', 'bin'), fetch=fetch)
        fetch.assert_not_called()
        self.assertEqual(second['retries'], first['retries'])

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
    def test_identical_bodies_at_different_origins_and_changed_static_contents_survive_index_query(self):
        from urllib.parse import parse_qs
        first = 'https://update.sonos.com/firmware/a/update.upm'
        second = 'https://update.sonos.com/firmware/b/update.upm'
        third = 'https://update.sonos.com/firmware/c/update.upm'
        captures = [
            ['20200101000000', first, 'same-body'],
            ['20210101000000', second, 'same-body'],
            ['20220101000000', second, 'changed-body'],
            ['20220101000000', third, 'changed-body']]
        def index(url):
            # Model CDX's adjacent-field collapsing: the second origin is
            # lost when only the digest participates in the server filter.
            query = parse_qs(urlsplit(url).query)
            rows = []
            for row in captures:
                if query.get('collapse') == ['digest'] and rows and rows[-1][2] == row[2]:
                    continue
                rows.append(row)
            return [['timestamp', 'original', 'digest'], *rows]
        result = scan_index({}, fetch=index, requests=1)
        self.assertEqual(set(result['entries']), {first, second, third})
        self.assertEqual(len(result['entries'][second]['captures']), 2)

    def test_collapsed_legacy_cursor_resets_but_observations_and_retry_deadline_survive(self):
        import hashlib
        import json
        roots = {'sonos.com': 'sonos.com/'}
        suffixes = '(?i:upm|upd)'
        old = hashlib.sha256(json.dumps([list(roots.items()), suffixes, 'domain']).encode()).hexdigest()
        old_spec = hashlib.sha256(json.dumps(['sonos.com/', suffixes, 'domain']).encode()).hexdigest()
        first = 'https://update.sonos.com/firmware/old/update.upm'
        with patch.object(retry, '_now', return_value=NOW):
            first_state = scan_index({}, fetch=lambda url: [
                ['timestamp', 'original', 'digest'], ['20200101000000', first, 'same'], [], ['legacy-resume']])
            first_state.update(query_revision=old, query_specs={'sonos.com': old_spec})
            failure = urllib.error.HTTPError('https://web.archive.org/cdx', 429, 'later',
                                            {'Retry-After': '7200'}, None)
            first_state['index_retry'] = retry.error_checkpoint(failure)
            fetch = Mock(return_value=[])
            paused = scan_index(first_state, fetch=fetch)
            fetch.assert_not_called()
            self.assertEqual(paused['index_retry'], first_state['index_retry'])
            self.assertIn(first, paused['entries'])
        with patch.object(retry, '_now', return_value=NOW+timedelta(hours=3)):
            resumed = scan_index(paused, fetch=fetch)
        self.assertNotIn('resumeKey=', fetch.call_args.args[0])
        self.assertIn(first, resumed['entries'])

    def test_rate_limit_preserves_cursor_and_honors_retry_after_across_runs(self):
        with patch.object(retry, '_now', return_value=NOW):
            first = scan_index({}, requests=1, fetch=lambda url: [
                ['timestamp', 'original', 'digest'], [], ['saved-page']])
            def limited(url):
                raise urllib.error.HTTPError(url, 429, 'slow down', {'Retry-After': '75'}, None)
            failed = scan_index(first, fetch=limited, requests=1)
        with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=74)), \
             patch('urllib.request.urlopen') as network:
            waiting = scan_index(failed, fetch=lambda url: self.fail('retried before Retry-After'), requests=1)
        self.assertEqual(waiting['status'], 'partial')
        self.assertEqual(waiting['cursors'], first['cursors'])
        requests = []
        with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=76)):
            recovered = scan_index(waiting, fetch=lambda url: requests.append(url) or [], requests=1)
        self.assertEqual(recovered['status'], 'ok')
        self.assertIn('resumeKey=saved-page', requests[0])
        self.assertEqual(recovered['errors'], [])

    def test_retry_after_http_date_and_invalid_value_use_durable_backoff(self):
        from email.utils import format_datetime
        for value, expected in ((format_datetime(NOW+timedelta(days=2)), NOW+timedelta(days=2)),
                                ('invalid', NOW+timedelta(hours=1))):
            with self.subTest(value=value):
                error = urllib.error.HTTPError('https://web.archive.org/', 429, 'retry later', {'Retry-After': value}, None)
                record = retry.error_checkpoint(error, now=NOW)
                self.assertEqual(record['next_attempt_at'], expected.isoformat())

    def test_installer_suffixes_are_case_insensitive_without_changing_source_urls(self):
        url = 'https://update-software.sonos.com/software/Sonos.EXE'
        result = scan_index({}, requests=1, roots={'software': 'update-software.sonos.com/'},
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
        result = scan_index({}, fetch=fetch, requests=1, roots={'software': root},
                                  extensions=('exe', 'dmg'), allowed=allowed)
        self.assertEqual(set(result['artifacts']), {url})
        self.assertEqual(len(result['artifacts'][url]['captures']), 2)
        self.assertEqual(result['cursors'], {'software': 'resume'})
        self.assertEqual(result['status'], 'partial')

    def test_changed_index_query_resets_cursors_without_erasing_captures(self):
        root = 'update-software.sonos.com/software/'
        url = 'https://'+root+'Sonos.exe'
        allowed = lambda source: urlsplit(source).hostname == 'update-software.sonos.com'
        first = scan_index({}, requests=1, roots={'software': root}, extensions=('exe',), allowed=allowed,
            fetch=lambda request: [['timestamp', 'original', 'digest'], ['20200101000000', url, 'old'], [], ['resume']])
        requests = []
        second = scan_index(first, requests=1, roots={'software': root}, extensions=('exe', 'dmg'), allowed=allowed,
                                  fetch=lambda request: requests.append(request) or [])
        self.assertNotIn('resumeKey=', requests[0])
        self.assertIn(url, second['artifacts'])
        self.assertNotEqual(first['query_revision'], second['query_revision'])

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

    def test_query_upgrade_resets_manifest_only_cursors_but_keeps_observations(self):
        previous = {'entries': {'https://update.sonos.com/firmware/old/update.upm': {'digest': 'old'}},
                    'cursors': {'update.sonos.com': 'old-query-token'}}
        urls = []
        result = scan_index(previous, fetch=lambda url: urls.append(url) or [], requests=1)
        self.assertNotIn('resumeKey=', urls[0])
        self.assertEqual(result['entries'][next(iter(previous['entries']))]['digest'], 'old')

    def test_legacy_capture_survives_a_new_capture_of_the_same_manifest_url(self):
        url = 'https://update.sonos.com/firmware/update.upm'
        previous = {'entries': {url: {'timestamp': '20100101000000', 'digest': 'old'}}}
        result = scan_index(previous, requests=1, fetch=lambda request: [
            ['timestamp', 'original', 'digest'], ['20200101000000', url, 'new']])
        self.assertEqual({row['digest'] for row in result['entries'][url]['captures'].values()}, {'old', 'new'})

    def test_unfinished_pages_keep_domain_discovery_partial(self):
        result = scan_index({}, requests=1, fetch=lambda url: [
            ['timestamp', 'original', 'digest'], [], ['resume']])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['pending_hosts'], ['sonos.com'])
        requests = []
        result = scan_index(result, requests=5, fetch=lambda url: requests.append(url) or [])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['pending_hosts'], [])
        self.assertEqual(len(requests), 1)
        self.assertIn('resumeKey=resume', requests[0])

    def test_malformed_rows_do_not_poison_the_rest_of_a_page(self):
        result = scan_index({}, requests=1, fetch=lambda url: [
            ['timestamp', 'original', 'digest'], ['malformed'],
            ['20200101000000', 'https://update.sonos.com/firmware/latest.upd', 'valid']])
        self.assertIn('https://update.sonos.com/firmware/latest.upd', result['artifacts'])
        self.assertEqual(len(result['errors']), 1)

    def test_malformed_page_retains_its_cursor_until_fixed_without_losing_valid_rows(self):
        source = 'https://update.sonos.com/firmware/history/update.upm'
        with patch.object(retry, '_now', return_value=NOW):
            first = scan_index({}, requests=1, fetch=lambda url: [
                ['timestamp', 'original', 'digest'], [], ['first-page']])
            failed = scan_index(first, requests=1, fetch=lambda url: [
                ['timestamp', 'original', 'digest'], ['malformed'],
                ['20200101000000', source, 'body'], [], ['second-page']])
        self.assertEqual(failed['cursors'], first['cursors'])
        self.assertIn(source, failed['entries'])
        self.assertTrue(failed['pending_hosts'])
        fetch = Mock(return_value=[])
        with patch.object(retry, '_now', return_value=NOW+timedelta(hours=2)):
            resumed = scan_index(failed, fetch=fetch, requests=1)
        self.assertIn('resumeKey=first-page', fetch.call_args.args[0])
        self.assertEqual(resumed['status'], 'ok')

    def test_index_http_error_response_is_closed_after_retry_timing_is_saved(self):
        import io
        body = io.BytesIO(b'fixture response')
        error = urllib.error.HTTPError('https://web.archive.org/cdx', 503, 'later',
                                       {'Retry-After': '7200'}, body)
        with patch.object(retry, '_now', return_value=NOW):
            result = scan_index({}, requests=1, fetch=Mock(side_effect=error))
        self.assertTrue(body.closed)
        self.assertEqual(result['retries']['sonos.com']['next_attempt_at'],
                         (NOW+timedelta(hours=2)).isoformat())
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

    def test_failed_index_preserves_existing_discoveries(self):
        def fetch(url): raise TimeoutError('unavailable')
        previous = scan_index({}, fetch=lambda url: [
            ['timestamp', 'original', 'digest'],
            ['20200101000000', 'https://update.sonos.com/firmware/old/update.upm', 'known'], [], ['resume']], requests=1)
        result = scan_index(previous, fetch=fetch, requests=1)
        self.assertEqual(result['entries'][next(iter(previous['entries']))]['digest'], 'known')
        self.assertEqual(result['cursors'], previous['cursors'])
        self.assertEqual(len(result['errors']), 1)

    def test_encoded_cdx_resume_key_is_not_double_encoded(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return []
        previous = scan_index({}, fetch=lambda url: [
            ['timestamp', 'original', 'digest'], [], ['org%2Carchive%29%2F+19980109140106%21']], requests=1)
        scan_index(previous, fetch=fetch, requests=1)
        self.assertIn('resumeKey=org%2Carchive%29%2F+19980109140106%21', urls[0])
        self.assertNotIn('%252C', urls[0])


if __name__ == '__main__':
    unittest.main()
