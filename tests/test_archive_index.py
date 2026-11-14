import unittest
from sonos_firmware.archive_index import manifest_roots


class ArchiveIndexTests(unittest.TestCase):
    def test_discovers_only_official_manifest_paths_and_keeps_resume_keys(self):
        seen = []
        def fetch(url):
            seen.append(url)
            return [['timestamp', 'original', 'digest'],
                    ['20200101000000', 'http://update.sonos.com:80/firmware/Prod/history/update.upm', 'digest'],
                    ['20200101000000', 'https://outside.test/update.upm', 'bad'], [], ['next page']]
        result = manifest_roots({}, fetch=fetch, pages=1)
        self.assertEqual(list(result['entries']), ['https://update.sonos.com/firmware/Prod/history/update.upm'])
        self.assertEqual(result['cursors']['update.sonos.com'], 'next page')
        self.assertEqual(result['next_host'], 1)
        self.assertIn('showResumeKey=true', seen[0])

    def test_failed_index_preserves_existing_discoveries(self):
        def fetch(url): raise TimeoutError('unavailable')
        previous = {'entries': {'https://update.sonos.com/firmware/old/update.upm': {'digest': 'known'}},
                    'cursors': {'update.sonos.com': 'resume'}, 'next_host': 0}
        result = manifest_roots(previous, fetch=fetch, pages=1)
        self.assertEqual(result['entries'], previous['entries'])
        self.assertEqual(result['cursors'], previous['cursors'])
        self.assertEqual(len(result['errors']), 1)

    def test_encoded_cdx_resume_key_is_not_double_encoded(self):
        urls = []
        def fetch(url):
            urls.append(url)
            return []
        manifest_roots({'cursors': {'update.sonos.com': 'org%2Carchive%29%2F+19980109140106%21'}}, fetch=fetch, pages=1)
        self.assertIn('resumeKey=org%2Carchive%29%2F+19980109140106%21', urls[0])
        self.assertNotIn('%252C', urls[0])


if __name__ == '__main__':
    unittest.main()
