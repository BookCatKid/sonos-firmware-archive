"""Package metadata listings cannot truncate or silently discard installer leads."""
import base64
import importlib.util
import io
import subprocess
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('desktop_index',
    Path(__file__).resolve().parents[2]/'scripts/apps/discover_desktop_installers.py')
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
             patch.object(MODULE.subprocess, 'check_output', side_effect=error) as request:
            for endpoint in ('first', 'later'):
                with self.assertRaisesRegex(MODULE.GitHubSourcePaused, 'HTTP 403'):
                    MODULE.gh_json(endpoint)
            self.assertEqual(request.call_count, 1)

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


    def test_homebrew_challenge_stops_further_raw_source_requests(self):
        error = urllib.error.HTTPError('url', 429, 'retry later', {}, None)
        with patch.object(MODULE, 'HOMEBREW_PATHS', {'one.rb': 's1', 'two.rb': 's2'}), \
             patch.object(MODULE, 'gh_lines', return_value=['first', 'later']), \
             patch.object(MODULE.urllib.request, 'urlopen', side_effect=error) as request:
            errors = MODULE.homebrew_candidates({})
        self.assertEqual(request.call_count, 1)
        self.assertEqual(len(errors), 1)


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
