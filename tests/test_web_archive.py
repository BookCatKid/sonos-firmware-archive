"""First-time web capture and interrupted publication use the real publisher."""
import hashlib
import json
import tempfile
import unittest
import sys
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import releases, web_archive as web
from test_app_archive import FakeGitHub


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
        body = path.read_bytes()
        self.github.releases[(repository, tag)][name] = {
            'size': len(body), 'digest': 'sha256:'+hashlib.sha256(body).hexdigest(),
            'browser_download_url': f'https://github.com/{repository}/releases/download/{tag}/{name}'}
        self.uploads.append(name)
        if self.lose_response and name.endswith('complete.tar.gz'):
            self.lose_response = False
            raise TimeoutError('response lost after publication')

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

    def test_failed_site_does_not_block_another_site_or_report_complete(self):
        def fetch(url):
            if 'play.sonos.com' in url:
                raise OSError('fixture source outage')
            return self.fetch(url)
        with patch.object(web, 'fetch', side_effect=fetch):
            result = self.run_archive(('controller', 'pro'))
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['observations']['fresh/archive']['pro']['status'], 'published')
        self.assertEqual(result['observations']['fresh/archive']['controller']['status'], 'partial')
        self.assertEqual({row['app'] for row in result['deployments']}, {'pro', 'controller'})

    def test_resource_budget_is_a_durable_gap(self):
        result = self.run_archive(max_assets=1)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(result['deployments'][0]['pending_urls'], ['https://pro.sonos.com/_next/static/app.js'])
        self.assertFalse(result['deployments'][0]['complete'])

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
        self.assertFalse(deployment['complete'])
        self.assertFalse(deployment['identity_complete'])
        self.assertEqual(result['status'], 'partial')

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

    def test_an_actionable_failure_keeps_the_deployment_incomplete(self):
        """A network error is retryable, so it must still block completion."""
        entry = web.APPS['pro']['entry_url']
        script = entry+'_next/static/app.js'
        def fetch(url):
            if url == script:
                raise OSError('connection reset')
            return self.fetch(url)
        with patch.object(web, 'fetch', side_effect=fetch):
            result = self.run_archive()
        deployment, = result['deployments']
        row = next(row for row in deployment['resources'] if row['requested_url'] == script)
        self.assertEqual(row['outcome'], 'network-error')
        self.assertEqual(deployment['gated_total'], 0)
        self.assertEqual(deployment['actionable_gap_total'], 1)
        self.assertFalse(deployment['complete'])
        self.assertEqual(result['status'], 'partial')

    def test_bounded_batches_resume_pending_urls_and_retain_every_prior_capture(self):
        def fetch(url):
            result = self.fetch(url)
            if url.endswith('/'):
                result['body'] = b'<script src="/_next/static/a.js"></script><script src="/_next/static/b.js"></script>'
            return result
        with patch.object(web, 'fetch', side_effect=fetch):
            first = self.run_archive(max_assets=2)
            self.assertEqual(first['status'], 'partial')
            result = web.archive_apps(('pro',), self.root/'next-worker', self.receipt, 'fresh/archive',
                                      capture_id='next', upload=True, max_assets=2)
        self.assertEqual(result['status'], 'ok')
        deployment, = result['deployments']
        self.assertTrue(deployment['complete'])
        self.assertEqual(deployment['pending_urls'], [])
        self.assertEqual({row['requested_url'] for row in deployment['resources']},
                         {'https://pro.sonos.com/', 'https://pro.sonos.com/_next/static/a.js',
                          'https://pro.sonos.com/_next/static/b.js'})
        self.assertEqual(len(deployment['bundles']), 2)

    def test_repeated_capture_keeps_every_verified_publication_for_audit(self):
        from test_release_audit import audit
        self.run_archive()
        for capture_id in ('next', 'third', 'third'):
            result = web.archive_apps(('pro',), self.root/capture_id, self.receipt, 'fresh/archive',
                                      capture_id=capture_id, upload=True)
        deployment, = result['deployments']
        self.assertEqual(deployment['captured_total'], 2)
        expected = {}
        audit.walk_release_records(result, 'web-receipt', expected, 'fresh/archive')
        actual = {(tag, name) for (repository, tag), assets in self.github.releases.items()
                  for name in assets}
        self.assertEqual(set(expected), actual)

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
            self.upload(repository, tag, name, path)
        with patch.object(releases, 'upload_streaming', side_effect=fail_bundle):
            first = self.run_archive()
        self.assertEqual(first['pending_total'], 1)
        with patch.object(web, 'capture', side_effect=AssertionError('must reuse retained capture')) as capture:
            result = self.run_archive()
        capture.assert_not_called()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['pending_total'], 0)
        self.assertEqual(len(self.uploads), 3)

    def test_release_creation_failure_can_retry_the_retained_bundle(self):
        with patch.object(web, 'ensure_release', side_effect=OSError('release creation unavailable')):
            first = self.run_archive()
        self.assertEqual(first['pending_total'], 1)
        self.assertEqual(self.github.releases, {})
        with patch.object(web, 'capture', side_effect=AssertionError('must reuse retained capture')) as capture:
            result = self.run_archive()
        capture.assert_not_called()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(self.uploads), 3)

    def test_relative_static_dynamic_and_reexported_modules_are_captured(self):
        bodies = {
            'https://pro.sonos.com/': b'<script src="/_next/static/app.js"></script>',
            'https://pro.sonos.com/_next/static/app.js':
                b'import {value} from "./helper.mjs"; import("./lazy.js"); export * from "./reexport.js";',
            'https://pro.sonos.com/_next/static/helper.mjs': b'export const value=1;',
            'https://pro.sonos.com/_next/static/lazy.js': b'export default 2;',
            'https://pro.sonos.com/_next/static/reexport.js': b'export const other=3;',
        }
        def fetch(url):
            result = self.fetch(url)
            result['body'] = bodies[url]
            return result
        with patch.object(web, 'fetch', side_effect=fetch):
            result = self.run_archive()
        self.assertEqual(result['status'], 'ok')
        deployment, = result['deployments']
        self.assertEqual({row['requested_url'] for row in deployment['resources']}, set(bodies))
        self.assertEqual(len(self.uploads), len(bodies)+1)

    def test_explicit_metadata_resume_clears_pending_capture_and_failed_observation(self):
        self.lose_response = True
        self.assertEqual(self.run_archive()['status'], 'partial')
        metadata = self.root/'worker/pro/fixture/capture.json'
        with patch.object(sys, 'argv', ['archive_web_apps.py', '--app', 'pro', '--repo', 'fresh/archive',
                '--receipt', str(self.receipt), '--resume-metadata', str(metadata)]):
            self.assertEqual(web.main(), 0)
        result = json.loads(self.receipt.read_text())
        self.assertEqual(result['pending_total'], 0)
        self.assertEqual(result['errors'], [])
        self.assertEqual(result['status'], 'ok')

    def test_dotted_capture_ids_keep_distinct_bundle_paths(self):
        first, _ = web.capture('pro', self.root/'worker', capture_id='fixture.one')
        second, _ = web.capture('pro', self.root/'worker', capture_id='fixture.two')
        self.assertNotEqual(first['bundle']['file'], second['bundle']['file'])
        self.assertTrue(Path(first['bundle']['file']).exists())

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
