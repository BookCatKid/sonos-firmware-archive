import unittest
import tempfile
import struct
import urllib.error
import itertools
from datetime import datetime, timedelta, timezone
from threading import Event
from pathlib import Path
from unittest.mock import patch, Mock

from sonos_firmware.metadata_sweep import (BASELINE, PROFILE_FIELDS, discover_client_reports,
                                          discover_profiles as discover_profiles_worker, discover_version_hints)


def run_profiles(previous, evidence, catalog, *, fetch, requests=1):
    stop = Event()
    calls = 0

    def sample(profile):
        nonlocal calls
        try:
            return fetch(profile)
        finally:
            calls += 1
            if calls >= requests:
                stop.set()

    if requests == 0:
        stop.set()
    return discover_profiles_worker(previous, evidence, catalog, fetch=sample, stop=stop)


class MetadataSweepTests(unittest.TestCase):

    def test_profile_server_retry_deadline_survives_restart_and_resumes(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        def throttle(profile):
            raise urllib.error.HTTPError('https://update.sonos.com/fixture', 429,
                'retry later', {'Retry-After': '75'}, None)
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            directory = Path(temporary)
            with patch('sonos_firmware.retry._now', return_value=now):
                state = run_profiles({}, directory, {}, requests=1, fetch=throttle)
            expected = (now+timedelta(seconds=75)).isoformat()
            self.assertEqual(state['source_backoff']['next_attempt_at'], expected)
            self.assertEqual(next(iter(state['profiles'].values()))['next_attempt_at'], expected)
            fetch = Mock(return_value=self.body())
            with patch('sonos_firmware.retry._now', return_value=now+timedelta(seconds=74)):
                state = run_profiles(state, directory, {}, requests=1, fetch=fetch)
            fetch.assert_not_called()
            with patch('sonos_firmware.retry._now', return_value=now+timedelta(seconds=76)):
                resumed = run_profiles(state, directory, {}, requests=1, fetch=fetch)
            self.assertEqual(resumed['attempted_this_run'], 1)
            self.assertEqual(resumed['source_backoff'], {})

    def test_release_note_hints_honor_server_retry_after_across_restarts(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        def throttle():
            raise urllib.error.HTTPError('https://support.sonos.com/fixture', 429,
                'retry later', {'Retry-After': '75'}, None)
        with patch('sonos_firmware.retry._now', return_value=now):
            state = discover_version_hints({}, fetch=throttle)
        self.assertEqual(state['next_attempt_at'], (now+timedelta(seconds=75)).isoformat())
        fetch = Mock(return_value=b'<p>Sonos 99.1-12345</p>')
        with patch('sonos_firmware.retry._now', return_value=now+timedelta(seconds=74)):
            state = discover_version_hints(state, fetch=fetch)
        fetch.assert_not_called()
        with patch('sonos_firmware.retry._now', return_value=now+timedelta(seconds=76)):
            resumed = discover_version_hints(state, fetch=fetch)
        self.assertEqual(resumed['versions'], ['99.1-12345'])

    @staticmethod
    def body():
        uri = b'https://update.sonos.com/firmware/live/update.upm\0'
        return struct.pack('<IIII', 0x35167F49, 14, 16+len(uri), 0) + uri

    def test_combination_lane_covers_a_complete_six_axis_cube(self):
        key = ':'.join(str(BASELINE[field]) for field in PROFILE_FIELDS)
        state = {'profiles': {key: {'profile': BASELINE, 'status': 'observed',
            'next_attempt_at': '2099-01-01T00:00:00+00:00'}}}
        seen = []
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            for coordinate in range(64):
                state['profile_exploration'] = {'generator_version': 1, 'next_profile_index': 1+8*coordinate+7}
                state = run_profiles(state, Path(temporary), {}, requests=1,
                    fetch=lambda profile: seen.append(tuple(profile[field] for field in PROFILE_FIELDS)) or self.body())
        self.assertEqual(set(seen), set(itertools.product((0, 1), repeat=6)))


    def test_a_continuing_stream_of_hints_cannot_starve_broad_exploration(self):
        seen, state = [], {}
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            for minor in range(7):
                state = run_profiles(state, Path(temporary), {'evidence': [{'version': f'777.{minor}-12345'}]}, requests=1, fetch=lambda profile: seen.append(profile) or self.body())
                self.assertEqual(state['attempted_this_run'], 1)
        self.assertTrue(any(profile['cmaj'] == 0 for profile in seen))
        self.assertTrue(any(profile['cmin'] == 0 for profile in seen))
        self.assertGreaterEqual(state['profile_exploration']['next_profile_index'], 4)


    def test_malformed_metadata_retains_a_traceable_response_and_a_failed_profile(self):
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = run_profiles({}, Path(temporary), {}, requests=1, fetch=lambda p: b'malformed UPS')
            profile = next(iter(state['profiles'].values()))
            captured = state['responses'][profile['response_sha256']]
            self.assertEqual((Path(temporary)/captured['snapshot']).read_bytes(), b'malformed UPS')
        self.assertEqual(profile['status'], 'failed')
        self.assertEqual(captured['records'], [])
        self.assertIn('parse_error', captured)


    def test_profile_cursor_continues_across_worker_wind_downs(self):
        uri = b'https://update.sonos.com/firmware/live/update.upm\0'
        body = struct.pack('<IIII', 0x35167F49, 14, 16+len(uri), 0) + uri
        state, seen = {}, []
        def fetch(profile):
            seen.append(profile)
            return body
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            for _ in range(4):
                state = run_profiles(state, Path(temporary), {}, requests=1, fetch=fetch)
        self.assertEqual(len(state['profiles']), 4)
        self.assertEqual(state['profile_exploration']['next_profile_index'], 4)
        self.assertEqual(len({tuple(profile.values()) for profile in seen}), 4)


    def test_new_version_hints_take_priority_without_losing_older_pending_profiles(self):
        uri = b'https://update.sonos.com/firmware/live/update.upm\0'
        body = struct.pack('<IIII', 0x35167F49, 14, 16+len(uri), 0) + uri
        seen = []
        def fetch(profile):
            seen.append(profile)
            return body
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = run_profiles({}, Path(temporary), {}, requests=1, fetch=fetch)
            state = run_profiles(state, Path(temporary), {'evidence': [{'version': '1000.5-5000000'}]}, requests=2, fetch=fetch)
            self.assertEqual(seen[-1]['cmaj'], 1000)
            for _ in range(2):
                state = run_profiles(state, Path(temporary), {}, requests=2, fetch=fetch)
        self.assertTrue(any(profile == {**BASELINE, 'cmaj': 0} for profile in seen))
        self.assertEqual({profile['subm'] for profile in seen if profile['cmaj'] == 1000}, {0, 1, 100})


class PublicReportSearchTests(unittest.TestCase):
    STAMP = '2026-01-01T00:00:00Z'

    def report(self, start, count):
        body = ('ZoneGroupTopology availableSoftwareUpdate manifestURL=https://update-firmware.sonos.com/'
                'firmware/Prod/62.1-88070-v13.0.5-lqipsm-GA-1/update.upm')
        return [{'id': start+offset, 'html_url': 'https://github.com/x/y/issues/'+str(start+offset),
                 'repository_url': 'https://api.github.com/repos/x/y',
                 'updated_at': self.STAMP, 'body': body,
                 'comments': 0, 'comments_url': 'https://api.github.com/repos/x/y/issues/'+str(start+offset)+'/comments'}
                for offset in range(count)]


    def test_a_saturated_bucket_rotates_behind_later_windows(self):
        fetch = Mock(return_value={'items': self.report(0, 20), 'total_count': 500})
        record = {'windows': [{'start': self.STAMP, 'end': self.STAMP,
                              'created_start': self.STAMP, 'created_end': self.STAMP},
                             {'start': self.STAMP, 'end': '2026-06-01T00:00:00Z'}]}
        with patch('sonos_firmware.metadata_sweep.winding_down', side_effect=lambda: fetch.call_count >= 2):
            state = discover_client_reports({'queries': {'"manifestURL" sonos in:body,comments is:issue is:public': record}},
                                            fetch=fetch)
        remaining = state['queries']['"manifestURL" sonos in:body,comments is:issue is:public']['windows']
        self.assertTrue(remaining[-1].get('saturated'))
        self.assertNotIn('saturated', remaining[0])
        self.assertTrue(state['errors'])

    def test_comment_traces_keep_their_own_provenance_and_scan_once(self):
        issue = {'id': 7, 'html_url': 'https://github.com/x/y/issues/7', 'updated_at': self.STAMP,
                 'repository_url': 'https://api.github.com/repos/x/y', 'body': 'no links',
                 'comments': 1, 'comments_url': 'https://api.github.com/repos/x/y/issues/7/comments'}
        body = ('ZoneGroupTopology manifestURL=https://update-firmware.sonos.com/'
                'firmware/Prod/62.1-88070-v13.0.5-lqipsm-GA-1/update.upm')
        comments = Mock(return_value=[{'html_url': 'https://github.com/x/y/issues/7#issuecomment-1',
                                       'updated_at': self.STAMP, 'body': body}])
        state = discover_client_reports({},
                                       fetch=Mock(return_value={'items': [issue], 'total_count': 1}),
                                       fetch_comments=comments)
        proof = state['roots']['https://update-firmware.sonos.com/firmware/Prod/'
                               '62.1-88070-v13.0.5-lqipsm-GA-1/update.upm'][0]
        self.assertTrue(proof['report_url'].endswith('#issuecomment-1'))
        self.assertEqual(state['comment_queue'], {})
        discover_client_reports(state,
                                fetch=Mock(return_value={'items': [issue], 'total_count': 1}),
                                fetch_comments=comments)
        self.assertEqual(comments.call_count, 1)


if __name__ == "__main__":
    unittest.main()
