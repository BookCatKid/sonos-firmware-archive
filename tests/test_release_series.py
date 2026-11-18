"""Release capacity must not impose a lifetime limit on preserved captures."""
import hashlib
import importlib.util
import json
import subprocess
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import releases, release_series
from test_app_archive import FakeGitHub


class CapacityGitHub(FakeGitHub):
    def command(self, command, **kwargs):
        if command[:3] == ['gh', 'release', 'upload']:
            repo = command[command.index('--repo')+1]
            if len(self.releases[(repo, command[3])]) >= 1000:
                raise subprocess.CalledProcessError(1, command, stderr='file_count limited to 1000 assets per release')
        return super().command(command, **kwargs)


class ReleaseSeriesTests(unittest.TestCase):
    def setUp(self):
        self.github = CapacityGitHub()
        self.repo, self.base = 'fresh/archive', 'captures-fixture'
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.addCleanup(patch.stopall)
        patch.object(release_series, 'matching_tags', side_effect=self.github.matching_tags).start()
        self.inventory = patch.object(releases, 'remote_assets', side_effect=self.github.assets).start()
        patch.object(releases, 'public_release', side_effect=self.github.public_release).start()
        patch.object(releases, 'upload_streaming', side_effect=self.github.upload_streaming).start()
        patch.object(releases.subprocess, 'run', side_effect=self.github.command).start()
        self.series = release_series.ReleaseSeries(self.repo, self.base, 'Captures', 'Synthetic tests')

    def capture(self, name='capture.upm', data=b'synthetic metadata'):
        path = self.root/name
        path.write_bytes(data)
        return path, {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}

    def fill(self, tag):
        self.github.releases[(self.repo, tag)] = {f'old-{i}': {'size': 1} for i in range(1000)}

    def test_multiple_full_partitions_continue_past_single_digit_ordinals(self):
        self.fill(self.base)
        for number in range(2, 12):
            self.fill(self.base+f'-part-{number}')
        proof = self.series.publish(*self.capture())
        self.assertEqual(proof['release_tag'], self.base+'-part-12')
        self.assertEqual(len(self.github.uploads), 1)

    def test_existing_later_partition_is_reconciled_before_using_earlier_space(self):
        self.fill(self.base)
        path, capture = self.capture()
        first = self.series.publish(path, capture)
        self.github.releases[(self.repo, self.base)].pop('old-0')
        restarted = release_series.ReleaseSeries(self.repo, self.base, 'Captures', 'Tests')
        self.assertEqual(restarted.publish(path, capture), first)
        self.assertEqual(len(self.github.uploads), 1)

    def test_name_collision_is_not_hidden_in_another_partition(self):
        path, capture = self.capture()
        self.series.publish(path, capture)
        self.github.releases[(self.repo, self.base)]['capture.upm']['digest'] = 'sha256:'+'0'*64
        with self.assertRaisesRegex(RuntimeError, 'asset mismatch'):
            self.series.publish(path, capture)
        self.assertEqual(len(self.github.uploads), 1)

    def test_lost_response_recovers_the_uploaded_asset(self):
        self.github.lose_upload_response = True
        proof = self.series.publish(*self.capture())
        self.assertEqual(proof['status'], 'verified')
        self.assertEqual(len(self.github.uploads), 1)

    def test_capacity_race_refreshes_then_rolls_over(self):
        original = self.github.upload_streaming
        def upload(repo, release_id, name, path):
            if release_id == self.base:
                self.fill(self.base)
            return original(repo, release_id, name, path)
        with patch.object(releases, 'upload_streaming', side_effect=upload):
            proof = self.series.publish(*self.capture())
        self.assertEqual(proof['release_tag'], self.base+'-part-2')

    def test_unconfirmed_capacity_error_does_not_create_endless_partitions(self):
        error = subprocess.CalledProcessError(1, 'upload', stderr='file_count limited to 1000 assets per release')
        with patch.object(releases, 'upload_streaming', side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                self.series.publish(*self.capture())
        self.assertEqual(set(self.github.releases), {(self.repo, self.base)})

    def test_other_upload_failures_do_not_create_partitions(self):
        with patch.object(releases, 'upload_streaming', side_effect=TimeoutError('unavailable')):
            with self.assertRaises(TimeoutError):
                self.series.publish(*self.capture())
        self.assertEqual(set(self.github.releases), {(self.repo, self.base)})

    def test_unavailable_inventory_does_not_block_fresh_evidence_and_remains_reported(self):
        self.fill(self.base)
        original = self.github.assets
        def inventory(repo, tag):
            if tag == self.base:
                raise urllib.error.URLError('older release temporarily unavailable')
            return original(repo, tag)
        with patch.object(releases, 'remote_assets', side_effect=inventory):
            proof = self.series.publish(*self.capture())
            other = self.series.publish(*self.capture('other.upm', b'other metadata'))
        self.assertEqual(proof['release_tag'], self.base+'-part-2')
        self.assertEqual(other['release_tag'], proof['release_tag'])
        self.assertEqual(len(self.series.errors), 1)
        self.assertEqual(self.series.errors[0]['release_tag'], self.base)
        self.assertEqual(len(self.github.releases[(self.repo, self.base)]), 1000)

    def test_inventory_is_shared_across_uploads_and_refreshes_on_restart(self):
        self.series.publish(*self.capture())
        self.series.publish(*self.capture('other.upm', b'other metadata'))
        self.inventory.assert_called_once_with(self.repo, self.base)
        self.inventory.reset_mock()
        release_series.ReleaseSeries(self.repo, self.base, 'Captures', 'Tests').publish(*self.capture())
        self.inventory.assert_called_once_with(self.repo, self.base)

    def test_changed_local_bytes_are_rejected_before_any_network_access(self):
        path, capture = self.capture()
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'does not match'):
            self.series.publish(path, capture)
        self.inventory.assert_not_called()
        self.assertFalse(self.github.releases)

    def test_missing_remote_digest_is_verified_before_returning_receipt(self):
        path, capture = self.capture()
        self.series.publish(path, capture)
        del self.github.releases[(self.repo, self.base)][path.name]['digest']
        with patch.object(releases, 'verified_remote_digest', return_value=capture['sha256']) as verify:
            self.assertEqual(self.series.publish(path, capture)['sha256'], capture['sha256'])
        verify.assert_called_once()

    def test_manifest_preservation_rolls_over_a_full_release_and_records_actual_tag(self):
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('capacity_publisher', root/'scripts/discover_firmware.py')
        publisher = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(publisher)
        github = CapacityGitHub()
        repo, base = 'fresh/archive', 'manifest-snapshots-fixture'
        github.releases[(repo, base)] = {f'old-{i}': {'size': 1, 'digest': 'sha256:'+'0'*64,
            'url': f'https://github.com/{repo}/releases/download/{base}/old-{i}'} for i in range(1000)}
        body = b'<html>synthetic discovery page</html>'
        document = {'url': 'https://update.sonos.com/firmware/downloads.html', 'snapshot': 'page.html',
                    'sha256': hashlib.sha256(body).hexdigest(), 'bytes': len(body),
                    'document_type': 'landing-page', 'metadata': {'format': 'html'}}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(release_series, 'matching_tags', side_effect=github.matching_tags), \
             patch.object(releases, 'public_release', side_effect=github.public_release), \
             patch.object(releases, 'upload_streaming', side_effect=github.upload_streaming), \
             patch.object(releases, 'remote_assets', side_effect=github.assets), \
             patch.object(releases.subprocess, 'run', side_effect=github.command), \
             patch.object(publisher, 'archive_candidates', return_value={'errors': [], 'pending': 0}), \
             patch.object(publisher, 'datetime') as clock:
            clock.now.return_value.date.return_value.isoformat.return_value = 'fixture'
            directory = Path(temporary)
            (directory/'page.html').write_bytes(body)
            state = {'manifests': [], 'documents': [document], 'probes': {}}
            failed = publisher.preserve(state, directory/'data/catalog.json', directory/'state.json',
                                        directory, directory, repo)
            self.assertFalse(failed, state.get('preservation_errors'))
            record, = json.loads((directory/'data/catalog.json').read_text())['source_documents']
            self.assertEqual(record['release_tag'], base+'-part-2')
            self.assertEqual(len(github.releases[(repo, base)]), 1000)
            self.assertEqual(len(github.releases[(repo, base+'-part-2')]), 1)


if __name__ == '__main__':
    unittest.main()
