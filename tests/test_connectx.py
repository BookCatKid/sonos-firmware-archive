import hashlib
import io
import json
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sonos_firmware.connectx import extract_package, legacy_kernel_data, package_version, unpack_tar
from test_upd import section
from test_anacapad import tar_payload


class ConnectxTests(unittest.TestCase):
    def test_legacy_zimage_gzip_stream_accepts_filename_and_trailing_wrapper(self):
        import gzip
        output = io.BytesIO()
        with gzip.GzipFile(filename='vmlinux', fileobj=output, mode='wb') as stream:
            stream.write(b'PowerPC kernel')
        prefix = b'boot wrapper' + b'\x1f\x8b\x08invalid'
        payload = prefix + output.getvalue() + b'trailing wrapper data'
        self.assertEqual(legacy_kernel_data(payload), (len(prefix), b'PowerPC kernel'))
        self.assertIsNone(legacy_kernel_data(output.getvalue()[:-10]))

    def test_header_version_is_read_from_package_not_url(self):
        data = section(1, 0, struct.pack('<III', 86, 4, 73207))
        self.assertEqual(package_version(data), '86.4-73207')
        for invalid in (section(1, 0, b'bad'), data + data, section(21, 0, b'x')):
            with self.assertRaises(ValueError):
                package_version(invalid)

    def test_all_sections_are_retained_and_tar_contents_are_inventoried(self):
        archive = tar_payload([('bin/anacapad', b'ELF', tarfile.REGTYPE),
                               ('lib/example', b'library', tarfile.REGTYPE)])
        data = section(1, 0, struct.pack('<III', 86, 4, 73207))
        data += section(21, 0, b'capabilities') + section(23, 0, archive)
        data += section(4, 4, b'wrapped') + section(99, 0, b'unknown')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'connectx.upd'
            source.write_bytes(data)
            output = root / 'out'
            report = extract_package(source, output)
            self.assertFalse(report['complete'])
            self.assertEqual(len(report['sections']), 5)
            self.assertEqual(report['sections'][3]['extraction'], 'blocked-key')
            self.assertEqual(report['sections'][2]['members'][1]['sha256'], hashlib.sha256(b'library').hexdigest())
            self.assertEqual((output / 'connectx-section-02-application-archive-files/lib/example').read_bytes(), b'library')
            stored = [c for c in report['components'] if c['kind'] == 'stored-section']
            self.assertEqual(len(stored), 5)
            self.assertEqual((output / stored[3]['filename']).read_bytes(), b'wrapped')
            self.assertEqual(report['sha256'], hashlib.sha256(data).hexdigest())

    def test_tar_paths_cannot_escape_and_links_are_only_inventoried(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name in ('../outside', '/absolute'):
                archive = tar_payload([(name, b'bad', tarfile.REGTYPE)])
                with self.assertRaisesRegex(ValueError, 'unsafe'):
                    unpack_tar(archive, root)
            archive = tar_payload([('linked', b'', tarfile.SYMTYPE), ('real', b'data', tarfile.REGTYPE)])
            inventory = unpack_tar(archive, root)
            self.assertEqual(inventory[0]['target'], '../../outside')
            self.assertFalse((root / 'linked').exists())
            self.assertEqual((root / 'real').read_bytes(), b'data')

    def test_every_tar_regular_member_is_extracted_and_duplicates_rejected(self):
        archive = tar_payload([('same', b'data', tarfile.REGTYPE)] * 2)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                unpack_tar(archive, Path(root))

    def test_tar_root_directory_entries_are_accepted_but_empty_names_are_rejected(self):
        for name in ('.', './'):
            archive = tar_payload([(name, b'', tarfile.DIRTYPE), ('bin/tool', b'ELF', tarfile.REGTYPE)])
            with tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                inventory = unpack_tar(archive, root)
                self.assertEqual(inventory[0]['path'], '.')
                self.assertEqual((root / 'bin/tool').read_bytes(), b'ELF')
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaisesRegex(ValueError, 'unsafe'):
                unpack_tar(tar_payload([('', b'bad', tarfile.REGTYPE)]), Path(temporary))

    def test_big_endian_legacy_squashfs_is_also_unpacked(self):
        data = section(1, 0, struct.pack('<III', 34, 16, 37101)) + section(4, 0, b'sqsh' + b'fixture')
        with tempfile.TemporaryDirectory() as temporary:
            source = Path(temporary) / 'old.upd'
            source.write_bytes(data)
            with patch('sonos_firmware.connectx.subprocess.run') as run:
                with patch('sonos_firmware.connectx.filesystem_manifest', return_value={'entries': []}):
                    report = extract_package(source, Path(temporary) / 'out')
            self.assertEqual(run.call_args.args[0][0], 'unsquashfs')
            self.assertIn('filesystem', report['sections'][1])


if __name__ == '__main__':
    unittest.main()
