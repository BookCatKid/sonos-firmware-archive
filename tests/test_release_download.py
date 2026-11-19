import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import urllib.error

from sonos_firmware import releases


class ReleaseDownloadTests(unittest.TestCase):
    def receipt(self, body):
        return {'release_tag': '-literal-tag', 'release_asset': 'literal[1]*?.bin',
                'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()}

    def process(self, command, body, *, running=False):
        output = Path(command[command.index('--output')+1])
        output.write_bytes(body)
        class Process:
            returncode = None if running else 0
            killed = False
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def poll(self): return self.returncode
            def kill(self): self.killed = True; self.returncode = -9
            def wait(self): return self.returncode
        return Process()

    def test_literal_asset_is_verified_before_installation_and_existing_capture_is_reused(self):
        body = b'public fixture'
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)/'source.bin'
            commands = []
            def download(command, **kwargs):
                commands.append(command)
                return self.process(command, body)
            with patch.object(releases.subprocess, 'Popen', side_effect=download) as process:
                releases.download_verified(self.receipt(body), target, 'owner/repo')
                releases.download_verified(self.receipt(body), target, 'owner/repo')
                process.assert_called_once()
            self.assertEqual(target.read_bytes(), body)
            self.assertIn('--pattern=literal\\[1\\]\\*\\?.bin', commands[0])
            self.assertEqual(commands[0][-2:], ['--', '-literal-tag'])
            self.assertEqual(list(Path(temporary).iterdir()), [target])

    def test_mismatched_body_leaves_no_capture_and_oversized_stream_is_stopped(self):
        for body, running in ((b'wrong', False), (b'too large', True)):
            with self.subTest(running=running), tempfile.TemporaryDirectory() as temporary:
                target = Path(temporary)/'source.bin'
                processes = []
                def download(command, **kwargs):
                    process = self.process(command, body, running=running)
                    processes.append(process)
                    return process
                with patch.object(releases.subprocess, 'Popen', side_effect=download):
                    with self.assertRaises(ValueError):
                        releases.download_verified(self.receipt(b'valid'), target, 'owner/repo')
                self.assertFalse(target.exists())
                self.assertEqual(list(Path(temporary).iterdir()), [])
                if running:
                    self.assertTrue(processes[0].killed)

    def test_an_existing_different_capture_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            target = Path(temporary)/'source.bin'
            target.write_bytes(b'original')
            with patch.object(releases.subprocess, 'Popen') as process:
                with self.assertRaises(FileExistsError):
                    releases.download_verified(self.receipt(b'new bytes'), target, 'owner/repo')
            process.assert_not_called()
            self.assertEqual(target.read_bytes(), b'original')


class AnonymousReadThrottleTests(unittest.TestCase):
    """Anonymous REST throttling must not fail a run that can authenticate.

    GitHub reports the primary allowance as 403 and burst/secondary limits as
    429. Only a genuinely absent resource may stay an error.
    """

    GRAPHQL_RELEASE = {'data': {'repository': {'release': {
        'databaseId': 7, 'url': 'https://github.com/owner/archive/releases/tag/fixture',
        'tagName': 'fixture'}}}}

    GRAPHQL_ASSETS = {'data': {'repository': {'release': {'releaseAssets': {
        'nodes': [{'id': 'a1', 'name': 'source.bin', 'size': 10, 'digest': 'sha256:'+'a'*64,
                   'downloadUrl': 'https://github.com/owner/archive/releases/download/fixture/source.bin'}],
        'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}}

    def test_both_anonymous_throttle_statuses_use_the_authenticated_release_lookup(self):
        for status in (403, 429):
            with self.subTest(status=status), \
                 patch.object(releases.urllib.request, 'urlopen',
                              side_effect=urllib.error.HTTPError('https://api.github.com/fixture',
                                                                 status, 'throttled', {}, None)), \
                 patch.object(releases.subprocess, 'check_output',
                              return_value=json.dumps(self.GRAPHQL_RELEASE)) as client:
                release = releases.public_release('owner/archive', 'fixture')
            self.assertEqual(release['id'], 7)
            self.assertEqual(release['tag_name'], 'fixture')
            client.assert_called_once()

    def test_a_missing_release_stays_missing_instead_of_being_retried_as_throttling(self):
        with patch.object(releases.urllib.request, 'urlopen',
                          side_effect=urllib.error.HTTPError('https://api.github.com/fixture',
                                                             404, 'missing', {}, None)), \
             patch.object(releases.subprocess, 'check_output') as client:
            with self.assertRaises(urllib.error.HTTPError) as raised:
                releases.public_release('owner/archive', 'fixture')
        self.assertEqual(raised.exception.code, 404)
        client.assert_not_called()

    def test_a_throttled_asset_page_falls_back_before_returning_a_partial_inventory(self):
        first_page = [{'name': f'asset-{i}.bin', 'size': 1, 'digest': 'sha256:'+'a'*64,
                       'browser_download_url': f'https://github.com/owner/archive/releases/download/fixture/asset-{i}.bin'}
                      for i in range(100)]
        responses = [first_page,
                     urllib.error.HTTPError('https://api.github.com/fixture', 429, 'throttled',
                                            {'Retry-After': '30'}, None)]
        def read(request, timeout=None):
            item = responses.pop(0)
            if isinstance(item, Exception):
                raise item
            return io.BytesIO(json.dumps(item).encode())
        with patch.object(releases, 'public_release', return_value={'id': 7}), \
             patch.object(releases.urllib.request, 'urlopen', side_effect=read), \
             patch.object(releases, 'graphql_assets', return_value=[
                 {'name': 'complete.bin', 'size': 1, 'digest': 'sha256:'+'b'*64,
                  'browser_download_url': 'https://github.com/owner/archive/releases/download/fixture/complete.bin'}]) as graphql:
            assets = releases.remote_assets('owner/archive', 'fixture')
        # A page-2 throttle must not be reported as a full 100-asset inventory.
        self.assertEqual(set(assets), {'complete.bin'})
        graphql.assert_called_once_with('owner/archive', 'fixture')
