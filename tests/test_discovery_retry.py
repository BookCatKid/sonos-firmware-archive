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

ROOT = Path(__file__).resolve().parents[1]
NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)


def load(name):
    spec = importlib.util.spec_from_file_location('retry_' + name, ROOT / 'scripts' / (name + '.py'))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def page(version='1.0', filename='source.tar.xz'):
    body = ('<html>Sonos GPL/LGPL Downloads<a href="' + filename + '">source</a></html>').encode()
    return HttpResult(200, body, 'text/html', index_url(version))


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

    def test_single_slot_alternates_across_serialized_checkpoints(self):
        state = {}
        records = {'old': {'checked_at': '2000-01-01T00:00:00+00:00'}}
        selected = []
        for index in range(6):
            chosen = retry.select(['old', f'new-{index}'], records, key=str, limit=1,
                                  state=state, queue='source', now=NOW)
            selected.extend(chosen)
            state = json.loads(json.dumps(state))
        self.assertEqual(selected, ['old', 'new-1', 'old', 'new-3', 'old', 'new-5'])

    def test_independent_queues_do_not_change_each_others_turn(self):
        state = {}
        records = {'old': {'checked_at': '2000-01-01T00:00:00+00:00'}}
        selected = [retry.select(['old', 'new'], records, key=str, limit=1, state=state, queue=queue, now=NOW)
                    for queue in ('a', 'b', 'a', 'b')]
        self.assertEqual(selected, [['old'], ['old'], ['new'], ['new']])
        before = json.loads(json.dumps(state))
        self.assertEqual(retry.select(['old'], records, key=str, limit=0, state=state, queue='a', now=NOW), [])
        self.assertEqual(state, before)

    def test_observed_versions_are_not_dropped_by_speculative_guess_bounds(self):
        from sonos_firmware.gpl_archive import candidates
        leads = candidates(set(), evidence={'1000.1000': {'https://www.sonos.com/documents/gpl/1000.1000/gpl.html'}})
        self.assertEqual([row['version'] for row in leads], ['1000.1000'])

    def test_new_and_old_formats_are_due_without_manual_migration(self):
        self.assertTrue(retry.due(None, NOW))
        self.assertTrue(retry.due({'checked_at': NOW.isoformat()}, NOW))
        self.assertTrue(retry.due({'next_attempt_at': 'invalid'}, NOW))

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

    def test_queue_reserves_capacity_for_retries_and_new_leads(self):
        records = {str(n): {'checked_at': (NOW - timedelta(hours=n)).isoformat()} for n in range(20)}
        items = [{'id': str(n)} for n in range(40)]
        chosen = retry.select(items, records, key=lambda x: x['id'], limit=8, now=NOW)
        self.assertEqual([x['id'] for x in chosen[:2]], ['19', '18'])
        self.assertEqual([x['id'] for x in chosen[2:]], [str(n) for n in range(20, 26)])
        self.assertEqual(len(retry.select(items[:20], records, key=lambda x: x['id'], limit=8, now=NOW)), 8)

    def test_journaling_unattempted_leads_does_not_take_reserved_retry_capacity(self):
        items = list(range(40))
        records = {n: {'checked_at': (NOW - timedelta(hours=n+1)).isoformat()} for n in range(20)}
        records.update({n: {'status': 'pending', 'candidate': n} for n in range(20, 40)})
        chosen = retry.select(items, records, key=lambda item: item, limit=8, now=NOW)
        self.assertEqual(chosen[:2], [19, 18])
        self.assertEqual(chosen[2:], list(range(20, 26)))

    def test_explicit_cooldown_without_check_time_is_still_respected(self):
        records = {'waiting': {'next_attempt_at': (NOW + timedelta(hours=1)).isoformat()},
                   'new': {'status': 'pending'}}
        self.assertEqual(retry.select(['waiting', 'new'], records, key=str, limit=8, now=NOW), ['new'])


class GPLRetryTests(unittest.TestCase):
    def args(self, root, **extra):
        return Namespace(output=root / 'live', recheck=False, binary=[], discovery_window=1,
                         limit=1, delay=0, **extra)

    def test_completely_empty_directory_bootstraps_its_own_archive_state(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual(list(root.iterdir()), [])
            args = self.args(root)
            with patch.object(module, 'ROOT', root), patch.object(module, 'get_page', return_value=page()), \
                 patch.object(module, 'pause'), patch.object(retry, '_now', return_value=NOW):
                module.discover(args)
            self.assertIn('0.0', module.live_catalog(args.output)['releases'])

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
                module.discover(args)
            fetch.assert_called_once_with(index_url('1000.1000'))
            self.assertIn('1000.1000', module.live_catalog(args.output)['releases'])
            state = json.loads((args.output/'discovery.json').read_text())
            self.assertGreater(state['exploration']['next_ordinal'], ordinal)

    def test_failed_versions_remain_retryable_outside_the_current_numeric_window(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            args.output.mkdir()
            (args.output/'discovery.json').write_text(json.dumps({'schema_version': 1, 'candidates': {
                '1000.1000': {'version': '1000.1000', 'state': 'network-error',
                              'checked_at': '2000-01-01T00:00:00+00:00'}}}))
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=page('1000.1000')) as fetch:
                module.discover(args)
            fetch.assert_called_once_with(index_url('1000.1000'))
            self.assertIn('1000.1000', module.live_catalog(args.output)['releases'])

    def test_one_request_runs_continue_discovery_while_old_failures_stay_due(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            args.output.mkdir()
            (args.output/'discovery.json').write_text(json.dumps({'schema_version': 1, 'candidates': {
                '1000.1000': {'version': '1000.1000', 'state': 'http-error',
                              'checked_at': '2000-01-01T00:00:00+00:00'}}}))
            requested = []
            def fetch(url):
                requested.append(url)
                version = url.split('/')[-2]
                return HttpResult(500, b'temporary failure', 'text/plain', url) if version == '1000.1000' else page(version)
            with patch.object(module, 'ROOT', root), patch.object(module, 'get_page', side_effect=fetch), \
                 patch.object(module, 'pause'):
                for index in range(4):
                    with patch.object(retry, '_now', return_value=NOW+timedelta(days=30*index)):
                        module.discover(args)
            self.assertEqual(requested[0], index_url('1000.1000'))
            self.assertNotEqual(requested[1], index_url('1000.1000'))
            self.assertEqual(requested[2], index_url('1000.1000'))
            self.assertNotEqual(requested[3], index_url('1000.1000'))

    def test_source_cooldown_is_not_bypassed_by_new_versions(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=HttpResult(429, b'retry later', 'text/plain', index_url('0.0'))) as fetch:
                module.discover(args)
                module.discover(args)
                self.assertEqual(fetch.call_count, 1)
            state = json.loads((args.output/'discovery.json').read_text())
            state['source_backoff']['next_attempt_at'] = '2000-01-01T00:00:00+00:00'
            (args.output/'discovery.json').write_text(json.dumps(state))
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=page()) as fetch:
                module.discover(args)
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
                        module.discover(args)
            self.assertTrue({index_url(version) for version in ('0.0', '1.0', '0.1')} <= set(requested))
            state = json.loads((args.output/'discovery.json').read_text())
            self.assertGreaterEqual(state['exploration']['next_ordinal'], 3)
            self.assertTrue(state['pending_versions'])

    def test_cold_start_discovers_without_a_release_list_or_archive(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            # A historical curated list must not be a runtime input or prerequisite.
            (root / 'data/gpl').mkdir(parents=True)
            (root / 'data/gpl/seed-versions.json').write_text('{"versions": ["99.98"]}')
            args = self.args(root)
            with patch.object(module, 'ROOT', root), patch.object(module, 'get_page', return_value=page()) as fetch, \
                 patch.object(module, 'pause'), patch.object(retry, '_now', return_value=NOW):
                self.assertEqual(module.discover(args), 0)
                self.assertEqual(module.discover(args), 0)
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual(len({call.args[0] for call in fetch.call_args_list}), 2)
            self.assertEqual(len(module.live_catalog(args.output)['releases']), 2)
            self.assertNotIn('99.98', module.live_catalog(args.output)['releases'])

    def test_failed_version_recovers_without_recheck_flag(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            args = self.args(root)
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=HttpResult(403, b'<Error>AccessDenied</Error>', 'application/xml', index_url('1.0'))) as fetch, \
                 patch.object(retry, '_now', return_value=NOW):
                module.discover(args)
                module.discover(args)
                self.assertEqual(fetch.call_count, 2)
            with patch.object(module, 'ROOT', root), patch.object(module, 'pause'), \
                 patch.object(module, 'get_page', return_value=page()), \
                 patch.object(retry, '_now', return_value=NOW + timedelta(hours=2)):
                module.discover(args)
            self.assertIn('0.0', module.live_catalog(args.output)['releases'])
            state = json.loads((args.output / 'discovery.json').read_text())
            self.assertEqual(state['candidates']['0.0']['consecutive_failures'], 0)

    def test_refreshed_index_retains_old_capture_links_and_publishes_both(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old, new = page(filename='old.tar.xz'), page(filename='new.tar.xz')
            module.save_index('1.0', old, root)
            record = module.save_index('1.0', new, root)
            self.assertEqual({row['filename'] for row in record['links']}, {'new.tar.xz'})
            self.assertEqual({row['filename'] for row in module.all_links(record)}, {'old.tar.xz', 'new.tar.xz'})
            snapshots = record['index_snapshots']
            self.assertEqual(len(snapshots), 2)
            self.assertEqual((root / snapshots[0]['capture_file']).read_bytes(), old.data)
            self.assertEqual((root / snapshots[1]['capture_file']).read_bytes(), new.data)
            (root / 'artifacts.json').write_text(json.dumps({'schema_version': 1, 'artifacts': {'1.0': {
                name: {'published': True, 'filename': name, 'source_url': index_url('1.0').replace('gpl.html', name)}
                for name in ('old.tar.xz', 'new.tar.xz')}}}))
            args = Namespace(output=root, version=[], next=True, limit=1, max_total_bytes=0,
                             filename=[], include_upstream=False,
                             retry_errors=False, continue_errors=True, publish=True,
                             store=root / 'objects', repo='owner/repo', reserve_bytes=0, delay=0)
            with patch.object(module, 'publish', return_value={'state': 'published'}) as upload, \
                 patch.object(module, 'download_artifact', side_effect=AssertionError('no payload acquisition in this fixture')):
                module.archive(args)
                module.archive(args)
            self.assertEqual(upload.call_count, 2)
            receipts = json.loads((root / 'artifacts.json').read_text())['artifacts']['1.0']
            receipts = {name: item for name, item in receipts.items() if name.endswith('.html')}
            self.assertEqual(len(receipts), 2)
            self.assertEqual({r['sha256'] for r in receipts.values()}, {r['sha256'] for r in snapshots})


    def test_source_batch_retries_failures_without_starving_new_sources(self):
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            names = ['old-' + str(n) + '.tar.xz' for n in range(6)] + ['new-' + str(n) + '.tar.xz' for n in range(6)]
            links = [{'filename': name, 'url': index_url('1.0').replace('gpl.html', name),
                      'fetch_url': index_url('1.0').replace('gpl.html', name),
                      'kind': 'source', 'origin': 'sonos'} for name in names]
            (root / 'catalog.json').write_text(json.dumps({'schema_version': 1, 'releases': {'1.0': {'state': 'verified', 'links': links}}}))
            (root / 'artifacts.json').write_text(json.dumps({'schema_version': 1, 'artifacts': {'1.0': {
                name: {'filename': name, 'source_url': index_url('1.0').replace('gpl.html', name),
                       'state': 'http-error', 'http_status': 403, 'checked_at': (NOW - timedelta(days=n+1)).isoformat()}
                for n, name in enumerate(names[:6])}}}))
            args = Namespace(output=root, version=[], next=True, limit=4, max_total_bytes=0,
                             filename=[], include_upstream=False, retry_errors=False,
                             continue_errors=True, publish=False, store=root / 'objects',
                             repo='owner/repo', reserve_bytes=0, delay=0)
            attempted = []
            def fetch(link, *args, **kwargs):
                attempted.append(link['filename'])
                return {'state': 'http-error', 'http_status': 403, 'filename': link['filename']}
            with patch.object(module, 'download_artifact', side_effect=fetch), patch.object(module, 'pause'), \
                 patch.object(retry, '_now', return_value=NOW):
                self.assertEqual(module.archive(args), 0)
            self.assertEqual(attempted, ['old-5.tar.xz', 'new-0.tar.xz', 'new-1.tar.xz', 'new-2.tar.xz'])


    def test_unacquired_same_filename_links_count_as_distinct_pending_sources(self):
        import contextlib
        import io
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = b'Sonos GPL/LGPL Downloads<a href="source.tar.xz">one</a><a href="https://ftp.gnu.org/gnu/project/source.tar.xz">two</a>'
            module.save_index('1.0', HttpResult(200, body, 'text/html', index_url('1.0')), root)
            with contextlib.redirect_stdout(io.StringIO()):
                module.report(Namespace(output=root))
            coverage = json.loads((root / 'report.json').read_text())['release_coverage']['1.0']
            self.assertEqual(coverage['expected_assets'], 3)
            self.assertEqual(coverage['remaining_assets'], 3)
            self.assertFalse(coverage['complete_publication'])

    def test_published_index_receipt_with_another_hash_does_not_complete_coverage(self):
        import contextlib
        import io
        module = load('archive_gpl_direct')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            record = module.save_index('1.0', page(), root)
            (root / 'artifacts.json').write_text(json.dumps({'artifacts': {'1.0': {
                'gpl.html': {'published': True, 'sha256': '0' * 64},
                'source.tar.xz': {'filename': 'source.tar.xz', 'source_url': record['links'][0]['url'], 'published': True}}}}))
            with contextlib.redirect_stdout(io.StringIO()):
                module.report(Namespace(output=root))
            coverage = json.loads((root / 'report.json').read_text())['release_coverage']['1.0']
            self.assertEqual(coverage['remaining_assets'], 1)
            self.assertFalse(coverage['complete_publication'])

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
            args = Namespace(output=root, version=[], next=True, limit=1, max_total_bytes=0,
                             filename=[], include_upstream=False, retry_errors=False,
                             continue_errors=True, publish=False, store=store,
                             repo='owner/repo', reserve_bytes=0, delay=0)
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
                             repo='owner/repo', reserve_bytes=0, delay=0)
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


    def test_stream_auditor_uses_original_filename_for_url_variant_receipts(self):
        module = load('audit_gpl_published_streams')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = {'filename': 'source.tar.xz', 'kind': 'source', 'published': True,
                      'sha256': 'a' * 64, 'bytes': 10,
                      'source_url': 'https://www.sonos.com/documents/gpl/1.0/source.tar.xz',
                      'fetch_url': 'https://www.sonos.com/documents/gpl/1.0/source.tar.xz'}
            (root / 'artifacts.json').write_text(json.dumps({'artifacts': {'1.0': {
                'source.tar.xz--source-url-identity': source}}}))
            args = Namespace(output=root, limit=1, version=[], retry_failed=False, store=root / 'objects',
                             reserve_bytes=0, delay=0)
            def acquire(link, *args, **kwargs):
                self.assertEqual(link['filename'], 'source.tar.xz')
                return {'state': 'verified', 'sha256': 'a' * 64, 'bytes': 10,
                        'verification': 'tar-and-compression-integrity', 'members': 1}
            stats = module.run(args, download=acquire, wait=lambda _: None)
            self.assertEqual(stats['verified'], 1)


class SitemapRetryTests(unittest.TestCase):
    def test_parent_refresh_discovers_new_children_and_retry_keeps_old_children(self):
        module = load('discover_gpl_sitemaps')
        parent = 'https://www.sonos.com/sitemap.xml'
        child = 'https://www.sonos.com/en/sitemap.xml'
        body = f'<sitemapindex><sitemap><loc>{child}</loc></sitemap></sitemapindex>'.encode()
        with tempfile.TemporaryDirectory() as temporary:
            args = Namespace(output=Path(temporary) / 'site.json', seed=[parent], limit=1, recheck=False, delay=0)
            with patch.object(module, 'get_page', return_value=HttpResult(200, body, 'application/xml', parent)), \
                 patch.object(module, 'pause'), patch.object(retry, '_now', return_value=NOW):
                module.scan(args)
            # A failed child retries automatically without refreshing the parent.
            with patch.object(module, 'get_page', return_value=HttpResult(403, b'', 'text/html', child)) as fetch, \
                 patch.object(module, 'pause'), patch.object(retry, '_now', return_value=NOW):
                module.scan(args)
                fetch.assert_called_once_with(child, max_bytes=module.MAX_XML_BYTES)
            xml = b'<urlset><url><loc>https://www.sonos.com/documents/gpl/2.5/gpl.html</loc></url></urlset>'
            with patch.object(module, 'get_page', return_value=HttpResult(200, xml, 'application/xml', child)), \
                 patch.object(module, 'pause'), patch.object(retry, '_now', return_value=NOW + timedelta(hours=2)):
                module.scan(args)
            state = json.loads(args.output.read_text())
            self.assertIn('2.5', state['version_evidence'])
            self.assertEqual(state['sitemaps'][parent]['sitemap_children'], [child])
            self.assertEqual(state['status'], 'ok')


if __name__ == '__main__':
    unittest.main()
