import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

from sonos_firmware import discovery, firmware_archive as archive
from sonos_firmware.catalog import validate_catalog
from sonos_firmware.storage import atomic_json
from sonos_firmware.upd import parse_bytes
from test_upd import section


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

    def test_size_only_cache_does_not_hide_changed_bytes_at_a_url(self):
        row = candidate(bytes=4)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', side_effect=[Response(b'old!'), Response(b'new!')]) as fetch:
            first = discovery.download(row, temporary)
            second = discovery.download(row, temporary)
            self.assertNotEqual(first['sha256'], second['sha256'])
            self.assertEqual(fetch.call_count, 2)
            self.assertEqual((Path(temporary) / first['local_file']).read_bytes(), b'old!')

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
    def test_mutable_protocol_root_is_captured_before_historical_backlog(self):
        historical = candidate('historical')
        current = candidate('current', source='protocol-static', version='unknown', refresh_hours=6)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', side_effect=lambda request, **kw:
                          Response(section(99, 0, request.full_url.encode()))) as fetch, \
             patch.object(archive, 'ensure_release'), \
             patch.object(archive, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}):
            root = Path(temporary)
            archive.archive_candidates(root, 'owner/repo', [historical, current], root/'worker', limit=1)
            self.assertEqual(fetch.call_args.args[0].full_url, current['url'])
            archive.archive_candidates(root, 'owner/repo', [historical, current], root/'worker', limit=1)
            self.assertEqual(fetch.call_args.args[0].full_url, historical['url'])
            observations = archive.acquisition_state(root)['sources']['owner/repo']
            for row in (current, historical):
                self.assertEqual(observations[row['url']]['status'], 'preserved')

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
            result = archive.archive_candidates(root, 'owner/repo', rows, root/'worker')
            self.assertEqual(result['preserved'], 2)
            records = archive.catalog_at(root/'data/catalog.json')['packages']
            unclassified = next(row for row in records if row['filename'] == 'opaque.upd')
            self.assertIsNone(unclassified['package_model'])
            self.assertEqual(unclassified['discovery_evidence']['capture']['digest'], 'index-evidence')
            self.assertEqual(validate_catalog(root), [])

    def test_snapshot_filename_alias_keeps_its_existing_catalog_identity(self):
        body = section(99, 0, b'snapshot')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            old = candidate(filename='old-snapshot.upd')
            original = archive.register_capture(root, captured(old, body))
            current = {**old, 'filename': 'connectx.upd'}
            result = archive.register_capture(root, captured(current, body))
            self.assertEqual(result['id'], original['id'])
            self.assertEqual(result['filename'], 'old-snapshot.upd')
            self.assertEqual(result['advertised_filenames'], ['connectx.upd', 'old-snapshot.upd'])
            self.assertEqual(len(archive.catalog_at(root/'data/catalog.json')['packages']), 1)

    def test_static_endpoint_versions_come_from_bytes_and_request_revision_remains_unpinned(self):
        import struct
        row = candidate(source='protocol-static', version='unknown', refresh_hours=6)
        first = section(1, 0, struct.pack('<III', 86, 4, 73207))
        second = section(1, 0, struct.pack('<III', 87, 1, 74000))
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', side_effect=[Response(first), Response(second)]), \
             patch.object(archive, 'ensure_release'), \
             patch.object(archive, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}):
            root = Path(temporary)
            archive.archive_candidates(root, 'owner/repo', [row], root/'worker')
            state = archive.acquisition_state(root)
            observation = state['sources']['owner/repo'][row['url']]
            self.assertEqual(observation['revision']['version'], 'unknown')
            self.assertEqual(observation['status'], 'preserved')
            observation['next_attempt_at'] = ''
            atomic_json(archive.acquisition_path(root), state)
            archive.archive_candidates(root, 'owner/repo', [row], root/'worker')
            packages = archive.catalog_at(root/'data/catalog.json')['packages']
            self.assertEqual({record['version'] for record in packages}, {'86.4-73207', '87.1-74000'})
            self.assertEqual(len({record['id'] for record in packages}), 2)
            self.assertTrue(all(record['release_tag'] != 'firmware-unknown' for record in packages))

    def test_empty_checkout_acquires_hashes_publishes_and_registers_without_prepared_inventory(self):
        row = candidate()
        body = section(99, 0, b'fixture unknown section')
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', return_value=Response(body)), \
             patch.object(archive, 'ensure_release'), \
             patch.object(archive, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}) as publish:
            root = Path(temporary)
            result = archive.archive_candidates(root, 'owner/repo', [row], root / 'worker')
            self.assertEqual(result, {'attempted': 1, 'preserved': 1, 'errors': [], 'pending': 0})
            record = archive.catalog_at(root / 'data/catalog.json')['packages'][0]
            self.assertEqual(record['sha256'], hashlib.sha256(body).hexdigest())
            self.assertEqual(record['bytes'], len(body))
            self.assertEqual(record['raw_status'], 'pending')
            self.assertEqual(record['extraction_policy'], 'all-sections')
            self.assertEqual(publish.call_args.args[2].name, record['release_asset'])
            self.assertFalse(list((root / 'worker').iterdir()))
            self.assertEqual(validate_catalog(root), [])

    def test_unsupported_container_does_not_block_source_publication(self):
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', return_value=Response(b'unsupported UPD container fixture')), \
             patch.object(archive, 'ensure_release'), \
             patch.object(archive, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}):
            root = Path(temporary)
            result = archive.archive_candidates(root, 'owner/repo', [candidate()], root / 'worker')
            self.assertEqual(result['preserved'], 1)
            record = archive.catalog_at(root / 'data/catalog.json')['packages'][0]
            self.assertEqual(record['raw_status'], 'pending')
            inventory = json.loads((root / 'data/upd' / (record['id'] + '.json')).read_text())
            self.assertTrue(inventory['inventory_error'])
            self.assertEqual(validate_catalog(root), [])

    def test_failed_acquisition_counts_against_budget_and_other_urls_continue(self):
        rows = [candidate(str(i)) for i in range(3)]
        body = section(6, 0, b'kernel')
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(discovery, 'open_advertised', side_effect=[OSError('fixture failure'), Response(body)]) as fetch, \
             patch.object(archive, 'ensure_release'), \
             patch.object(archive, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}):
            root = Path(temporary)
            result = archive.archive_candidates(root, 'owner/repo', rows, root / 'worker', limit=2)
            self.assertEqual(result['attempted'], 2)
            self.assertEqual(result['preserved'], 1)
            self.assertEqual(result['pending'], 2)
            self.assertEqual(fetch.call_count, 2)

    def test_lost_upload_response_recovers_without_original_bytes_or_source(self):
        row = candidate()
        body = section(6, 0, b'kernel')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.object(discovery, 'open_advertised', return_value=Response(body)), \
                 patch.object(archive, 'ensure_release'), \
                 patch.object(archive, 'publish_verified', side_effect=OSError('lost response')):
                first = archive.archive_candidates(root, 'owner/repo', [row], root / 'worker')
            self.assertEqual(first['preserved'], 0)
            state = archive.acquisition_state(root)
            state['sources']['owner/repo'][row['url']]['next_attempt_at'] = ''
            atomic_json(archive.acquisition_path(root), state)
            with patch.object(discovery, 'open_advertised') as fetch, \
                 patch.object(archive, 'verified_asset', return_value={'url': 'https://github.com/fixture/asset'}):
                result = archive.archive_candidates(root, 'owner/repo', [], root / 'worker')
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

    def test_equal_bytes_add_aliases_without_resetting_extraction_or_version(self):
        body = section(6, 0, b'kernel')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first = archive.register_capture(root, captured(candidate(), body))
            path = root / 'data/catalog.json'
            catalog = archive.catalog_at(path)
            catalog['packages'][0]['raw_status'] = 'complete'
            atomic_json(path, catalog)
            second = archive.register_capture(root, captured(candidate('two', version='2.0-1'), body))
            self.assertEqual(second['id'], first['id'])
            self.assertEqual(second['version'], '1.0-1')
            self.assertEqual(second['advertised_versions'], ['1.0-1', '2.0-1'])
            self.assertEqual(second['raw_status'], 'complete')
            self.assertEqual(len(second['source_urls']), 2)

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

    def test_older_unpublished_capture_is_reconciled_independently_and_scoped_to_repository(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture = captured(candidate(), section(6, 0, b'old'))
            capture.pop('publication')
            state = {'sources': {}, 'unpublished_captures': [
                {'repository': 'owner/repo', 'capture': capture},
                {'repository': 'another/repo', 'capture': capture}]}
            atomic_json(archive.acquisition_path(root), state)
            with patch.object(archive, 'verified_asset', return_value={'url': 'https://github.com/fixture/asset'}) as verify:
                result = archive.archive_candidates(root, 'owner/repo', [], root / 'worker', limit=1)
            self.assertEqual(result['preserved'], 1)
            self.assertEqual(result['pending'], 0)
            verify.assert_called_once()
            remaining = archive.acquisition_state(root)['unpublished_captures']
            self.assertEqual([r['repository'] for r in remaining], ['another/repo'])

    def test_one_slot_rotates_between_old_publication_recovery_and_new_sources(self):
        from datetime import timedelta
        from sonos_firmware import releases, retry
        from test_app_archive import FakeGitHub, NOW
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
                first = archive.archive_candidates(root, 'owner/repo', [fresh], root/'worker', limit=1)
            self.assertEqual(first['attempted'], 1)
            acquire.assert_not_called()
            with patch.object(retry, '_now', return_value=NOW+timedelta(days=2)):
                second = archive.archive_candidates(root, 'owner/repo', [fresh], root/'worker', limit=1)
            self.assertEqual(second['attempted'], 1)
            self.assertEqual(second['preserved'], 1)
            self.assertEqual(acquire.call_count, 1)
            with patch.object(retry, '_now', return_value=NOW+timedelta(days=4)):
                archive.archive_candidates(root, 'owner/repo', [fresh], root/'worker', limit=1)
            remaining = archive.acquisition_state(root)['unpublished_captures']
            self.assertEqual(remaining[0]['consecutive_failures'], 2)
            self.assertEqual(acquire.call_count, 1)

    def test_paused_replay_does_not_consume_the_only_firmware_acquisition_slot(self):
        from sonos_firmware import releases
        from test_app_archive import FakeGitHub
        github = FakeGitHub()
        paused = candidate('paused', archive_capture={'timestamp': '20200101000000'}, acquisition_id='wayback:paused')
        fresh = candidate('healthy')
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(releases, 'remote_assets', side_effect=github.assets), \
             patch.object(releases.subprocess, 'run', side_effect=github.command), \
             patch.object(discovery, 'open_advertised', return_value=Response(section(99, 0, b'fixture'))) as acquire:
            root = Path(temporary)
            result = archive.archive_candidates(root, 'owner/repo', [paused, fresh], root/'worker',
                                                limit=1, paused_hosts={'web.archive.org'})
            self.assertEqual(result['attempted'], 1)
            self.assertEqual(result['preserved'], 1)
            self.assertEqual(acquire.call_args.args[0].full_url, fresh['url'])
            self.assertEqual(archive.acquisition_state(root)['sources']['owner/repo']['wayback:paused']['status'], 'pending')

    def test_receipt_automatically_publishes_with_declared_asset_name(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = section(6, 0, b'kernel')
            capture = captured(candidate(), body)
            artifact = {**capture, 'downloaded': True, 'local_file': 'staged.upd', 'filename': 'same.upd'}
            (root / 'staged.upd').write_bytes(body)
            path = root / 'receipt.json'
            atomic_json(path, {'artifacts': [artifact]})
            def publish(repo, tag, upload, size, digest):
                self.assertEqual(upload.name, capture['release_asset'])
                self.assertEqual(upload.read_bytes(), body)
                return {'url': 'https://github.com/fixture/asset'}
            with patch.object(archive, 'verified_asset', return_value=None), \
                 patch.object(archive, 'publish_verified', side_effect=publish):
                result = archive.upload_receipt(path, root, 'tag', 'owner/repo')
            self.assertEqual(result['artifacts'][0]['publication']['status'], 'verified')


class RawCoverageTests(unittest.TestCase):
    def import_components(self, root, components):
        script = Path(__file__).resolve().parents[1] / 'scripts/import_raw_receipts.py'
        spec = importlib.util.spec_from_file_location('raw_import_fixture', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        receipt = root / 'receipt.json'
        atomic_json(receipt, {'components': components})
        with patch.object(module, 'ROOT', root), patch.object(sys, 'argv', [str(script), str(receipt)]):
            self.assertEqual(module.main(), 0)

    def test_incomplete_coverage_cannot_retain_complete_status(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            body = section(99, 0, b'one') + section(98, 0, b'two')
            archive.register_capture(root, captured(candidate(), body))
            path = root / 'data/catalog.json'
            catalog = archive.catalog_at(path)
            catalog['packages'][0]['raw_status'] = 'complete'
            atomic_json(path, catalog)
            self.import_components(root, [{'package_id': 'same', 'section_index': 0, 'section_type': 99,
                'kind': 'section-99', 'filename': 'same-section-99.bin', 'sha256': hashlib.sha256(b'one').hexdigest()}])
            self.assertEqual(archive.catalog_at(path)['packages'][0]['raw_status'], 'pending')
            self.assertEqual(validate_catalog(root), [])

    def test_multiple_device_sections_are_retained_and_counted(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive.register_capture(root, captured(candidate(), section(13, 0, b'one') + section(13, 0, b'two')))
            self.import_components(root, [{'package_id': 'same', 'section_index': index, 'section_type': 13,
                'kind': 'device-payload', 'filename': f'same-device-{index}.bin', 'sha256': hashlib.sha256(body).hexdigest()}
                for index, body in enumerate((b'one', b'two'))])
            catalog = archive.catalog_at(root / 'data/catalog.json')
            self.assertEqual(len(catalog['raw_images']), 2)
            self.assertEqual(catalog['packages'][0]['raw_status'], 'complete')
            self.assertEqual(validate_catalog(root), [])

    def test_legacy_kind_only_component_cannot_cover_multiple_indexed_sections(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            archive.register_capture(root, captured(candidate(), section(6, 0, b'one') + section(6, 0, b'two')))
            self.import_components(root, [{'package_id': 'same', 'section_type': 6, 'kind': 'kernel',
                'filename': 'same-kernel.bin', 'sha256': hashlib.sha256(b'one').hexdigest()}])
            path = root / 'data/catalog.json'
            catalog = archive.catalog_at(path)
            self.assertEqual(catalog['packages'][0]['raw_status'], 'pending')
            self.assertEqual(validate_catalog(root), [])
            catalog['packages'][0]['raw_status'] = 'complete'
            atomic_json(path, catalog)
            self.assertEqual(len(validate_catalog(root)), 2)
