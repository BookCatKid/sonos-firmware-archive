import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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
