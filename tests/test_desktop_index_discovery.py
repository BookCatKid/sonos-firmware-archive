"""Package metadata listings cannot truncate or silently discard installer leads."""
import base64
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch
from sonos_firmware import installers

SPEC = importlib.util.spec_from_file_location('desktop_index',
    Path(__file__).resolve().parents[1]/'scripts/discover_desktop_installers.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
REPOSITORY = 'repos/microsoft/winget-pkgs'


def tree(*rows, truncated=False):
    return {'tree': [{'path': name, 'sha': sha, 'type': kind} for name, sha, kind in rows],
            'truncated': truncated}


def blob(text):
    return {'content': base64.b64encode(text.encode()).decode()}


class DesktopIndexTests(unittest.TestCase):
    def test_github_challenge_pauses_later_requests_without_immediate_retries(self):
        error = subprocess.CalledProcessError(1, ['gh'], stderr='gh: rate limit exceeded (HTTP 403)')
        with patch.object(MODULE, 'GH_SOURCE_PAUSE', None), \
             patch.object(MODULE.subprocess, 'check_output', side_effect=error) as request, \
             patch.object(MODULE.time, 'sleep') as sleep:
            for endpoint in ('first', 'later'):
                with self.assertRaisesRegex(MODULE.GitHubSourcePaused, 'HTTP 403'):
                    MODULE.gh_json(endpoint)
            self.assertEqual(request.call_count, 1)
            sleep.assert_not_called()

    def snapshot(self, versions, documents):
        nodes = {
            REPOSITORY: {'default_branch': 'new-main'},
            REPOSITORY+'/git/trees/new-main': tree(('manifests', 'manifests', 'tree')),
            REPOSITORY+'/git/trees/manifests': tree(('s', 's', 'tree')),
            REPOSITORY+'/git/trees/s': tree(('Sonos', 'vendor', 'tree')),
            REPOSITORY+'/git/trees/vendor': tree(('NewControllerName', 'package', 'tree')),
            REPOSITORY+'/git/trees/package': tree(*[(str(n), 'version-'+str(n), 'tree') for n in versions]),
        }
        for n, document in documents.items():
            nodes[REPOSITORY+'/git/trees/version-'+str(n)] = tree(('Sonos.installer.yaml', 'blob-'+str(n), 'blob'))
            nodes[REPOSITORY+'/git/blobs/blob-'+str(n)] = blob(document)
        return nodes

    def test_git_trees_discover_more_than_1000_versions_and_new_package_names(self):
        versions = range(1001)
        documents = {n: 'Installers:\n  - InstallerUrl: https://new-apps.sonos.com/releases/Sonos_'+str(n)+
                       '.exe\n    InstallerSha256: '+('a'*64)+'\n' for n in versions}
        nodes = self.snapshot(versions, documents)
        requests, candidates = [], {}
        def fetch(endpoint):
            requests.append(endpoint)
            self.assertNotIn('/contents/', endpoint)
            return nodes[endpoint]
        with patch.object(MODULE, 'gh_json', side_effect=fetch):
            self.assertEqual(MODULE.winget_candidates(candidates), [])
        self.assertEqual(len(candidates), 1001)
        self.assertTrue(any(row['version'] == '1000' for row in candidates.values()))
        self.assertIn(REPOSITORY+'/git/trees/new-main', requests)

    def test_yaml_rows_keep_their_own_hashes_and_missing_hashes_remain_usable_leads(self):
        document = '''Installers:
  - InstallerUrl: "https://new-apps.sonos.com/Sonos_1.exe"
    InstallerSha256: 0000000000000000000000000000000000000000000000000000000000000001
  - InstallerUrl: 'https://new-apps.sonos.com/Sonos_2.exe'
  - InstallerUrl: https://new-apps.sonos.com/Sonos_3.exe
    InstallerSha256: invalid
  - InstallerUrl: https://new-apps.sonos.com/Sonos_4.exe
    InstallerSha256: aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa
'''
        nodes = self.snapshot([1], {1: document})
        candidates = {}
        with patch.object(MODULE, 'gh_json', side_effect=lambda endpoint: nodes[endpoint]):
            errors = MODULE.winget_candidates(candidates)
        by_url = {row['url']: row for row in candidates.values()}
        self.assertEqual(len(by_url), 4)
        self.assertEqual(by_url['https://new-apps.sonos.com/Sonos_1.exe']['expected_sha256'], '0'*63+'1')
        self.assertIsNone(by_url['https://new-apps.sonos.com/Sonos_2.exe']['expected_sha256'])
        self.assertIsNone(by_url['https://new-apps.sonos.com/Sonos_3.exe']['expected_sha256'])
        self.assertEqual(by_url['https://new-apps.sonos.com/Sonos_4.exe']['expected_sha256'], 'a'*64)
        self.assertEqual(len(errors), 1)

    def test_different_observed_hashes_at_one_url_keep_separate_acquisition_contexts(self):
        candidates = {}
        for digest in ('a'*64, 'b'*64):
            MODULE.add_candidate(candidates, family='s2', platform='windows', version='1',
                url='https://new-apps.sonos.com/Sonos.exe', sha256=digest, source='fixture:'+digest)
        rows = list(candidates.values())
        self.assertEqual(len(rows), 2)
        self.assertEqual(len({row['acquisition_id'] for row in rows}), 2)
        self.assertEqual({row['expected_sha256'] for row in rows}, {'a'*64, 'b'*64})

    def test_historical_http_urls_upgrade_without_losing_the_advertised_alias(self):
        candidates = {}
        url = 'http://new-apps.sonos.com:80/releases/Sonos_1.exe'
        MODULE.add_candidate(candidates, family='s2', platform='windows', version='1',
            url=url, sha256=None, source='fixture')
        row = next(iter(candidates.values()))
        self.assertEqual(row['url'], 'https://new-apps.sonos.com/releases/Sonos_1.exe')
        self.assertEqual(row['advertised_urls'], [url])

    def test_saved_http_observations_can_still_be_acquired_over_https(self):
        response = io.BytesIO(b'MZ fixture')
        response.headers = {}
        response.geturl = lambda: 'https://new-apps.sonos.com/Sonos.exe'
        with tempfile.TemporaryDirectory() as temporary, patch.object(installers, 'open_advertised', return_value=response) as fetch:
            result = installers.acquire({'url': 'http://new-apps.sonos.com:80/Sonos.exe', 'platform': 'windows'},
                                        Path(temporary)/'installer.exe')
        self.assertEqual(fetch.call_args.args[0].full_url, 'https://new-apps.sonos.com/Sonos.exe')
        self.assertEqual(result, 'https://new-apps.sonos.com/Sonos.exe')

    def test_failed_or_truncated_version_tree_does_not_hide_later_versions(self):
        nodes = self.snapshot([1, 2], {2: 'InstallerUrl: https://new-apps.sonos.com/Sonos_2.exe\n'})
        nodes[REPOSITORY+'/git/trees/version-1'] = tree(truncated=True)
        candidates = {}
        with patch.object(MODULE, 'gh_json', side_effect=lambda endpoint: nodes[endpoint]):
            errors = MODULE.winget_candidates(candidates)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn('incomplete Git tree', errors[0]['error'])

    def test_source_pause_keeps_errors_found_before_the_challenge(self):
        nodes = self.snapshot([1, 2, 3], {3: 'InstallerUrl: https://new-apps.sonos.com/Sonos_3.exe\n'})
        nodes[REPOSITORY+'/git/trees/version-1'] = tree(truncated=True)
        nodes[REPOSITORY+'/git/trees/version-2'] = MODULE.GitHubSourcePaused('HTTP 403')
        requests = []
        def fetch(endpoint):
            requests.append(endpoint)
            result = nodes[endpoint]
            if isinstance(result, Exception):
                raise result
            return result
        with patch.object(MODULE, 'gh_json', side_effect=fetch):
            errors = MODULE.winget_candidates({})
        self.assertEqual(len(errors), 2)
        self.assertIn('incomplete Git tree', errors[0]['error'])
        self.assertIn('HTTP 403', errors[1]['error'])
        self.assertNotIn(REPOSITORY+'/git/trees/version-3', requests)

    def test_homebrew_fetch_errors_are_reported_and_other_commits_still_contribute(self):
        content = ('version "100.2"\nsha256 "'+('a'*64)+'"\n'
                   'url "https://new-apps.sonos.com/Sonos_100.2.dmg"\n').encode()
        candidates = {}
        responses = [urllib.error.URLError('temporary metadata failure'), io.BytesIO(content),
                     urllib.error.HTTPError('url', 404, 'deleted file at this commit', {}, None)]
        with patch.object(MODULE, 'HOMEBREW_PATHS', {'Casks/s/sonos.rb': 's2'}), \
             patch.object(MODULE, 'gh_lines', return_value=['old', 'working', 'deleted']), \
             patch.object(MODULE.urllib.request, 'urlopen', side_effect=responses):
            errors = MODULE.homebrew_candidates(candidates)
        self.assertEqual(len(candidates), 1)
        self.assertEqual(len(errors), 1)
        self.assertIn('temporary metadata failure', errors[0]['error'])

    def test_homebrew_history_listing_failure_does_not_stop_other_paths(self):
        with patch.object(MODULE, 'HOMEBREW_PATHS', {'first.rb': 's1', 'second.rb': 's2'}), \
             patch.object(MODULE, 'gh_lines', side_effect=[subprocess.CalledProcessError(1, ['gh']), []]) as fetch:
            errors = MODULE.homebrew_candidates({})
        self.assertEqual(fetch.call_count, 2)
        self.assertEqual(len(errors), 1)

    def test_homebrew_challenge_stops_further_raw_source_requests(self):
        error = urllib.error.HTTPError('url', 429, 'retry later', {}, None)
        with patch.object(MODULE, 'HOMEBREW_PATHS', {'one.rb': 's1', 'two.rb': 's2'}), \
             patch.object(MODULE, 'gh_lines', return_value=['first', 'later']), \
             patch.object(MODULE.urllib.request, 'urlopen', side_effect=error) as request:
            errors = MODULE.homebrew_candidates({})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(errors), 1)

    def test_homebrew_uses_the_advertised_host_and_path_without_requiring_a_digest(self):
        content = b'''version '100.2,campaign'
sha256 :no_check
url "https://new-apps.sonos.com/new-layout/#{version.csv.second}/client_#{version.csv.first}.dmg"
'''
        candidates = {}
        with patch.object(MODULE, 'HOMEBREW_PATHS', {'Casks/s/sonos.rb': 's2'}), \
             patch.object(MODULE, 'gh_lines', return_value=['one']), \
             patch.object(MODULE.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
            errors = MODULE.homebrew_candidates(candidates)
        self.assertEqual(errors, [])
        self.assertEqual([row['url'] for row in candidates.values()],
                         ['https://new-apps.sonos.com/new-layout/campaign/client_100.2.dmg'])
        self.assertIsNone(next(iter(candidates.values()))['expected_sha256'])

    def test_single_quoted_ruby_url_does_not_invent_interpolated_downloads(self):
        content = b'''version '100.2,campaign'
url 'https://new-apps.sonos.com/#{version.csv.second}/client_#{version.csv.first}.dmg'
'''
        candidates = {}
        with patch.object(MODULE, 'HOMEBREW_PATHS', {'Casks/s/sonos.rb': 's2'}), \
             patch.object(MODULE, 'gh_lines', return_value=['one']), \
             patch.object(MODULE.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
            errors = MODULE.homebrew_candidates(candidates)
        self.assertEqual(candidates, {})
        self.assertEqual(len(errors), 1)
        self.assertIn('cannot interpolate', errors[0]['error'])

    def test_source_report_errors_make_the_discovery_document_partial(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'discovery.json'
            error = {'source': 'homebrew:commit', 'error': 'metadata unavailable'}
            with patch.object(sys, 'argv', ['desktop', '--output', str(output), '--archive-root', temporary]), \
                 patch.object(MODULE, 'winget_candidates', return_value=[]), patch.object(MODULE, 'winget_history', return_value={'status': 'ok', 'pending_total': 0, 'errors': []}), \
                 patch.object(MODULE, 'homebrew_candidates', return_value=[error]), \
                 patch.object(MODULE, 'check_direct_redirects', return_value=[]):
                self.assertEqual(MODULE.main(), 0)
            report = json.loads(output.read_text())
        self.assertEqual(report['status'], 'partial')
        self.assertIn(error, report['errors'])
