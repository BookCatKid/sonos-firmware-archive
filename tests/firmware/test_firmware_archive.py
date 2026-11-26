import hashlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from sonos_firmware import discovery, firmware_archive as archive
from sonos_firmware.catalog import validate_catalog
from sonos_firmware.storage import atomic_json
from sonos_firmware.upd import parse_bytes
from tests.firmware.test_upd import section


class Response(io.BytesIO):
    def __init__(self, body, headers=None):
        super().__init__(body)
        self.headers = headers or {}


def candidate(label='one', **extra):
    return {'url': 'https://update.sonos.com/firmware/fixture/' + label + '/same.upd',
            'filename': 'same.upd', 'version': '1.0-1', 'package_model': 17,
            'available': True, **extra}


def captured(row, body):
    digest = hashlib.sha256(body).hexdigest()
    name = 'same--' + digest + '.upd'
    capture = {'candidate': row, 'bytes': len(body), 'sha256': digest,
               'sections': [s.to_dict() for s in parse_bytes(body)],
               'release_tag': 'firmware-' + row['version'], 'release_asset': name}
    capture['publication'] = archive.publication_receipt('owner/repo', capture['release_tag'], name,
        capture, {'url': 'https://github.com/owner/repo/releases/download/tag/' + name})
    return capture


def run_archive(root, repository, candidates, work, *, work_units=None, **kwargs):
    checks = 0
    def wind_down():
        nonlocal checks
        checks += 1
        return work_units is not None and checks > work_units
    with patch.object(archive, 'winding_down', side_effect=wind_down):
        return archive.archive_candidates(root, repository, candidates, work, **kwargs)


class AcquisitionTests(unittest.TestCase):
    def test_partial_http_response_is_rejected_even_when_its_own_length_matches(self):
        response = Response(b'short', {'Content-Length': '5', 'Content-Range': 'bytes 0-4/100'})
        response.status = 206
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', return_value=response):
            result = discovery.download(candidate(), temporary)
            self.assertFalse(result['downloaded'])
            self.assertIn('HTTP 200', result['download_error'])
            self.assertEqual(list(Path(temporary).iterdir()), [])

    def test_parallel_urls_with_same_filename_have_distinct_immutable_captures(self):
        rows = [candidate('one'), candidate('two')]
        bodies = {row['url']: section(1, 0, row['url'].encode()) for row in rows}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', side_effect=lambda request, **kw: Response(bodies[request.full_url])):
            results = discovery.download_all(rows, temporary)
            self.assertEqual(len({row['local_file'] for row in results}), 2)
            for row in results:
                self.assertTrue(row['downloaded'])
                self.assertEqual((Path(temporary) / row['local_file']).read_bytes(), bodies[row['url']])
                self.assertEqual(row['bytes'], len(bodies[row['url']]))


    def test_checksum_is_optional_and_corrupt_cache_is_not_trusted(self):
        body = b'new!'
        digest = hashlib.sha256(body).hexdigest()
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', return_value=Response(body)) as fetch:
            path = Path(temporary) / ('same--' + digest + '.upd')
            path.write_bytes(b'bad!')
            result = discovery.download(candidate(bytes=4, expected_sha256=digest), temporary)
            self.assertTrue(result['downloaded'])
            self.assertEqual(path.read_bytes(), body)
            fetch.assert_called_once()
            discovery.download(candidate(expected_sha256=digest), temporary)
            fetch.assert_called_once()

    def test_truncated_response_is_rejected_without_a_prior_size_or_checksum(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', return_value=Response(b'short', {'Content-Length': '100'})):
            result = discovery.download(candidate(), temporary)
            self.assertFalse(result['downloaded'])
            self.assertIn('Content-Length', result['download_error'])
            self.assertEqual(list(Path(temporary).iterdir()), [])


class PublicationTests(unittest.TestCase):

    def test_request_backoff_survives_download_receipts_and_worker_restarts(self):
        import urllib.error
        from datetime import timedelta
        from sonos_firmware import releases, retry
        from tests.apps.test_app_archive import FakeGitHub, NOW
        github = FakeGitHub()
        rows = [candidate('first'), candidate('peer')]
        calls = []
        failing = True
        def fetch(request, **kwargs):
            calls.append(request.full_url)
            if failing:
                raise urllib.error.HTTPError(request.full_url, 429, 'retry later', {'Retry-After': '75'}, None)
            return Response(section(99, 0, request.full_url.encode()))
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', side_effect=fetch), \
             patch.object(releases, 'remote_assets', side_effect=github.assets), \
             patch.object(releases.subprocess, 'run', side_effect=github.command):
            root = Path(temporary)
            with patch.object(retry, '_now', return_value=NOW):
                run_archive(root, 'owner/repo', rows[:1], root/'worker')
            with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=74)):
                waiting = run_archive(root, 'owner/repo', rows, root/'worker')
            self.assertEqual(waiting['attempted'], 0)
            self.assertEqual(calls, [rows[0]['url']])
            failing = False
            with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=76)):
                resumed = run_archive(root, 'owner/repo', rows, root/'worker')
            self.assertEqual(resumed['preserved'], 2)
            self.assertEqual(validate_catalog(root), [])

    def test_publication_throttle_does_not_fetch_fresh_firmware_before_retry_after(self):
        import urllib.error
        from datetime import timedelta
        from sonos_firmware import releases, retry
        from tests.apps.test_app_archive import FakeGitHub, NOW
        github = FakeGitHub()
        github.inventory_failure = urllib.error.HTTPError('https://api.github.com/fixture', 429,
            'retry later', {'Retry-After': '75'}, None)
        rows = [candidate('first'), candidate('peer')]
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', side_effect=lambda request, **kwargs:
                 Response(section(99, 0, request.full_url.encode()))) as fetch, \
             patch.object(releases, 'remote_assets', side_effect=github.assets), \
             patch.object(releases.subprocess, 'run', side_effect=github.command):
            root = Path(temporary)
            with patch.object(retry, '_now', return_value=NOW):
                run_archive(root, 'owner/repo', rows[:1], root/'worker')
            with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=74)):
                waiting = run_archive(root, 'owner/repo', rows, root/'worker')
            self.assertEqual(waiting['attempted'], 0)
            self.assertEqual(fetch.call_count, 1)
            with patch.object(retry, '_now', return_value=NOW+timedelta(seconds=76)):
                resumed = run_archive(root, 'owner/repo', rows, root/'worker')
            self.assertEqual(resumed['preserved'], 2)
            self.assertEqual(validate_catalog(root), [])


    def test_unclassified_upd_can_be_preserved_alongside_known_models_without_guessing(self):
        rows = [candidate('unclassified', filename='opaque.upd', version='unknown', package_model=None,
                          archive_captures={'capture': {'timestamp': '20200101000000', 'digest': 'index-evidence'}}),
                candidate('known', filename='known.upd', version='unknown')]
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', side_effect=lambda request, **kw:
                 Response(section(99, 0, request.full_url.encode()))), \
             patch.object(archive, 'ensure_release'), \
             patch.object(archive, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}):
            root = Path(temporary)
            result = run_archive(root, 'owner/repo', rows, root/'worker')
            self.assertEqual(result['preserved'], 2)
            records = archive.catalog_at(root/'data/catalog.json')['packages']
            unclassified = next(row for row in records if row['filename'] == 'opaque.upd')
            self.assertIsNone(unclassified['package_model'])
            self.assertEqual(unclassified['discovery_evidence']['capture']['digest'], 'index-evidence')
            self.assertEqual(validate_catalog(root), [])


    def test_empty_checkout_acquires_hashes_publishes_and_registers_without_prepared_inventory(self):
        row = candidate()
        body = section(99, 0, b'fixture unknown section')
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', return_value=Response(body)), \
             patch.object(archive, 'ensure_release'), \
             patch.object(archive, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}) as publish:
            root = Path(temporary)
            result = run_archive(root, 'owner/repo', [row], root / 'worker')
            self.assertEqual(result, {'attempted': 1, 'preserved': 1, 'errors': [], 'pending': 0})
            record = archive.catalog_at(root / 'data/catalog.json')['packages'][0]
            self.assertEqual(record['sha256'], hashlib.sha256(body).hexdigest())
            self.assertEqual(record['bytes'], len(body))
            self.assertEqual(record['raw_status'], 'pending')
            self.assertEqual(record['extraction_policy'], 'all-sections')
            self.assertEqual(publish.call_args.args[2].name, record['release_asset'])
            self.assertFalse(list((root / 'worker').iterdir()))
            self.assertEqual(validate_catalog(root), [])


    def test_lost_upload_response_recovers_without_original_bytes_or_source(self):
        row = candidate()
        body = section(6, 0, b'kernel')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(discovery, 'open_advertised', return_value=Response(body)), \
                 patch.object(archive, 'ensure_release'), \
                 patch.object(archive, 'publish_verified', side_effect=OSError('lost response')):
                first = run_archive(root, 'owner/repo', [row], root / 'worker')
            self.assertEqual(first['preserved'], 0)
            state = archive.acquisition_state(root)
            state['sources']['owner/repo'][row['url']]['next_attempt_at'] = ''
            atomic_json(archive.acquisition_path(root), state)
            with patch.object(discovery, 'open_advertised') as fetch, \
                 patch.object(archive, 'verified_asset', return_value={'url': 'https://github.com/fixture/asset'}):
                result = run_archive(root, 'owner/repo', [], root / 'worker')
            fetch.assert_not_called()
            self.assertEqual(result['preserved'], 1)
            self.assertEqual(validate_catalog(root), [])

    def test_same_name_different_bytes_or_models_do_not_overwrite_catalog(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = archive.register_capture(root, captured(candidate(), section(6, 0, b'one')))
            second = archive.register_capture(root, captured(candidate('two'), section(6, 0, b'two')))
            third = archive.register_capture(root, captured(candidate('three', package_model=8), section(6, 0, b'one')))
            self.assertEqual(len({row['id'] for row in (first, second, third)}), 3)
            self.assertEqual(len(archive.catalog_at(root / 'data/catalog.json')['packages']), 3)
            self.assertEqual(validate_catalog(root), [])


    def test_preserved_requires_verified_publication_and_complete_requires_all_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = captured(candidate(), section(99, 0, b'metadata'))
            del capture['publication']
            with self.assertRaisesRegex(ValueError, 'publication receipt'):
                archive.register_capture(root, capture)
            self.assertFalse((root / 'data').exists())
            archive.register_capture(root, captured(candidate(), section(99, 0, b'metadata')))
            self.assertEqual(validate_catalog(root), [])
            path = root / 'data/catalog.json'
            catalog = archive.catalog_at(path)
            catalog['packages'][0]['raw_status'] = 'complete'
            atomic_json(path, catalog)
            self.assertTrue(any('section 0' in error for error in validate_catalog(root)))


    def test_publication_recovery_and_new_sources_share_wind_down(self):
        from datetime import timedelta
        from sonos_firmware import releases, retry
        from tests.apps.test_app_archive import FakeGitHub, NOW
        github = FakeGitHub()
        fresh = candidate('new')
        body = section(99, 0, b'new synthetic payload')
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(releases, 'remote_assets', side_effect=github.assets), \
             patch.object(releases.subprocess, 'run', side_effect=github.command), \
             patch.object(archive, 'verified_asset', return_value=None), \
             patch.object(discovery, 'open_advertised', side_effect=lambda *args, **kwargs: Response(body)) as acquire:
            root = Path(temporary)
            old = {'repository': 'owner/repo', 'capture': captured(candidate('old'), section(6, 0, b'old')),
                   'checked_at': '2000-01-01T00:00:00+00:00'}
            atomic_json(archive.acquisition_path(root), {'sources': {}, 'unpublished_captures': [old]})
            with patch.object(retry, '_now', return_value=NOW):
                first = run_archive(root, 'owner/repo', [fresh], root/'worker', work_units=1)
            self.assertEqual(first['attempted'], 1)
            self.assertEqual(first['preserved'], 1)
            self.assertEqual(acquire.call_count, 1)
            with patch.object(retry, '_now', return_value=NOW+timedelta(days=2)):
                second = run_archive(root, 'owner/repo', [fresh], root/'worker', work_units=1)
            self.assertEqual(second['attempted'], 1)
            self.assertEqual(second['preserved'], 0)
            self.assertEqual(acquire.call_count, 1)
            with patch.object(retry, '_now', return_value=NOW+timedelta(days=4)):
                run_archive(root, 'owner/repo', [fresh], root/'worker', work_units=1)
            remaining = archive.acquisition_state(root)['unpublished_captures']
            self.assertEqual(remaining[0]['consecutive_failures'], 2)
            self.assertEqual(acquire.call_count, 1)
