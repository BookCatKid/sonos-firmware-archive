"""Work quota arguments are rejected and active workers finish before wind-down."""
from __future__ import annotations

import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from sonos_firmware.archive_index import discover_archives
from sonos_firmware.binary_discovery import discover_preserved
from sonos_firmware.firmware_inventory import inventory_package
from sonos_firmware.gpl_binary_evidence import scan_binary
from sonos_firmware.metadata_sweep import discover_profiles
from sonos_firmware.platform.model import Step, Target
from sonos_firmware.platform.runner import run
from sonos_firmware.retry import select
from sonos_firmware.web_archive import archive_apps

ROOT = Path(__file__).resolve().parents[2]


class NoWorkQuotas(unittest.TestCase):


    def test_unrecognized_work_limits_are_rejected(self):
        cases = (
            (discover_archives, ({},)),
            (discover_preserved, (ROOT, ROOT, 'owner/repo')),
            (inventory_package, (ROOT / 'missing.upd', ROOT / 'missing-dir')),
            (scan_binary, (ROOT / 'missing.bin',)),
            (discover_profiles, ({}, ROOT / 'missing', {})),
            (archive_apps, (('pro',), ROOT, ROOT / 'missing.json', 'owner/repo')),
            (select, ([], {})),
        )
        for function, args in cases:
            for keyword in ('limit', 'max_bytes', 'deadline'):
                with self.subTest(function=function.__name__, keyword=keyword):
                    with self.assertRaisesRegex(TypeError, 'unexpected keyword argument'):
                        function(*args, **{keyword: 1})


    def test_running_step_finishes_after_soft_cutoff_and_next_is_deferred(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            marker = directory / 'finished'
            late = directory / 'new-step'
            command = (sys.executable, '-c',
                       f'import time; from pathlib import Path; '
                       f'time.sleep(.15); Path({str(marker)!r}).touch()')
            next_command = (sys.executable, '-c',
                            f'from pathlib import Path; Path({str(late)!r}).touch()')
            target = Target('test-soft-stop', 'fixture', 'fixture', 1, ('data/test.json',))
            with patch.dict(os.environ, {'SONOS_WIND_DOWN_AT': str(time.time()+.04)}):
                report = run(
                    target, directory, 'owner/repo',
                    environment={**os.environ, 'GITHUB_ACTIONS': 'true', 'RUNNER_TEMP': temporary},
                    provider_map={'fixture': lambda context: [Step('finish', command), Step('defer', next_command)]})
            self.assertTrue(marker.exists(), report)
            self.assertFalse(late.exists(), report)
            self.assertEqual([r['status'] for r in report['steps']], ['ok', 'skipped'])
            self.assertEqual(report['status'], 'partial')
            self.assertIn('wind-down', report['pending'][0]['reason'])


if __name__ == '__main__':
    unittest.main()
