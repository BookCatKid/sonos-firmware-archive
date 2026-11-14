import importlib.util
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / 'scripts'))
SPEC = importlib.util.spec_from_file_location('archive_connectx', ROOT / 'scripts/archive_connectx.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class ArchiveConnectxTests(unittest.TestCase):
    def test_cataloged_static_snapshot_never_becomes_a_pinned_release_candidate(self):
        catalog = {'packages': [{'id': 'old-static', 'package_model': 17, 'artifact_type': 'upd',
                                'source_url': MODULE.STATIC_URL, 'version': '86.4-73207',
                                'artifact_status': 'preserved', 'release_tag': 'old',
                                'release_asset': 'old-snapshot.upd'}]}
        candidates = MODULE.candidates(catalog)
        static = next(c for c in candidates if c.get('static'))
        self.assertTrue(static['static'])
        self.assertNotIn('version', static)
        self.assertNotIn('release_tag', static)
        historical = next(c for c in candidates if c.get('source_key'))
        self.assertEqual(historical['release_asset'], 'old-snapshot.upd')
        self.assertEqual(historical['source_key'], 'release:old/old-snapshot.upd')

    def test_identical_bytes_at_an_unregistered_candidate_still_get_registered(self):
        from test_upd import section
        import struct
        data = section(1, 0, struct.pack('<III', 86, 4, 73207))
        digest = MODULE.sha256(data)
        identity = 'connectx-86.4-73207-' + digest[:16]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            output = root / 'index'
            output.mkdir()
            (output / 'discovery').mkdir()
            candidate = {'id': '86.4-73207-1-17', 'filename': '86.4-73207-1-17.upd',
                         'source_url': 'https://update.sonos.com/firmware/known/86.4-73207-1-17.upd'}
            prior = {'id': identity, 'sha256': digest, 'complete': True,
                     'extraction_sha256': 'old-bundle', 'release_tag': 'firmware-86.4-73207'}
            (output / 'discovery/connectx-index.json').write_text(json.dumps({'snapshots': [prior], 'sources': {}}))
            catalog = root / 'catalog.json'
            catalog.write_text(json.dumps({'packages': [], 'raw_images': []}))
            def acquire(url, destination):
                destination.write_bytes(data)
                return data
            args = ['archive_connectx', '--work-dir', str(root / 'work'), '--catalog', str(catalog),
                    '--output', str(output), '--key', str(root / 'key.pem'), '--publish',
                    '--frontier', str(root / 'no-frontier')]
            with patch.object(sys, 'argv', args), patch.object(MODULE, 'candidates', return_value=[candidate]), \
                 patch.object(MODULE, 'acquire', side_effect=acquire), \
                 patch.object(MODULE, 'load_private_key'), \
                 patch.object(MODULE, 'key_recipient_id', return_value=MODULE.RECIPIENT), \
                 patch.object(MODULE, 'extract_package', return_value={'complete': True, 'sections': [], 'components': [], 'bytes': len(data), 'sha256': digest}), \
                 patch.object(MODULE, 'ensure_release'), patch.object(MODULE, 'upload_small_verified'), \
                 patch.object(MODULE, 'register_published') as register:
                self.assertEqual(MODULE.main(), 0)
                register.assert_called_once()
                self.assertEqual(register.call_args.args[1], candidate)

    def test_catalog_discovery_includes_missing_versions_and_evidenced_model_siblings(self):
        base = 'https://update.sonos.com/firmware/Prod/campaign/'
        catalog = {'packages': [
            {'id': 'known', 'package_model': 17, 'artifact_type': 'upd', 'source_url': base + '1.2-12345-1-17.upd'},
            {'id': 'other', 'package_model': 8, 'artifact_type': 'upd', 'source_url': base + '2.3-23456-1-8.upd'},
            {'id': 'bad', 'package_model': 8, 'source_url': 'https://outside.test/firmware/3.4-34567-1-8.upd'},
        ]}
        rows = MODULE.candidates(catalog)
        urls = {r['source_url'] for r in rows}
        self.assertEqual(urls, {MODULE.STATIC_URL, base + '1.2-12345-1-17.upd', base + '2.3-23456-1-17.upd'})

    def test_release_hash_mismatch_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'package.upd'
            path.write_bytes(b'wrong bytes')
            source = {'release_tag': 'tag', 'release_asset': 'package.upd', 'bytes': 11, 'sha256': '0' * 64}
            with patch.object(MODULE.subprocess, 'run'):
                with self.assertRaisesRegex(ValueError, 'hash mismatch'):
                    MODULE.download_release(source, path, 'owner/repo')

    def test_failed_component_upload_cannot_register_a_preserved_package(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = {'packages': [], 'raw_images': []}
            args = types.SimpleNamespace(repository='owner/repo', catalog=root / 'catalog.json')
            snapshot = {'id': 'connectx-test', 'release_tag': 'firmware-test'}
            report = {'components': [{'kind': 'rootfs', 'filename': 'rootfs.bin', 'sha256': 'a' * 64}]}
            with patch.object(MODULE, 'upload_small_verified', side_effect=RuntimeError('upload failed')):
                with self.assertRaisesRegex(RuntimeError, 'upload failed'):
                    MODULE.register_published(catalog, {}, snapshot, report, root, args)
            self.assertEqual(catalog['packages'], [])
            self.assertFalse(args.catalog.exists())

    def test_bundle_content_is_reproducible_despite_file_mtime(self):
        import os
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            directory = root / 'files'
            directory.mkdir()
            source = directory / 'anacapad'
            source.write_bytes(b'fixture')
            MODULE.bundle(directory, root / 'first.tar.xz')
            os.utime(source, (2000000000, 2000000000))
            source.chmod(0o600)
            MODULE.bundle(directory, root / 'second.tar.xz')
            self.assertEqual((root / 'first.tar.xz').read_bytes(), (root / 'second.tar.xz').read_bytes())

    def test_extraction_bundles_match_across_host_umasks_and_preserve_firmware_modes(self):
        import os
        import io
        import struct
        import tarfile
        from test_upd import section
        payload = io.BytesIO()
        with tarfile.open(fileobj=payload, mode='w') as archive:
            member = tarfile.TarInfo('bin/anacapad')
            member.size = 3
            member.mode = 0o755
            archive.addfile(member, io.BytesIO(b'ELF'))
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'connectx.upd'
            source.write_bytes(section(1, 0, struct.pack('<III', 86, 4, 73207)) + section(23, 0, payload.getvalue()))
            bundles = []
            for mask in (0o022, 0o077):
                original_umask = os.umask(mask)
                try:
                    directory = root / str(mask)
                    report = MODULE.extract_package(source, directory)
                    MODULE.write_json(directory / 'inventory.json', report)
                    destination = root / f'{mask}.tar.xz'
                    MODULE.bundle(directory, destination)
                    bundles.append(destination.read_bytes())
                    with tarfile.open(destination) as archive:
                        self.assertEqual(archive.getmember('connectx-section-01-application-archive-files/bin/anacapad').mode, 0o755)
                finally:
                    os.umask(original_umask)
            self.assertEqual(*bundles)

    def test_shared_firmware_provider_keeps_static_history_and_key_recovery(self):
        from sonos_firmware.platform.registry import targets
        from sonos_firmware.platform.runner import plan
        steps = plan(targets(ROOT)['firmware'], ROOT, 'owner/repo')['steps']
        extract = next(s for s in steps if s['name'] == 'extract-firmware')
        archive = next(s for s in steps if s['name'] == 'archive_connectx')
        self.assertTrue(extract['after_failure'])
        self.assertIn('--publish', archive['command'])
        self.assertEqual(archive['command'][archive['command'].index('--max-packages') + 1], '4')


if __name__ == '__main__':
    unittest.main()
