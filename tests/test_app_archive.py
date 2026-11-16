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

    def run_archive(self, candidates=None, *, now=NOW, limit=12, repo='owner/repo'):
        if candidates is not None:
            self.discovery.write_text(json.dumps({'candidates': candidates}))
        with patch.object(retry, '_now', return_value=now):
            return preserve(self.source, self.discovery, self.receipt, repo, root=self.root, limit=limit)

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

    def test_single_slot_cannot_be_monopolized_by_a_missing_old_upload(self):
        old = {'repository': 'owner/repo', 'candidate': self.candidate('old.exe'),
               'sha256': 'a'*64, 'bytes': 10, 'release_tag': 'missing-old', 'release_asset': 'old.exe',
               'checked_at': '2000-01-01T00:00:00+00:00'}
        self.receipt.write_text(json.dumps({'schema_version': 1, 'artifacts': [], 'unpublished_captures': [old]}))
        fresh = self.candidate('new.exe')
        first = self.run_archive([fresh], limit=1)
        self.assertEqual(first['attempted_this_run'], 1)
        self.assertEqual(self.acquired, [])
        second = self.run_archive([fresh], now=NOW+timedelta(days=2), limit=1)
        self.assertEqual(second['attempted_this_run'], 1)
        self.assertEqual(second['artifact_total'], 1)
        self.assertEqual(self.acquired, [fresh['url']])
        third = self.run_archive([], now=NOW+timedelta(days=4), limit=1)
        self.assertEqual(third['unpublished_captures'][0]['consecutive_failures'], 2)
        self.assertEqual(len(self.acquired), 1)

    def test_paused_unattempted_replay_cannot_take_a_healthy_sources_only_slot(self):
        paused = self.candidate('paused.exe', acquisition_id='wayback:paused', archive_capture={'timestamp': '20200101000000'})
        fresh = self.candidate('healthy.exe')
        self.discovery.write_text(json.dumps({'candidates': [paused, fresh]}))
        with patch.object(retry, '_now', return_value=NOW):
            result = preserve(self.source, self.discovery, self.receipt, 'owner/repo', root=self.root,
                              limit=1, paused_hosts={'web.archive.org'})
        self.assertEqual(self.acquired, [fresh['url']])
        self.assertEqual(result['attempted_this_run'], 1)
        self.assertEqual(result['source_observations']['owner/repo']['wayback:paused']['status'], 'pending')

    def test_timestamped_captures_at_same_url_remain_distinct_queued_acquisitions(self):
        rows = [self.candidate(acquisition_id='wayback:old'), self.candidate(acquisition_id='wayback:new')]
        self.data.update({'wayback:old': b'MZ older captured installer', 'wayback:new': b'MZ newer captured installer'})
        result = self.run_archive(rows)
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual(set(result['source_observations']['owner/repo']), {'wayback:old', 'wayback:new'})
        self.assertEqual({row['acquisitions'][0]['acquisition_id'] for row in result['artifacts']},
                         {'wayback:old', 'wayback:new'})
        self.assertEqual(len(self.github.uploads), 2)

    def test_same_url_historical_upload_recovers_independently_of_new_capture(self):
        rows = [self.candidate(acquisition_id='wayback:old'), self.candidate(acquisition_id='wayback:new')]
        self.data.update({'wayback:old': b'MZ older captured installer', 'wayback:new': b'MZ newer captured installer'})
        self.github.lose_upload_response = True
        self.assertEqual(self.run_archive(rows, limit=1)['artifact_total'], 0)
        self.data['wayback:old'] = AssertionError('recovery must not acquire the old source again')
        result = self.run_archive(rows, now=NOW+timedelta(hours=2))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual(len(self.acquired), 2)
        self.assertTrue(all(row['status'] == 'preserved' for row in result['source_observations']['owner/repo'].values()))

    def test_identical_historical_bytes_keep_every_acquisition_context(self):
        rows = [self.candidate(acquisition_id='wayback:old'), self.candidate(acquisition_id='wayback:new')]
        result = self.run_archive(rows)
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual({row['acquisition_id'] for row in result['artifacts'][0]['acquisitions']},
                         {'wayback:old', 'wayback:new'})
        self.assertEqual(len(self.github.uploads), 1)

    def test_seen_url_refreshes_and_keeps_both_byte_distinct_snapshots(self):
        candidate = self.candidate()
        first = self.run_archive([candidate])['artifacts'][0]['sha256']
        self.run_archive([candidate], now=NOW + timedelta(days=1))
        self.assertEqual(len(self.acquired), 1)
        self.data[candidate['url']] = b'MZ changed installer at the same URL'
        result = self.run_archive([candidate], now=NOW + timedelta(days=8))
        self.assertEqual(result['artifact_total'], 2)
        self.assertIn(first, {row['sha256'] for row in result['artifacts']})
        self.assertNotEqual(self.github.uploads[0][2], self.github.uploads[1][2])

    def test_newly_current_url_uses_target_cadence_instead_of_historical_timer(self):
        candidate = self.candidate(current=False)
        self.run_archive([candidate])
        current = {**candidate, 'current': True}
        self.run_archive([current], now=NOW + timedelta(hours=5))
        self.assertEqual(len(self.acquired), 1)
        self.data[candidate['url']] = b'MZ current payload changed without an etag'
        result = self.run_archive([current], now=NOW + timedelta(hours=6))
        self.assertEqual(len(self.acquired), 2)
        self.assertEqual(result['artifact_total'], 2)

    def test_revived_http_availability_bypasses_negative_retry_timer(self):
        candidate = self.candidate(http_status=404)
        self.data[candidate['url']] = urllib.error.HTTPError(candidate['url'], 404, 'fixture missing', {}, None)
        self.run_archive([candidate])
        self.data[candidate['url']] = b'MZ recovered server object'
        result = self.run_archive([{**candidate, 'http_status': 200}], now=NOW + timedelta(hours=1))
        self.assertEqual(len(self.acquired), 2)
        self.assertEqual(result['artifact_total'], 1)

    def test_empty_response_is_not_an_archived_installer(self):
        candidate = self.candidate()
        self.data[candidate['url']] = b''
        result = self.run_archive([candidate])
        self.assertEqual(result['artifact_total'], 0)
        self.assertEqual(len(self.github.uploads), 0)
        self.assertEqual(result['status'], 'partial')

    def test_new_etag_and_checksum_bypass_refresh_cooldown(self):
        candidate = self.candidate(etag='first')
        self.run_archive([candidate])
        data = b'MZ new current version'
        self.data[candidate['url']] = data
        current = {**candidate, 'etag': 'second', 'expected_sha256': hashlib.sha256(data).hexdigest()}
        result = self.run_archive([current], now=NOW + timedelta(minutes=5))
        self.assertEqual(len(self.acquired), 2)
        self.assertEqual(result['artifact_total'], 2)

    def test_changed_revision_left_outside_budget_remains_pending(self):
        candidates = [self.candidate('one.exe', etag='old'), self.candidate('two.exe', etag='old')]
        self.run_archive(candidates)
        changed = [{**row, 'etag': 'new'} for row in candidates]
        self.data.update({row['url']: ('MZ new ' + row['url']).encode() for row in changed})
        result = self.run_archive(changed, now=NOW + timedelta(minutes=5), limit=1)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['pending_total'], 1)
        result = self.run_archive([], now=NOW + timedelta(minutes=10), limit=1)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['pending_total'], 0)
        self.assertEqual(len(self.acquired), 4)

    def test_same_bytes_at_different_urls_are_verified_before_aliasing(self):
        one, two = self.candidate('one.exe'), self.candidate('two.exe', version='2')
        result = self.run_archive([one, two])
        self.assertEqual(self.acquired, [one['url'], two['url']])
        self.assertEqual(len(self.github.uploads), 1)
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(set(result['artifacts'][0]['source_urls']), {one['url'], two['url']})
        self.assertEqual(result['artifacts'][0]['versions'], ['1', '2'])

    def test_failed_source_retries_without_blocking_other_candidates(self):
        one, two = self.candidate('one.exe'), self.candidate('two.exe')
        self.data[one['url']] = OSError('fixture offline')
        result = self.run_archive([one, two])
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(result['status'], 'partial')
        self.run_archive([], now=NOW + timedelta(minutes=30))
        self.assertEqual(len(self.acquired), 2)
        self.data[one['url']] = b'MZ recovered fixture'
        # No new discovery row or manually prepared input is needed to recover.
        result = self.run_archive([], now=NOW + timedelta(hours=2))
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual(result['gaps'], [])
        self.assertEqual(result['status'], 'ok')

    def test_limit_counts_failures_and_retains_unattempted_discoveries(self):
        candidates = [self.candidate(str(n) + '.exe') for n in range(4)]
        self.data.update({c['url']: OSError('fixture offline') for c in candidates})
        result = self.run_archive(candidates, limit=2)
        self.assertEqual(len(self.acquired), 2)
        self.assertEqual(result['attempted_this_run'], 2)
        self.assertEqual(result['pending_total'], 2)
        self.run_archive([], limit=2, now=NOW + timedelta(minutes=5))
        self.assertEqual(self.acquired, [c['url'] for c in candidates])

    def test_legacy_gap_is_not_a_permanent_exclusion(self):
        candidate = self.candidate()
        self.receipt.write_text(json.dumps({'schema_version': 1, 'artifacts': [], 'gaps': [{
            'url': candidate['url'], 'family': 's2', 'version': '1', 'reason': 'invalid old APK capture'}]}))
        result = self.run_archive([])
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(result['gaps'], [])

    def test_selected_repository_does_not_reuse_foreign_publication(self):
        candidate = self.candidate()
        self.run_archive([candidate], repo='owner/original')
        result = self.run_archive([candidate], repo='owner/fork')
        self.assertEqual(len(self.acquired), 2)
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual({row['release_url'].split('/')[4] for row in result['artifacts']}, {'original', 'fork'})
        self.assertEqual({upload[0] for upload in self.github.uploads}, {'owner/original', 'owner/fork'})

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

    def test_delayed_old_publication_recovers_after_new_bytes_are_preserved(self):
        candidate = self.candidate()
        self.github.lose_upload_response = True
        self.run_archive([candidate])
        assets = self.github.releases[('owner/repo', 'apps-windows')]
        name, delayed = next(iter(assets.items()))
        del assets[name]
        self.data[candidate['url']] = b'MZ newer replacement payload'
        result = self.run_archive([], now=NOW + timedelta(hours=2))
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['unpublished_captures']), 1)
        assets[name] = delayed
        result = self.run_archive([], now=NOW + timedelta(hours=4))
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual(result['unpublished_captures'], [])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(self.acquired), 2)

    def test_pending_capture_recovery_does_not_hide_newly_observed_version(self):
        candidate = self.candidate(version='1')
        self.github.lose_upload_response = True
        self.run_archive([candidate])
        changed = {**candidate, 'version': '2', 'etag': 'new'}
        result = self.run_archive([changed], now=NOW + timedelta(minutes=5))
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(result['artifacts'][0]['version'], '1')
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['pending_total'], 1)
        self.data[candidate['url']] = b'MZ newer version payload'
        result = self.run_archive([changed], now=NOW + timedelta(minutes=10))
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual(result['status'], 'ok')

    def test_recovered_capture_does_not_inherit_unverified_new_checksum(self):
        candidate = self.candidate()
        self.github.lose_upload_response = True
        self.run_archive([candidate])
        replacement = b'MZ new bytes with a newly discovered checksum'
        checksum = hashlib.sha256(replacement).hexdigest()
        changed = {**candidate, 'expected_sha256': checksum}
        self.github.inventory_failure = urllib.error.HTTPError('https://api.github.com/fixture', 503, 'fixture offline', {}, None)
        self.run_archive([changed], now=NOW + timedelta(minutes=5))
        self.data[candidate['url']] = replacement
        result = self.run_archive([changed], now=NOW + timedelta(hours=3))
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['pending_total'], 1)
        observation = result['source_observations']['owner/repo'][candidate['url']]
        self.assertNotIn('expected_sha256', observation['revision'])
        result = self.run_archive([changed], now=NOW + timedelta(hours=3, minutes=5))
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual(result['status'], 'ok')
        self.assertIn(checksum, {row['sha256'] for row in result['artifacts']})

    def test_empty_partial_discovery_is_not_a_clean_archive_result(self):
        self.discovery.write_text(json.dumps({'status': 'partial', 'errors': ['fixture index unavailable'], 'candidates': []}))
        result = self.run_archive()
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['discovery_status'], 'partial')
        self.assertEqual(result['artifact_total'], 0)
        self.assertEqual(self.acquired, [])

    def test_missing_discovery_does_not_prevent_pending_publication_recovery(self):
        candidate = self.candidate()
        self.github.lose_upload_response = True
        self.run_archive([candidate])
        self.discovery.unlink()
        self.data[candidate['url']] = OSError('fixture source offline')
        result = self.run_archive(now=NOW + timedelta(hours=2))
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['discovery_status'], 'unavailable')
        self.assertEqual(len(self.acquired), 1)

    def test_checksum_mismatch_does_not_claim_verified_publication(self):
        result = self.run_archive([self.candidate(expected_sha256='0' * 64)])
        self.assertEqual(result['artifact_total'], 0)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(self.github.uploads), 0)

    def test_remote_digest_mismatch_never_promotes_pending_receipt(self):
        candidate = self.candidate()
        self.github.lose_upload_response = True
        self.run_archive([candidate])
        asset = next(iter(self.github.releases[('owner/repo', 'apps-windows')].values()))
        asset['digest'] = 'sha256:' + '0' * 64
        result = self.run_archive([], now=NOW + timedelta(hours=2))
        self.assertEqual(result['artifact_total'], 0)
        self.assertEqual(result['status'], 'partial')
        self.assertIn('GitHub asset mismatch', result['errors'][0]['error'])
        self.assertEqual(len(self.github.uploads), 1)


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
