"""Exercise app capture through acquisition, retry and real publication helpers."""
import hashlib
import json
import tempfile
import unittest
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from sonos_firmware import releases, retry
from sonos_firmware.app_archive import AppSource, preserve

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeGitHub:
    def __init__(self):
        self.releases = {}
        self.uploads = []
        self.lose_upload_response = False
        self.inventory_failure = None

    def assets(self, repo, tag):
        if self.inventory_failure:
            error, self.inventory_failure = self.inventory_failure, None
            raise error
        if (repo, tag) not in self.releases:
            raise urllib.error.HTTPError('https://api.github.com/fixture', 404, 'missing', {}, None)
        return self.releases[(repo, tag)]

    def matching_tags(self, repo, prefix):
        return [tag for owner, tag in self.releases if owner == repo and tag.startswith(prefix)]

    def public_release(self, repo, tag):
        self.assets(repo, tag)
        return {'id': tag}

    def upload_streaming(self, repo, release_id, name, path):
        assert name == path.name
        self.command(['gh', 'release', 'upload', release_id, str(path), '--repo', repo])
        return self.releases[(repo, release_id)][name]

    def command(self, command, **kwargs):
        assert command[:2] == ['gh', 'release']
        repo = command[command.index('--repo') + 1]
        tag, operation = command[3], command[2]
        if operation == 'view':
            return SimpleNamespace(returncode=0 if (repo, tag) in self.releases else 1)
        if operation == 'create':
            self.releases[(repo, tag)] = {}
        elif operation == 'upload':
            path = Path(command[4])
            data = path.read_bytes()
            self.releases[(repo, tag)][path.name] = {
                'size': len(data), 'digest': 'sha256:' + hashlib.sha256(data).hexdigest(),
                'browser_download_url': f'https://github.com/{repo}/releases/download/{tag}/{path.name}',
            }
            self.uploads.append((repo, tag, path.name, data))
            if self.lose_upload_response:
                self.lose_upload_response = False
                raise TimeoutError('upload response lost after GitHub stored the asset')
        else:
            raise AssertionError(operation)
        return SimpleNamespace(returncode=0)


class AppArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.discovery = self.root / 'discovery.json'
        self.receipt = self.root / 'archive.json'
        self.github = FakeGitHub()
        self.data = {}
        self.acquired = []
        self.paths = []
        self.source = AppSource(self.acquire, lambda c: ('apps-' + c['platform'], 'Fixture apps'), 'Fixture', 'windows')
        self.addCleanup(patch.stopall)
        patch.object(releases, 'remote_assets', side_effect=self.github.assets).start()
        patch.object(releases.subprocess, 'run', side_effect=self.github.command).start()

    def candidate(self, name='Sonos_1.exe', **extra):
        return {'url': 'https://update-software.sonos.com/software/pc/' + name,
                'family': 's2', 'platform': 'windows', 'version': '1', 'sources': ['fixture'], **extra}

    def acquire(self, candidate, destination):
        url = candidate['url']
        self.acquired.append(url)
        self.paths.append(destination)
        data = self.data.get(candidate.get('acquisition_id', url), b'MZ offline installer fixture')
        if isinstance(data, Exception):
            raise data
        destination.write_bytes(data)
        return url

    def run_archive(self, candidates=None, *, now=NOW, repo='owner/repo', work_units=None):
        if candidates is not None:
            self.discovery.write_text(json.dumps({'candidates': candidates}))
        checked = 0
        def winding_down():
            nonlocal checked
            checked += 1
            return work_units is not None and checked > work_units
        with patch.object(retry, '_now', return_value=now), \
             patch('sonos_firmware.app_archive.winding_down', side_effect=winding_down):
            return preserve(self.source, self.discovery, self.receipt, repo, root=self.root)

    def test_empty_archive_bootstraps_and_cleans_worker_payloads(self):
        candidate = self.candidate()
        self.assertFalse(self.receipt.exists())
        result = self.run_archive([candidate])
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(self.acquired, [candidate['url']])
        self.assertTrue(all(not path.exists() for path in self.paths))
        self.assertEqual(len(self.github.uploads), 1)
        self.assertEqual(result['artifacts'][0]['sha256'], hashlib.sha256(self.github.uploads[0][3]).hexdigest())

    def test_source_retry_after_survives_restart_and_defers_new_urls_on_the_same_host(self):
        first, peer = self.candidate(), self.candidate('peer.exe')
        self.data[first['url']] = urllib.error.HTTPError(first['url'], 429, 'retry later', {'Retry-After': '75'}, None)
        self.run_archive([first])
        waiting = self.run_archive([first, peer], now=NOW+timedelta(seconds=74))
        self.assertEqual(waiting['attempted_this_run'], 0)
        self.assertEqual(self.acquired, [first['url']])
        self.data.clear()
        resumed = self.run_archive(now=NOW+timedelta(seconds=76))
        self.assertEqual(resumed['artifact_total'], 1)
        self.assertEqual(set(self.acquired), {first['url'], peer['url']})

    def test_publication_retry_after_does_not_contact_sources_early(self):
        first, peer = self.candidate(), self.candidate('peer.exe')
        self.github.inventory_failure = urllib.error.HTTPError('https://api.github.com/fixture', 429,
            'retry later', {'Retry-After': '75'}, None)
        self.run_archive([first])
        waiting = self.run_archive([first, peer], now=NOW+timedelta(seconds=74))
        self.assertEqual(waiting['attempted_this_run'], 0)
        self.assertEqual(self.acquired, [first['url']])
        resumed = self.run_archive(now=NOW+timedelta(seconds=76))
        self.assertEqual(resumed['status'], 'ok')


    def test_inspection_failure_prevents_publication_and_respects_backoff(self):
        from dataclasses import replace
        from unittest.mock import Mock
        inspect = Mock(side_effect=ValueError('bad APK signature'))
        self.source = replace(self.source, inspect=inspect, inspection_revision='first')
        first = self.run_archive([self.candidate()])
        self.assertEqual(first['artifact_total'], 0)
        self.assertEqual(self.github.releases, {})
        self.assertIn('bad APK signature', first['errors'][0]['error'])
        again = self.run_archive()
        self.assertEqual(again['attempted_this_run'], 0)
        self.assertEqual(inspect.call_count, 1)


    def test_inspected_upload_response_loss_recovers_without_another_inspection(self):
        from dataclasses import replace
        from unittest.mock import Mock
        inspect = Mock(return_value={'verified': True})
        self.source = replace(self.source, inspect=inspect, inspection_revision='first')
        self.github.lose_upload_response = True
        failed = self.run_archive([self.candidate()])
        self.assertEqual(failed['artifact_total'], 0)
        self.assertEqual(inspect.call_count, 1)
        result = self.run_archive(now=NOW+timedelta(hours=2))
        self.assertEqual(result['artifact_total'], 1)
        self.assertTrue(result['artifacts'][0]['verification']['verified'])
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(len(self.acquired), 1)

    def test_old_pending_publication_cannot_bypass_new_inspection(self):
        from dataclasses import replace
        from unittest.mock import Mock
        self.github.lose_upload_response = True
        self.assertEqual(self.run_archive([self.candidate()])['artifact_total'], 0)
        inspect = Mock(side_effect=ValueError('inspection failed'))
        self.source = replace(self.source, inspect=inspect, inspection_revision='first')
        result = self.run_archive()
        self.assertEqual(result['artifact_total'], 0)
        self.assertEqual(inspect.call_count, 1)
        self.assertEqual(len(self.github.uploads), 1)


    def test_publication_recovery_failure_does_not_hide_fresh_sources(self):
        old = {'repository': 'owner/repo', 'candidate': self.candidate('old.exe'),
               'sha256': 'a'*64, 'bytes': 10, 'release_tag': 'missing-old', 'release_asset': 'old.exe',
               'checked_at': '2000-01-01T00:00:00+00:00'}
        self.receipt.write_text(json.dumps({'schema_version': 1, 'artifacts': [], 'unpublished_captures': [old]}))
        fresh = self.candidate('new.exe')
        first = self.run_archive([fresh], work_units=1)
        self.assertEqual(first['attempted_this_run'], 1)
        self.assertEqual(first['artifact_total'], 1)
        self.assertEqual(self.acquired, [fresh['url']])
        second = self.run_archive([fresh], now=NOW+timedelta(days=2), work_units=1)
        self.assertEqual(second['attempted_this_run'], 1)
        self.assertEqual(second['artifact_total'], 1)
        self.assertEqual(self.acquired, [fresh['url']])
        third = self.run_archive([], now=NOW+timedelta(days=4), work_units=1)
        self.assertEqual(third['unpublished_captures'][0]['consecutive_failures'], 2)
        self.assertEqual(len(self.acquired), 1)


    def test_timestamped_captures_at_same_url_remain_distinct_queued_acquisitions(self):
        rows = [self.candidate(acquisition_id='wayback:old'), self.candidate(acquisition_id='wayback:new')]
        self.data.update({'wayback:old': b'MZ older captured installer', 'wayback:new': b'MZ newer captured installer'})
        result = self.run_archive(rows)
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual(set(result['source_observations']['owner/repo']), {'wayback:old', 'wayback:new'})
        self.assertEqual({row['acquisitions'][0]['acquisition_id'] for row in result['artifacts']},
                         {'wayback:old', 'wayback:new'})
        self.assertEqual(len(self.github.uploads), 2)


    def test_same_bytes_at_different_urls_are_verified_before_aliasing(self):
        one, two = self.candidate('one.exe'), self.candidate('two.exe', version='2')
        result = self.run_archive([one, two])
        self.assertEqual(self.acquired, [one['url'], two['url']])
        self.assertEqual(len(self.github.uploads), 1)
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(set(result['artifacts'][0]['source_urls']), {one['url'], two['url']})
        self.assertEqual(result['artifacts'][0]['versions'], ['1', '2'])


    def test_lost_upload_response_recovers_from_remote_without_original_source(self):
        candidate = self.candidate()
        self.github.lose_upload_response = True
        result = self.run_archive([candidate])
        self.assertEqual(result['artifact_total'], 0)
        self.assertEqual(result['status'], 'partial')
        pending = result['source_observations']['owner/repo'][candidate['url']]['pending_publication']
        self.assertEqual(pending['sha256'], hashlib.sha256(self.github.uploads[0][3]).hexdigest())
        self.data[candidate['url']] = OSError('source URL is now unavailable')
        result = self.run_archive([], now=NOW + timedelta(hours=2))
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(self.acquired), 1)
        self.assertEqual(len(self.github.uploads), 1)


    def test_checksum_mismatch_does_not_claim_verified_publication(self):
        result = self.run_archive([self.candidate(expected_sha256='0' * 64)])
        self.assertEqual(result['artifact_total'], 0)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(self.github.uploads), 0)


class AtomicCheckpointTests(unittest.TestCase):
    def test_failed_replacement_keeps_previous_checkpoint_and_removes_temporary_file(self):
        from sonos_firmware.storage import atomic_json
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / 'state.json'
            atomic_json(path, {'preserved': True})
            previous = path.read_bytes()
            with patch('sonos_firmware.storage.os.replace', side_effect=OSError('fixture disk error')):
                with self.assertRaises(OSError):
                    atomic_json(path, {'new': 'checkpoint'})
            self.assertEqual(path.read_bytes(), previous)
            self.assertEqual(list(root.iterdir()), [path])


if __name__ == '__main__':
    unittest.main()
