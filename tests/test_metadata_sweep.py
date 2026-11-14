import unittest
import tempfile
import struct
from pathlib import Path
from unittest.mock import patch, Mock

from sonos_firmware.metadata_sweep import BASELINE, PROFILE_FIELDS, build_profiles, discover_profiles, discover_version_hints


class MetadataSweepTests(unittest.TestCase):
    def test_public_version_hints_feed_profile_queries_and_survive_a_source_outage(self):
        hints = discover_version_hints({}, fetch=lambda: b'<p>Sonos 99.1-12345</p>')
        self.assertEqual(hints['versions'], ['99.1-12345'])
        planned = build_profiles({'evidence': [{'version': v} for v in hints['versions']]}, 100)
        self.assertTrue(any(row['cmaj'] == 99 and row['cmin'] == 1 and row['cbld'] == 12345 for row in planned))
        fetch = Mock()
        discover_version_hints(hints, fetch=fetch)
        fetch.assert_not_called()
        hints['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
        def failed():
            raise TimeoutError('support page unavailable')
        failed_hints = discover_version_hints(hints, fetch=failed)
        self.assertEqual(failed_hints['versions'], ['99.1-12345'])
        self.assertEqual(failed_hints['status'], 'failed')
        discover_version_hints(failed_hints, fetch=fetch)
        fetch.assert_not_called()

    def test_malformed_metadata_retains_a_traceable_response_and_a_failed_profile(self):
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = discover_profiles({}, Path(temporary), {}, limit=1, count=1, fetch=lambda p: b'malformed UPS')
            profile = next(iter(state['profiles'].values()))
            captured = state['responses'][profile['response_sha256']]
            self.assertEqual((Path(temporary)/captured['snapshot']).read_bytes(), b'malformed UPS')
        self.assertEqual(profile['status'], 'failed')
        self.assertEqual(captured['records'], [])
        self.assertIn('parse_error', captured)

    def test_scheduled_profiles_resume_without_requiring_local_binary_inputs(self):
        seen = []
        uri = b'https://update.sonos.com/firmware/live/update.upm\0'
        body = struct.pack('<IIII', 0x35167F49, 14, 16+len(uri), 0) + uri
        def fetch(profile):
            seen.append(profile)
            return body
        state = {}
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            for _ in range(3):
                state = discover_profiles(state, Path(temporary), {}, limit=1, count=3, fetch=fetch)
            self.assertEqual(len(list(Path(temporary).glob('*.ups'))), 1)
            again = discover_profiles(state, Path(temporary), {}, limit=1, count=3,
                                      fetch=lambda p: self.fail('completed profile reacquired early'))
        self.assertEqual(len({tuple(row.values()) for row in seen}), 3)
        self.assertEqual(len(state['profiles']), 3)
        self.assertEqual(len(state['responses']), 1)
        self.assertEqual(again['attempted_this_run'], 0)
        self.assertEqual(again['pending_profile_total'], 0)
        self.assertEqual(again['status'], 'ok')

    def test_failed_profiles_back_off_while_other_profiles_continue(self):
        calls = []
        body = struct.pack('<IIII', 0x35167F49, 99, 17, 0) + b'x'
        def fetch(profile):
            calls.append(profile)
            if profile == BASELINE:
                raise TimeoutError('temporary metadata outage')
            return body
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = discover_profiles({}, Path(temporary), {}, limit=2, count=3, fetch=fetch)
            self.assertEqual(len(state['errors']), 1)
            self.assertEqual(len(state['responses']), 1)
            self.assertEqual(state['pending_profile_total'], 2)
            self.assertEqual(state['status'], 'partial')
            state = discover_profiles(state, Path(temporary), {}, limit=1, count=3, fetch=fetch)
            self.assertEqual(len(calls), 3)
            self.assertEqual(sum(row == BASELINE for row in calls), 1)
            failed = next(row for row in state['profiles'].values() if row['status'] == 'failed')
            failed['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
            state = discover_profiles(state, Path(temporary), {}, limit=1, count=3, fetch=lambda p: body)
            self.assertTrue(all(row['status'] == 'observed' for row in state['profiles'].values()))

    def test_profile_deadline_leaves_the_queue_available_for_a_later_worker(self):
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.monotonic', return_value=2):
            state = discover_profiles({}, Path(temporary), {}, limit=2, count=2, deadline=1,
                                      fetch=lambda p: self.fail('profile fetched after deadline'))
        self.assertEqual(state['attempted_this_run'], 0)
        self.assertEqual(state['profiles'], {})

    def test_profile_plan_is_deterministic_unique_and_bounded(self):
        catalog = {
            "packages": [{"version": "97.1-80312"}],
            "evidence": [{"version": "86.8-78270"}],
        }
        first = build_profiles(catalog, 800)
        second = build_profiles(catalog, 800)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 800)
        self.assertEqual(
            len({tuple(item[field] for field in PROFILE_FIELDS) for item in first}), 800
        )
        self.assertEqual(first[0], BASELINE)
        self.assertIn(
            {"cmaj": 97, "cmin": 1, "cbld": 80312, "subm": 1, "rev": 1, "reg": 2},
            first,
        )


if __name__ == "__main__":
    unittest.main()
