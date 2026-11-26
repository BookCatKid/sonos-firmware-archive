import hashlib
import io
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path

from sonos_firmware.firmware_inventory import inventory_package as extract_package, legacy_kernel_data, package_version, unpack_tar
from tests.firmware.test_upd import section
from tests.firmware.test_anacapad import tar_payload


class FirmwareSectionTests(unittest.TestCase):


    def test_truncated_tar_extended_headers_are_rejected(self):
        import gzip
        header = tarfile.TarInfo('pax')
        header.type = tarfile.XHDTYPE
        header.size = 4 * 1024 ** 3
        payload = header.tobuf() + bytes(10240)
        with tempfile.TemporaryDirectory() as temporary:
            for body in (payload, gzip.compress(payload)):
                with self.subTest(compressed=body is not payload):
                    with self.assertRaises((ValueError, tarfile.ReadError)):
                        unpack_tar(body, Path(temporary))


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
            report = extract_package(source, output, store_sections=True)
            self.assertFalse(report['complete'])
            self.assertEqual(len(report['sections']), 5)
            self.assertEqual(report['sections'][3]['extraction'], 'blocked-key')
            self.assertEqual(report['sections'][2]['members'][1]['sha256'], hashlib.sha256(b'library').hexdigest())
            self.assertEqual((output / 'connectx-section-02-application-archive-files/lib/example').read_bytes(), b'library')
            stored = [c for c in report['derived'] if c['kind'] == 'stored-section']
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
            self.assertFalse((root / 'linked').is_symlink())
            self.assertFalse((root / 'linked').exists())
            self.assertEqual((root / 'real').read_bytes(), b'data')

    def test_every_tar_regular_member_is_extracted_and_duplicates_rejected(self):
        archive = tar_payload([('same', b'data', tarfile.REGTYPE)] * 2)
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(ValueError, 'duplicate'):
                unpack_tar(archive, Path(root))


if __name__ == '__main__':
    unittest.main()
