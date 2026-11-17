"""Git history discovers original installer URLs even after metadata deletion."""
import base64
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from sonos_firmware import package_history, retry
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
        rows = history.advance(limit=64)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['url'], URL)
        self.assertEqual(rows[0]['expected_sha256'], 'a'*64)
        self.assertIn('before-commit', ' '.join(self.requests))
        self.assertEqual(history.state['status'], 'ok')
        self.assertFalse(any('releases' in endpoint for endpoint in self.requests))

    def test_budget_and_restart_preserve_unattempted_git_objects(self):
        first = self.history()
        self.assertEqual(first.advance(limit=2), [])
        self.assertGreater(first.state['pending_total'], 0)
        rows = self.history().advance(limit=64)
        self.assertEqual({row['url'] for row in rows}, {URL})

    def test_small_budget_descends_to_manifests_before_opening_every_other_root(self):
        self.pages[(VENDOR_PATH, 1)] = [commit('useful', 'before-root')]
        for index in range(40):
            sha = 'empty-root-'+str(index)
            self.pages[(VENDOR_PATH, 1)].append(commit('other-'+str(index), sha))
            self.objects['/git/trees/'+sha] = tree()
        history = self.history()
        self.assertEqual({row['url'] for row in history.advance(limit=12)}, {URL})
        self.assertGreater(history.state['pending_total'], 0)

    def test_commit_pages_resume_past_one_hundred_without_requiring_existing_candidates(self):
        self.pages[(VENDOR_PATH, 1)] = [commit('c'+str(n), 'after-root') for n in range(100)]
        self.pages[(VENDOR_PATH, 2)] = [commit('old', 'before-root')]
        history = self.history()
        history.advance(limit=1)
        self.assertEqual(history.state['paths'][VENDOR_PATH]['next_page'], 2)
        restarted = self.history()
        restarted.advance(limit=1)
        self.assertTrue(restarted.state['paths'][VENDOR_PATH]['complete'])
        self.assertEqual({row['url'] for row in restarted.advance(limit=64)}, {URL})

    def test_head_refresh_stops_at_the_old_anchor_not_a_duplicate_from_the_current_scan(self):
        self.pages[(VENDOR_PATH, 1)] = [commit('anchor', 'after-root')]
        history = self.history()
        history.advance(limit=64)
        self.pages[(VENDOR_PATH, 1)] = [commit('new'+str(n), 'after-root') for n in range(100)]
        self.pages[(VENDOR_PATH, 2)] = [commit('new99', 'after-root'), commit('new100', 'before-root'),
                                          commit('anchor', 'after-root')]
        later = datetime.now(timezone.utc)+timedelta(hours=7)
        with patch.object(retry, '_now', return_value=later):
            history.advance(limit=1)
            self.assertEqual(history.state['paths'][VENDOR_PATH]['head_page'], 2)
            history.advance(limit=1)
            self.assertNotIn('head_page', history.state['paths'][VENDOR_PATH])
            rows = history.advance(limit=64)
        self.assertEqual({row['url'] for row in rows}, {URL})

    def test_pre_alphabetical_layout_is_discovered_and_gets_its_own_history_cursor(self):
        self.objects['/git/trees/before-manifests'] = tree(('Sonos.Sonos', 'old-package', 'tree'))
        self.objects['/git/trees/old-package'] = tree(('1.0.yaml', 'combined', 'blob'))
        self.objects['/git/blobs/combined'] = blob('Version: 1.0\nInstallerUrl: '+URL+'\n')
        history = self.history()
        rows = history.advance(limit=64)
        self.assertEqual({row['url'] for row in rows}, {URL})
        self.assertIn('manifests/Sonos.Sonos', history.state['paths'])
        self.assertGreater(history.state['pending_total'], 0)
        history.advance(limit=64)
        self.assertEqual(history.state['status'], 'ok')

    def test_immutable_cache_supports_parser_replay_during_a_source_outage(self):
        observations = self.root/'observations'
        history = self.history(observations=observations)
        history.advance(limit=64)
        history.state['parser_revision'] = 'previous-parser'
        history.save()
        self.requests.clear()
        with patch.object(self, 'fetch', side_effect=AssertionError('all immutable metadata is cached')):
            restarted = self.history(observations=observations)
            self.assertEqual({row['url'] for row in restarted.advance(limit=64)}, {URL})
        self.assertEqual(restarted.state['status'], 'ok')

    def test_truncated_tree_is_retried_instead_of_becoming_a_permanent_cached_result(self):
        observations = self.root/'observations'
        self.objects['/git/trees/vendor'] = tree(truncated=True)
        history = self.history(observations=observations)
        self.assertEqual(history.advance(limit=64), [])
        self.assertTrue(history.state['errors'])
        self.objects['/git/trees/vendor'] = tree(('Controller', 'package', 'tree'))
        later = datetime.now(timezone.utc)+timedelta(hours=2)
        with patch.object(retry, '_now', return_value=later):
            rows = self.history(observations=observations).advance(limit=64)
        self.assertEqual({row['url'] for row in rows}, {URL})

    def test_partial_installer_records_keep_valid_urls_and_retry_the_bad_metadata(self):
        self.objects['/git/blobs/installer'] = blob('Installers:\n - InstallerUrl: '+URL+
            '\n   InstallerSha256: invalid\n - InstallerUrl: https://update.sonos.com/second.exe\n')
        history = self.history()
        rows = history.advance(limit=64)
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['expected_sha256'] is None for row in rows))
        self.assertEqual(history.state['status'], 'partial')
        self.assertTrue(history.state['errors'])

    def test_metadata_listing_outage_retains_previously_discovered_candidates(self):
        history = self.history()
        history.advance(limit=64)
        later = datetime.now(timezone.utc)+timedelta(hours=7)
        with patch.object(retry, '_now', return_value=later), patch.object(history, 'fetch', side_effect=OSError('outage')):
            rows = history.advance(limit=64)
        self.assertEqual({row['url'] for row in rows}, {URL})
        self.assertEqual(history.state['status'], 'partial')


if __name__ == '__main__':
    unittest.main()
