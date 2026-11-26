"""Offline cold-start, retry, refresh, and publication regressions."""
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import retry
from sonos_firmware.gpl_archive import HttpResult, index_url

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load(name):
    spec = importlib.util.spec_from_file_location('retry_' + name, next((ROOT / 'scripts').rglob(name + '.py')))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def page(version='1.0', filename='source.tar.xz'):
    body = ('<html>Sonos GPL/LGPL Downloads<a href="' + filename + '">source</a></html>').encode()
    return HttpResult(200, body, 'text/html', index_url(version))


def discover_until_wind_down(module, args):
    fetch = module.get_page
    before = getattr(fetch, 'call_count', 0)
    with patch.object(module, 'winding_down', side_effect=lambda: getattr(fetch, 'call_count', 0) > before):
        return module.discover(args)


class RetryPolicyTests(unittest.TestCase):
    def test_valid_server_deadline_can_exceed_local_backoff_cap(self):
        import urllib.error
        from email.utils import format_datetime
        expected = NOW+timedelta(days=8)
        for value in (str(8*24*60*60), format_datetime(expected)):
            with self.subTest(value=value):
                error = urllib.error.HTTPError('https://example.test', 429, 'retry later', {'Retry-After': value}, None)
                self.assertEqual(retry.error_checkpoint(error, now=NOW)['next_attempt_at'], expected.isoformat())

    def test_request_backoff_scopes_survive_serialization_without_blocking_other_repositories(self):
        import urllib.error
        state = {}
        error = urllib.error.HTTPError('https://example.test', 503, 'unavailable', {'Retry-After': '75'}, None)
        with patch.object(retry, '_now', return_value=NOW):
            retry.RequestBackoff(state, 'owner/first').failed('publication', error)
        state = json.loads(json.dumps(state))
        with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=74)):
            self.assertTrue(retry.RequestBackoff(state, 'owner/first').paused('publication'))
            self.assertFalse(retry.RequestBackoff(state, 'owner/second').paused('publication'))
        with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=76)):
            self.assertFalse(retry.RequestBackoff(state, 'owner/first').paused('publication'))


    def test_backoff_and_refresh_are_automatic_and_bounded(self):
        record = None
        for expected_hours in (1, 2, 4, 8, 16, 24, 24):
            record = retry.checkpoint(record, now=NOW)
            self.assertFalse(retry.due(record, NOW))
            self.assertTrue(retry.due(record, NOW + timedelta(hours=expected_hours)))
            self.assertFalse(retry.due(record, NOW + timedelta(hours=expected_hours, seconds=-1)))
        success = retry.checkpoint(record, success=True, success_hours=24, now=NOW)
        self.assertEqual(success['consecutive_failures'], 0)
        self.assertTrue(retry.due(success, NOW + timedelta(days=1)))
        negative = retry.checkpoint(negative=True, now=NOW)
        self.assertFalse(retry.due(negative, NOW + timedelta(days=6)))
        self.assertTrue(retry.due(negative, NOW + timedelta(days=7)))


class GPLRetryTests(unittest.TestCase):
    def test_metadata_retry_after_survives_restarts_and_pauses_fresh_versions(self):
        for status in (429, 503):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as temporary:
                module = load('archive_gpl_direct')
                root = Path(temporary)
                args = self.args(root)
                response = HttpResult(status, b'temporary', 'text/plain', index_url('0.0'), retry_after=7200)
                with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                     patch.object(module, 'get_page', return_value=response) as fetch:
                    with patch.object(retry, '_now', return_value=NOW):
                        discover_until_wind_down(module, args)
                    with patch.object(retry, '_now', return_value=NOW+timedelta(minutes=90)):
                        discover_until_wind_down(module, args)
                    self.assertEqual(fetch.call_count, 1)
                state = json.loads((args.output/'discovery.json').read_text())
                deadline = (NOW+timedelta(hours=2)).isoformat()
                self.assertEqual(state['source_backoff']['next_attempt_at'], deadline)
                self.assertEqual(state['candidates']['0.0']['next_attempt_at'], deadline)
                with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                     patch.object(module, 'get_page', return_value=page()) as fetch, \
                     patch.object(retry, '_now', return_value=NOW+timedelta(hours=3)):
                    discover_until_wind_down(module, args)
                    self.assertEqual(fetch.call_count, 1)

    def args(self, root, **extra):
        return Namespace(output=root / 'live', recheck=False, binary=[], delay=0, **extra)


    def test_numeric_cursor_archives_versions_above_former_major_and_minor_limits(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            args.output.mkdir()
            ordinal = 2000*2001//2+1000
            (args.output/'discovery.json').write_text(json.dumps({'schema_version': 1, 'candidates': {},
                'exploration': {'next_ordinal': ordinal}}))
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=page('1000.1000')) as fetch:
                discover_until_wind_down(module, args)
            fetch.assert_called_once_with(index_url('1000.1000'))
            self.assertIn('1000.1000', module.live_catalog(args.output)['releases'])
            state = json.loads((args.output/'discovery.json').read_text())
            self.assertGreater(state['exploration']['next_ordinal'], ordinal)


    def test_source_cooldown_is_not_bypassed_by_new_versions(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=HttpResult(429, b'retry later', 'text/plain', index_url('0.0'))) as fetch:
                discover_until_wind_down(module, args)
                discover_until_wind_down(module, args)
                self.assertEqual(fetch.call_count, 1)
            state = json.loads((args.output/'discovery.json').read_text())
            state['source_backoff']['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
            (args.output/'discovery.json').write_text(json.dumps(state))
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=page()) as fetch:
                discover_until_wind_down(module, args)
                self.assertEqual(fetch.call_count, 1)
            self.assertEqual(json.loads((args.output/'discovery.json').read_text())['source_backoff'], {})

    def test_new_version_evidence_cannot_starve_numeric_exploration(self):
        module = load('archive_gpl_direct')
        requested = []
        def missing(url):
            requested.append(url)
            return HttpResult(404, b'absent', 'text/plain', url)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', side_effect=missing):
                for major in range(2000, 2006):
                    with patch.object(module, 'offline_candidates', return_value={f'{major}.1000': {index_url(f'{major}.1000')}}):
                        discover_until_wind_down(module, args)
            self.assertTrue({index_url(version) for version in ('0.0', '1.0', '0.1')} <= set(requested))
            state = json.loads((args.output/'discovery.json').read_text())
            self.assertGreaterEqual(state['exploration']['next_ordinal'], 3)
            self.assertTrue(state['pending_versions'])


    def test_failed_version_recovers_without_recheck_flag(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=HttpResult(403, b'<Error>AccessDenied</Error>', 'application/xml', index_url('1.0'))) as fetch, \
                 patch.object(retry, '_now', return_value=NOW):
                discover_until_wind_down(module, args)
                discover_until_wind_down(module, args)
                self.assertEqual(fetch.call_count, 2)
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=page()), \
                 patch.object(retry, '_now', return_value=NOW + timedelta(hours=2)):
                discover_until_wind_down(module, args)
            self.assertIn('0.0', module.live_catalog(args.output)['releases'])
            state = json.loads((args.output / 'discovery.json').read_text())
            self.assertEqual(state['candidates']['0.0']['consecutive_failures'], 0)


    def test_verified_cached_source_skips_reacquisition_and_can_publish_immediately(self):
        import hashlib
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            index = module.save_index('1.0', page(), root)
            payload = b'previously verified source fixture'
            digest = hashlib.sha256(payload).hexdigest()
            store = root / 'objects'
            blob = store / 'sha256' / digest[:2] / digest
            blob.parent.mkdir(parents=True); blob.write_bytes(payload)
            (root / 'artifacts.json').write_text(json.dumps({'schema_version': 1, 'artifacts': {'1.0': {
                'gpl.html': {'filename': 'gpl.html', 'sha256': index['sha256'], 'published': True},
                'source.tar.xz': {'filename': 'source.tar.xz', 'state': 'verified', 'sha256': digest,
                                  'source_url': index['links'][0]['url'], 'bytes': len(payload),
                                  **retry.checkpoint(success=True, now=NOW)} }}}))
            args = Namespace(output=root, version=[], next=True,                              filename=[], include_upstream=False, retry_errors=False,
                             continue_errors=True, publish=False, store=store,
                             repo='owner/repo', delay=0)
            with patch.object(module, 'download_artifact', side_effect=AssertionError('valid cached blob must not be acquired again')), \
                 patch.object(module, 'publish', return_value={'state': 'published'}) as publish:
                with patch.object(retry, '_now', return_value=NOW + timedelta(days=2)):
                    module.archive(args)
                publish.assert_not_called()
                args.publish = True
                # Even before success refresh expires, publication needs no delay.
                with patch.object(retry, '_now', return_value=NOW):
                    module.archive(args)
                publish.assert_called_once()
            receipt = json.loads((root / 'artifacts.json').read_text())['artifacts']['1.0']['source.tar.xz']
            self.assertTrue(receipt['published'])

    def test_new_url_with_same_filename_is_published_without_overwriting_old_bytes(self):
        import hashlib
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = index_url('1.0').replace('gpl.html', 'source.tar.xz')
            second = 'https://ftp.gnu.org/gnu/project/source.tar.xz'
            body = ('Sonos GPL/LGPL Downloads<a href="' + first + '">old</a><a href="' + second + '">new</a>').encode()
            index = module.save_index('1.0', HttpResult(200, body, 'text/html', index_url('1.0')), root)
            old_body = b'old verified source capture'
            old_hash = hashlib.sha256(old_body).hexdigest()
            old = {'filename': 'source.tar.xz', 'source_url': first, 'state': 'published', 'published': True,
                   'sha256': old_hash, 'bytes': len(old_body), 'kind': 'source'}
            (root / 'artifacts.json').write_text(json.dumps({'schema_version': 1, 'artifacts': {'1.0': {
                'gpl.html': {'filename': 'gpl.html', 'sha256': index['sha256'], 'published': True},
                'source.tar.xz': old}}}))
            remote = {'id': 1, 'assets': [{'name': 'source.tar.xz', 'size': len(old_body),
                                          'digest': 'sha256:' + old_hash, 'browser_download_url': 'fixture://old'}]}
            acquired = []
            def acquire(link, store, **kwargs):
                acquired.append(link['url'])
                content = b'new verified source capture'
                digest = hashlib.sha256(content).hexdigest()
                blob = store / 'sha256' / digest[:2] / digest
                blob.parent.mkdir(parents=True); blob.write_bytes(content)
                return {'filename': link['filename'], 'state': 'verified', 'sha256': digest, 'bytes': len(content)}
            def github(*command, **kwargs):
                if command[0] == 'api':
                    return json.dumps(remote)
                self.assertEqual(command[:3], ('release', 'upload', 'gpl-1.0'))
                local = Path(command[3])
                remote['assets'].append({'name': local.name, 'size': local.stat().st_size,
                                         'digest': 'sha256:' + hashlib.sha256(local.read_bytes()).hexdigest(),
                                         'browser_download_url': 'fixture://new'})
                return ''
            args = Namespace(output=root, version=[], next=True, limit=2, max_total_bytes=0,
                             filename=[], include_upstream=True, retry_errors=False,
                             continue_errors=True, publish=True, store=root / 'objects',
                             repo='owner/repo', delay=0)
            with patch.object(module, 'download_artifact', side_effect=acquire), \
                 patch.object(module, 'run_gh', side_effect=github), patch.object(module, 'pause'):
                module.archive(args)
                module.archive(args)
            self.assertEqual(acquired, [second])
            data = json.loads((root / 'artifacts.json').read_text())['artifacts']['1.0']
            sources = [row for row in data.values() if row.get('kind') == 'source']
            self.assertEqual({row['source_url'] for row in sources}, {first, second})
            self.assertEqual(data['source.tar.xz']['sha256'], old_hash)
            self.assertEqual(len(remote['assets']), 2)
            self.assertTrue(remote['assets'][1]['name'].startswith('direct-'))
            self.assertNotEqual(remote['assets'][0]['digest'], remote['assets'][1]['digest'])


if __name__ == '__main__':
    unittest.main()
