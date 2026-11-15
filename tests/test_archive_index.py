import unittest
from sonos_firmware.archive_index import discover_sources


class ArchiveIndexTests(unittest.TestCase):
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

    def test_unfinished_hosts_and_pages_keep_index_discovery_partial(self):
        result = discover_sources({}, pages=1, fetch=lambda url: [])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['pending_hosts']), 5)
        result = discover_sources(result, pages=5, fetch=lambda url: [])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['pending_hosts'], [])

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
        self.assertEqual(result['cursors']['update.sonos.com'], 'next page')
        self.assertEqual(result['next_host'], 1)
        self.assertIn('showResumeKey=true', seen[0])

    def test_failed_index_preserves_existing_discoveries(self):
        def fetch(url): raise TimeoutError('unavailable')
        previous = {'schema_version': 2, 'entries': {'https://update.sonos.com/firmware/old/update.upm': {'digest': 'known'}},
                    'cursors': {'update.sonos.com': 'resume'}, 'next_host': 0}
        result = discover_sources(previous, fetch=fetch, pages=1)
        self.assertEqual(result['entries'][next(iter(previous['entries']))]['digest'], 'known')
        self.assertEqual(result['cursors'], previous['cursors'])
        self.assertEqual(len(result['errors']), 1)

    def test_encoded_cdx_resume_key_is_not_double_encoded(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return []
        discover_sources({'schema_version': 2, 'cursors': {'update.sonos.com': 'org%2Carchive%29%2F+19980109140106%21'}}, fetch=fetch, pages=1)
        self.assertIn('resumeKey=org%2Carchive%29%2F+19980109140106%21', urls[0])
        self.assertNotIn('%252C', urls[0])


if __name__ == '__main__':
    unittest.main()
