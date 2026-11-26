import unittest
import tempfile
import struct
import urllib.error
import itertools
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, Mock

from sonos_firmware.metadata_sweep import (BASELINE, PROFILE_FIELDS, _profile_at, _profile_key, _version_profiles, discover_client_reports,
                                          discover_profiles, discover_version_hints)


def profile_preview(catalog, count):
    """Finite fixture sample of the open-ended production profile generator."""
    profiles = {_profile_key(profile): profile for profile in _version_profiles(catalog)}
    index = 1
    while len(profiles) < count:
        candidate = _profile_at(index)
        profiles.setdefault(_profile_key(candidate), candidate)
        index += 1
    return list(profiles.values())[:count]


class MetadataSweepTests(unittest.TestCase):
    def test_profile_service_unavailable_pauses_untried_profiles(self):
        fetch = Mock(side_effect=urllib.error.HTTPError('https://update.sonos.com/fixture', 503,
            'temporarily unavailable', {'Retry-After': '75'}, None))
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = discover_profiles({}, Path(temporary), {}, limit=2, count=2, fetch=fetch)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(state['source_backoff']['http_status'], 503)
            discover_profiles(state, Path(temporary), {}, limit=2, count=2, fetch=fetch)
            self.assertEqual(fetch.call_count, 1)

    def test_profile_server_retry_deadline_survives_restart_and_resumes(self):
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        def throttle(profile):
            raise urllib.error.HTTPError('https://update.sonos.com/fixture', 429,
                'retry later', {'Retry-After': '75'}, None)
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            directory = Path(temporary)
            with patch('sonos_firmware.retry._now', return_value=now):
                state = discover_profiles({}, directory, {}, limit=1, count=1, fetch=throttle)
            expected = (now+timedelta(seconds=75)).isoformat()
            self.assertEqual(state['source_backoff']['next_attempt_at'], expected)
            self.assertEqual(next(iter(state['profiles'].values()))['next_attempt_at'], expected)
            fetch = Mock(return_value=self.body())
            with patch('sonos_firmware.retry._now', return_value=now+timedelta(seconds=74)):
                state = discover_profiles(state, directory, {}, limit=1, count=1, fetch=fetch)
            fetch.assert_not_called()
            with patch('sonos_firmware.retry._now', return_value=now+timedelta(seconds=76)):
                resumed = discover_profiles(state, directory, {}, limit=1, count=1, fetch=fetch)
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
                state = discover_profiles(state, Path(temporary), {}, limit=1, count=1,
                    fetch=lambda profile: seen.append(tuple(profile[field] for field in PROFILE_FIELDS)) or self.body())
        self.assertEqual(set(seen), set(itertools.product((0, 1), repeat=6)))

    def test_one_slot_keeps_exploring_when_a_saved_profile_always_fails(self):
        from sonos_firmware import retry
        from tests.apps.test_app_archive import NOW
        profile = {**BASELINE, 'cmaj': 777}
        key = ':'.join(str(profile[field]) for field in PROFILE_FIELDS)
        state = {'profiles': {key: {'profile': profile, 'status': 'failed',
                                   'checked_at': '2000-01-01T00:00:00+00:00'}}}
        seen = []
        def fetch(profile):
            seen.append(profile['cmaj'])
            if profile['cmaj'] == 777:
                raise TimeoutError('fixture profile failure')
            return self.body()
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            for index in range(4):
                with patch.object(retry, '_now', return_value=NOW+timedelta(days=30*index)):
                    state = discover_profiles(state, Path(temporary), {}, limit=1, count=1, fetch=fetch)
                state = json.loads(json.dumps(state))
        self.assertEqual(seen[::2], [777, 777])
        self.assertTrue(all(major != 777 for major in seen[1::2]))

    def test_failed_version_profile_remains_retryable_after_its_hint_disappears(self):
        def fetch(profile):
            if profile['cmaj'] == 777:
                raise TimeoutError('temporary version-specific failure')
            return self.body()
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = discover_profiles({}, Path(temporary), {'packages': [{'version': '777.1-12345'}]},
                                      limit=2, count=2, fetch=fetch)
            failed = next(row for row in state['profiles'].values() if row['status'] == 'failed')
            failed['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
            seen = []
            state = discover_profiles(state, Path(temporary), {}, limit=1, count=2,
                                      fetch=lambda profile: seen.append(profile) or self.body())
        self.assertEqual(seen[0]['cmaj'], 777)
        self.assertEqual(next(row for row in state['profiles'].values() if row['profile'] == seen[0])['status'], 'observed')

    def test_access_challenge_cooldown_cannot_be_bypassed_by_new_profiles(self):
        def challenge(profile):
            raise urllib.error.HTTPError('fixture', 429, 'retry later', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = discover_profiles({}, Path(temporary), {}, limit=1, count=1, fetch=challenge)
            blocked_fetch = Mock()
            state = discover_profiles(state, Path(temporary), {}, limit=1, count=1, fetch=blocked_fetch)
            blocked_fetch.assert_not_called()
            self.assertEqual(state['attempted_this_run'], 0)
            self.assertEqual(state['source_backoff']['http_status'], 429)
            state['source_backoff']['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
            state = discover_profiles(state, Path(temporary), {}, limit=1, count=1, fetch=lambda profile: self.body())
        self.assertEqual(state['attempted_this_run'], 1)
        self.assertEqual(state['source_backoff'], {})

    def test_a_continuing_stream_of_hints_cannot_starve_broad_exploration(self):
        seen, state = [], {}
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            for minor in range(7):
                state = discover_profiles(state, Path(temporary), {'evidence': [{'version': f'777.{minor}-12345'}]},
                    limit=1, count=1, fetch=lambda profile: seen.append(profile) or self.body())
                self.assertEqual(state['attempted_this_run'], 1)
        self.assertTrue(any(profile['cmaj'] == 0 for profile in seen))
        self.assertTrue(any(profile['cmin'] == 0 for profile in seen))
        self.assertGreaterEqual(state['profile_exploration']['next_profile_index'], 4)

    def test_public_version_hints_feed_profile_queries_and_survive_a_source_outage(self):
        hints = discover_version_hints({}, fetch=lambda: b'<p>Sonos 99.1-12345</p>')
        self.assertEqual(hints['versions'], ['99.1-12345'])
        planned = profile_preview({'evidence': [{'version': v} for v in hints['versions']]}, 100)
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
                                      fetch=fetch)
        self.assertEqual(len({tuple(row.values()) for row in seen}), 4)
        self.assertEqual(len(state['profiles']), 3)
        self.assertEqual(len(state['responses']), 1)
        self.assertEqual(again['attempted_this_run'], 1)
        self.assertEqual(again['pending_profile_total'], 2)
        self.assertEqual(again['profile_exploration']['next_profile_index'], 6)

    def test_profile_window_is_not_a_lifetime_limit(self):
        uri = b'https://update.sonos.com/firmware/live/update.upm\0'
        body = struct.pack('<IIII', 0x35167F49, 14, 16+len(uri), 0) + uri
        state, seen = {}, []
        def fetch(profile):
            seen.append(profile)
            return body
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            for _ in range(4):
                state = discover_profiles(state, Path(temporary), {}, limit=1, count=1, fetch=fetch)
        self.assertEqual(len(state['profiles']), 4)
        self.assertEqual(state['profile_exploration']['next_profile_index'], 4)
        self.assertEqual(len({tuple(profile.values()) for profile in seen}), 4)

    def test_profile_cursor_reaches_submodels_above_the_old_255_boundary(self):
        uri = b'https://update.sonos.com/firmware/live/update.upm\0'
        body = struct.pack('<IIII', 0x35167F49, 14, 16+len(uri), 0) + uri
        key = ':'.join(str(BASELINE[field]) for field in PROFILE_FIELDS)
        previous = {'profiles': {key: {'profile': BASELINE, 'status': 'observed',
            'checked_at': '2000-01-01T00:00:00+00:00', 'next_attempt_at': '2099-01-01T00:00:00+00:00'}},
            'profile_exploration': {'generator_version': 1, 'next_profile_index': 1+256*8+3}}
        seen = []
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = discover_profiles(previous, Path(temporary), {}, limit=1, count=1,
                                      fetch=lambda profile: seen.append(profile) or body)
        self.assertEqual(seen, [{**BASELINE, 'subm': 256}])
        self.assertEqual(len(state['profiles']), 2)

    def test_new_version_hints_take_priority_without_losing_older_pending_profiles(self):
        uri = b'https://update.sonos.com/firmware/live/update.upm\0'
        body = struct.pack('<IIII', 0x35167F49, 14, 16+len(uri), 0) + uri
        seen = []
        def fetch(profile):
            seen.append(profile)
            return body
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = discover_profiles({}, Path(temporary), {}, limit=1, count=2, fetch=fetch)
            state = discover_profiles(state, Path(temporary), {'evidence': [{'version': '1000.5-5000000'}]},
                                      limit=1, count=2, fetch=fetch)
            self.assertEqual(seen[-1]['cmaj'], 1000)
            for _ in range(2):
                state = discover_profiles(state, Path(temporary), {}, limit=2, count=2, fetch=fetch)
        self.assertTrue(any(profile == {**BASELINE, 'cmaj': 0} for profile in seen))
        self.assertEqual({profile['subm'] for profile in seen if profile['cmaj'] == 1000}, {0, 1, 100})

    def test_negative_profiles_back_off_and_retry_without_becoming_errors(self):
        uri = b'https://update.sonos.com/firmware/live/update.upm\0'
        body = struct.pack('<IIII', 0x35167F49, 14, 16+len(uri), 0) + uri
        def missing(profile):
            raise urllib.error.HTTPError('fixture', 404, 'no matching profile', {}, None)
        with tempfile.TemporaryDirectory() as temporary, patch('sonos_firmware.metadata_sweep.time.sleep'):
            state = discover_profiles({}, Path(temporary), {}, limit=1, count=1, fetch=missing)
            negative = next(iter(state['profiles'].values()))
            self.assertEqual(negative['status'], 'not-served')
            self.assertEqual(state['errors'], [])
            self.assertEqual(state['negative_profile_total'], 1)
            state = discover_profiles(state, Path(temporary), {}, limit=1, count=1, fetch=lambda profile: body)
            negative = next(row for row in state['profiles'].values() if row['profile'] == BASELINE)
            self.assertEqual(negative['status'], 'not-served')
            negative['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
            state = discover_profiles(state, Path(temporary), {}, limit=1, count=1, fetch=lambda profile: body)
        self.assertEqual(next(row for row in state['profiles'].values() if row['profile'] == BASELINE)['status'], 'observed')

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
        self.assertEqual(len(state['pending_profiles']), 2)
        self.assertEqual(state['profile_exploration']['next_profile_index'], 2)

    def test_profile_plan_is_deterministic_unique_and_bounded(self):
        catalog = {
            "packages": [{"version": "97.1-80312"}],
            "evidence": [{"version": "86.8-78270"}],
        }
        first = profile_preview(catalog, 800)
        second = profile_preview(catalog, 800)
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

    def test_an_indivisible_bucket_drains_before_later_windows(self):
        def fetch(query, page):
            return {'items': self.report(0 if page == 1 else 20, 20 if page == 1 else 5),
                    'total_count': 25}
        record = {'windows': [{'start': self.STAMP, 'end': self.STAMP,
                              'created_start': self.STAMP, 'created_end': self.STAMP},
                             {'start': self.STAMP, 'end': '2026-06-01T00:00:00Z'}]}
        state = discover_client_reports({'queries': {'"manifestURL" sonos in:body,comments is:issue is:public': record}},
                                       pages=1, fetch=fetch)
        remaining = state['queries']['"manifestURL" sonos in:body,comments is:issue is:public']['windows']
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]['end'], '2026-06-01T00:00:00Z')
        self.assertEqual(len(state['roots']), 1)
        self.assertEqual(len(next(iter(state['roots'].values()))), 25)

    def test_a_saturated_bucket_rotates_behind_later_windows(self):
        fetch = Mock(return_value={'items': self.report(0, 20), 'total_count': 500})
        record = {'windows': [{'start': self.STAMP, 'end': self.STAMP,
                              'created_start': self.STAMP, 'created_end': self.STAMP},
                             {'start': self.STAMP, 'end': '2026-06-01T00:00:00Z'}]}
        state = discover_client_reports({'queries': {'"manifestURL" sonos in:body,comments is:issue is:public': record}},
                                       pages=1, fetch=fetch)
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
        state = discover_client_reports({}, pages=1, comments=1,
                                       fetch=Mock(return_value={'items': [issue], 'total_count': 1}),
                                       fetch_comments=comments)
        proof = state['roots']['https://update-firmware.sonos.com/firmware/Prod/'
                               '62.1-88070-v13.0.5-lqipsm-GA-1/update.upm'][0]
        self.assertTrue(proof['report_url'].endswith('#issuecomment-1'))
        self.assertEqual(state['comment_queue'], {})
        discover_client_reports(state, pages=1, comments=1,
                                fetch=Mock(return_value={'items': [issue], 'total_count': 1}),
                                fetch_comments=comments)
        self.assertEqual(comments.call_count, 1)


if __name__ == "__main__":
    unittest.main()
