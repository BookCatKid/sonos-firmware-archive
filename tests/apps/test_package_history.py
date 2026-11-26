"""Git history discovers original installer URLs even after metadata deletion."""
import base64
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from sonos_firmware import retry
from sonos_firmware.package_history import ManifestHistory, REPOSITORY, VENDOR_PATH

PREFIX = 'repos/'+REPOSITORY
URL = 'https://update-software.sonos.com/software/pc/dcr/Sonos_17.1.exe'


def tree(*entries, truncated=False):
    return {'truncated': truncated, 'tree': [dict(path=path, sha=sha, type=kind) for path, sha, kind in entries]}


def commit(sha, root, parents=()):
    return {'sha': sha, 'commit': {'tree': {'sha': root}}, 'parents': [{'sha': parent} for parent in parents]}


def blob(text):
    return {'encoding': 'base64', 'content': base64.b64encode(text.encode()).decode()}


class ManifestHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.pages = {(VENDOR_PATH, 1): [commit('deleted', 'after-root', ['before-commit'])]}
        self.objects = {
            '/git/trees/after-root': tree(('manifests', 'after-manifests', 'tree')),
            '/git/trees/after-manifests': tree(('s', 'after-s', 'tree')),
            '/git/trees/after-s': tree(),
            '/git/commits/before-commit': {'tree': {'sha': 'before-root'}},
            '/git/trees/before-root': tree(('manifests', 'before-manifests', 'tree')),
            '/git/trees/before-manifests': tree(('s', 'before-s', 'tree')),
            '/git/trees/before-s': tree(('Sonos', 'vendor', 'tree')),
            '/git/trees/vendor': tree(('Controller', 'package', 'tree')),
            '/git/trees/package': tree(('17.1', 'version', 'tree')),
            '/git/trees/version': tree(('Sonos.installer.yaml', 'installer', 'blob')),
            '/git/blobs/installer': blob('PackageVersion: 17.1\nInstallers:\n  - InstallerUrl: '+URL+
                                        '\n    InstallerSha256: '+('a'*64)+'\n'),
        }
        self.requests = []

    def fetch(self, endpoint):
        self.requests.append(endpoint)
        if endpoint.startswith(PREFIX+'/commits?'):
            query = parse_qs(urlsplit(endpoint).query)
            return self.pages.get((query['path'][0], int(query['page'][0])), [])
        return self.objects[endpoint.removeprefix(PREFIX)]

    def history(self, **kwargs):
        return ManifestHistory(self.root/'history.json', self.fetch, **kwargs)

    def test_parent_snapshot_recovers_a_manifest_absent_from_current_tree(self):
        history = self.history()
        rows = history.advance()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], URL)
        self.assertEqual(rows[0]['expected_sha256'], 'a'*64)
        self.assertIn('before-commit', ' '.join(self.requests))
        self.assertEqual(history.state['status'], 'ok')
        self.assertFalse(any('releases' in endpoint for endpoint in self.requests))


    def test_complete_history_scans_all_roots_and_preserves_manifest(self):
        self.pages[(VENDOR_PATH, 1)] = [commit('useful', 'before-root')]
        for index in range(40):
            sha = 'empty-root-'+str(index)
            self.pages[(VENDOR_PATH, 1)].append(commit('other-'+str(index), sha))
            self.objects['/git/trees/'+sha] = tree()
        history = self.history()
        self.assertEqual({row['url'] for row in history.advance()}, {URL})
        self.assertEqual(history.state['pending_total'], 0)

    def test_commit_pages_resume_past_one_hundred_without_requiring_existing_candidates(self):
        self.pages[(VENDOR_PATH, 1)] = [commit('c'+str(n), 'after-root') for n in range(100)]
        self.pages[(VENDOR_PATH, 2)] = [commit('old', 'before-root')]
        history = self.history()
        history.advance()
        self.assertEqual(history.state['paths'][VENDOR_PATH]['next_page'], 2)
        restarted = self.history()
        restarted.advance()
        self.assertTrue(restarted.state['paths'][VENDOR_PATH]['complete'])
        self.assertEqual({row['url'] for row in restarted.advance()}, {URL})


    def test_immutable_cache_supports_parser_replay_during_a_source_outage(self):
        observations = self.root/'observations'
        history = self.history(observations=observations)
        history.advance()
        history.state['parser_revision'] = 'previous-parser'
        history.save()
        self.requests.clear()
        with patch.object(self, 'fetch', side_effect=AssertionError('all immutable metadata is cached')):
            restarted = self.history(observations=observations)
            self.assertEqual({row['url'] for row in restarted.advance()}, {URL})
        self.assertEqual(restarted.state['status'], 'ok')

    def test_truncated_tree_is_retried_instead_of_becoming_a_permanent_cached_result(self):
        observations = self.root/'observations'
        self.objects['/git/trees/vendor'] = tree(truncated=True)
        history = self.history(observations=observations)
        self.assertEqual(history.advance(), [])
        self.assertTrue(history.state['errors'])
        self.objects['/git/trees/vendor'] = tree(('Controller', 'package', 'tree'))
        later = datetime.now(timezone.utc)+timedelta(hours=2)
        with patch.object(retry, '_now', return_value=later):
            rows = self.history(observations=observations).advance()
        self.assertEqual({row['url'] for row in rows}, {URL})


if __name__ == '__main__':
    unittest.main()
