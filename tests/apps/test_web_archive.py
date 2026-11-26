"""First-time web capture and interrupted publication use the real publisher."""
import hashlib
import tempfile
import unittest
import subprocess
import urllib.error
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import releases, release_series, web_archive as web
from tests.apps.test_app_archive import FakeGitHub


class WebArchiveTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.receipt = self.root/'data/apps/web-app-archive.json'
        self.github = FakeGitHub()
        self.uploads = []
        self.lose_response = False
        for mocked in (
            patch.object(releases, 'remote_assets', side_effect=self.github.assets),
            patch.object(releases.subprocess, 'run', side_effect=self.github.command),
            patch.object(releases, 'public_release', side_effect=lambda repo, tag: {'id': tag}),
            patch.object(releases, 'upload_streaming', side_effect=self.upload),
            patch.object(release_series, 'matching_tags', side_effect=self.github.matching_tags),
            patch.object(releases.time, 'sleep'),
            patch.object(web, 'fetch', side_effect=self.fetch),
        ):
            mocked.start()
            self.addCleanup(mocked.stop)

    def fetch(self, url):
        body = b'<html><script src="/_next/static/app.js"></script></html>' if url.endswith('/') else b'window.fixture=1;'
        return {'requested_url': url, 'final_url': url, 'status': 200, 'redirects': [],
                'headers': {'content-type': 'text/html' if url.endswith('/') else 'application/javascript'}, 'body': body}

    def upload(self, repository, tag, name, path):
        if len(self.github.releases[(repository, tag)]) >= 1000:
            raise subprocess.CalledProcessError(1, 'upload', stderr='file_count limited to 1000 assets per release')
        body = path.read_bytes()
        self.github.releases[(repository, tag)][name] = {
            'size': len(body), 'digest': 'sha256:'+hashlib.sha256(body).hexdigest(),
            'browser_download_url': f'https://github.com/{repository}/releases/download/{tag}/{name}'}
        self.uploads.append(name)
        if self.lose_response and name.endswith('complete.tar.gz'):
            self.lose_response = False
            self.github.inventory_failure = urllib.error.URLError('inventory unavailable after lost upload response')
            raise TimeoutError('response lost after publication')
        return self.github.releases[(repository, tag)][name]

    def test_full_release_continues_without_deleting_earlier_capture_assets(self):
        tag = web.APPS['pro']['release_tag']
        old = {f'20200101T000000Z--old-{i}.js': {'size': 1} for i in range(1000)}
        self.github.releases[('fresh/archive', tag)] = dict(old)
        result = self.run_archive()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(self.github.releases[('fresh/archive', tag)], old)
        captures = [*result['deployments'][0]['resources'], result['deployments'][0]['bundle']]
        self.assertTrue(all(row['release_tag'] == tag+'-part-2' for row in captures))

    def test_capture_crossing_remaining_capacity_records_each_actual_release(self):
        tag = web.APPS['pro']['release_tag']
        self.github.releases[('fresh/archive', tag)] = {f'old-{i}': {'size': 1} for i in range(999)}
        result = self.run_archive()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(self.github.releases[('fresh/archive', tag)]), 1000)
        deployment = result['deployments'][0]
        captures = [*deployment['resources'], deployment['bundle']]
        self.assertEqual({row['release_tag'] for row in captures}, {tag, tag+'-part-2'})

    def test_overflow_recovery_needs_neither_cached_bytes_nor_a_working_site(self):
        tag = web.APPS['pro']['release_tag']
        self.github.releases[('fresh/archive', tag)] = {f'old-{i}': {'size': 1} for i in range(1000)}
        self.lose_response = True
        self.assertEqual(self.run_archive()['status'], 'partial')
        uploads = list(self.uploads)
        with patch.object(web, 'capture', side_effect=OSError('site unavailable')):
            result = web.archive_apps(('pro',), self.root/'empty-worker', self.receipt, 'fresh/archive',
                                      capture_id='fixture', upload=True)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(self.uploads, uploads)
        self.assertTrue(all(row['release_tag'] == tag+'-part-2'
            for row in [*result['deployments'][0]['resources'], result['deployments'][0]['bundle']]))


    def run_archive(self, apps=('pro',), **kwargs):
        return web.archive_apps(apps, self.root/'worker', self.receipt, 'fresh/archive',
                                capture_id='fixture', upload=True, **kwargs)

    def test_empty_archive_creates_its_own_release_and_verifies_every_capture(self):
        self.assertEqual(self.github.releases, {})
        result = self.run_archive()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['pending_total'], 0)
        deployment, = result['deployments']
        self.assertTrue(deployment['complete'])
        self.assertEqual(deployment['captured_total'], 2)
        self.assertEqual(len(self.uploads), 3)
        self.assertNotIn(str(self.root), self.receipt.read_text())
        for capture in [*deployment['resources'], deployment['bundle']]:
            self.assertEqual(capture['status'], 'verified')
            self.assertEqual(capture['repository'], 'fresh/archive')


    def test_public_login_shell_and_its_scripts_are_preserved_without_claiming_controller_access(self):
        entry = web.APPS['controller']['entry_url']
        script = entry+'_next/static/public-login.js'
        calls = []
        def fetch(url):
            calls.append(url)
            result = self.fetch(url)
            if url == entry:
                result.update(final_url=entry+'en-us/login?callbackUrl=fixture',
                              body=b'<html><script src="/_next/static/public-login.js"></script></html>')
            return result
        with patch.object(web, 'fetch', side_effect=fetch):
            result = self.run_archive(('controller',))
        deployment, = result['deployments']
        self.assertIn(script, calls)
        shell = next(row for row in deployment['resources'] if row['requested_url'] == entry)
        self.assertEqual(shell['outcome'], 'gated-or-redirected')
        self.assertEqual(shell['status'], 'verified')
        self.assertIn(shell['release_asset'], self.uploads)
        self.assertTrue(any(row['requested_url'] == script and row['status'] == 'verified'
                            for row in deployment['resources']))
        # The deployment itself was never captured: no identity, so not complete.
        self.assertFalse(deployment['complete'])
        self.assertFalse(deployment['identity_complete'])
        # But nothing retryable remains, so the worker finished what it can do.
        self.assertEqual(deployment['gated_total'], 1)
        self.assertEqual(deployment['actionable_gap_total'], 0)
        self.assertTrue(deployment['public_capture_done'])
        self.assertEqual(result['status'], 'ok')

    def test_login_html_returned_for_a_script_does_not_become_that_script(self):
        entry = web.APPS['pro']['entry_url']
        script = entry+'_next/static/app.js'
        def fetch(url):
            result = self.fetch(url)
            if url == script:
                result.update(final_url=entry+'en-us/login', body=b'<html>Sign in</html>',
                              headers={'content-type': 'text/html'})
            return result
        with patch.object(web, 'fetch', side_effect=fetch):
            result = self.run_archive()
        deployment, = result['deployments']
        row = next(row for row in deployment['resources'] if row['requested_url'] == script)
        self.assertEqual(row['outcome'], 'gated-or-redirected')
        self.assertNotIn('release_asset', row)
        # A gated resource is terminal: no retry retrieves it without credentials,
        # so it must not hold the deployment open as incomplete work.
        self.assertEqual(deployment['gated_total'], 1)
        self.assertEqual(deployment['actionable_gap_total'], 0)
        self.assertTrue(deployment['complete'])


    def test_lost_upload_response_reconciles_without_original_worker_or_source(self):
        self.lose_response = True
        first = self.run_archive()
        self.assertEqual(first['status'], 'partial')
        self.assertEqual(first['deployments'], [])
        self.assertEqual(len(first['pending_captures']), 1)
        uploads = list(self.uploads)
        with patch.object(web, 'capture', side_effect=OSError('source no longer serves original bytes')):
            result = web.archive_apps(('pro',), self.root/'new-worker', self.receipt, 'fresh/archive',
                                      capture_id='fixture', upload=True)
        self.assertEqual(len(result['deployments']), 1)
        self.assertEqual(result['pending_total'], 0)
        self.assertEqual(self.uploads, uploads)
        self.assertNotIn(str(self.root), self.receipt.read_text())
        self.assertEqual(result['status'], 'ok')

    def test_retry_uploads_retained_missing_assets_without_recapturing(self):
        def fail_bundle(repository, tag, name, path):
            if name.endswith('complete.tar.gz'):
                raise OSError('upload failed before storing bundle')
            return self.upload(repository, tag, name, path)
        with patch.object(releases, 'upload_streaming', side_effect=fail_bundle):
            first = self.run_archive()
        self.assertEqual(first['pending_total'], 1)
        with patch.object(web, 'capture', side_effect=AssertionError('must reuse retained capture')) as capture:
            result = self.run_archive()
        capture.assert_not_called()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['pending_total'], 0)
        self.assertEqual(len(self.uploads), 3)


    def test_collection_preflight_rejects_all_uploads_if_a_later_capture_collides(self):
        body = b'new fixture'
        path = self.root/'new.bin'
        path.write_bytes(body)
        capture = {'release_asset': 'new.bin', 'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}
        self.github.releases[('fresh/archive', 'tag')] = {
            'collision': {'size': 1, 'digest': 'sha256:'+'0'*64, 'url': 'https://example.invalid'}}
        with self.assertRaises(RuntimeError):
            releases.publish_collection('fresh/archive', 'tag', [(path, capture),
                (self.root/'missing', {**capture, 'release_asset': 'collision'})])
        self.assertEqual(self.uploads, [])
