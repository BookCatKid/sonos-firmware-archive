import hashlib
import io
import json
import struct
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sonos_firmware.firmware_inventory import inventory_package as extract_package, legacy_kernel_data, package_version, unpack_tar
from test_upd import section
from test_anacapad import tar_payload


class FirmwareSectionTests(unittest.TestCase):
    def test_tar_without_optional_trailing_padding_has_a_clean_eof(self):
        header = tarfile.TarInfo('file')
        header.size = 4
        payload = header.tobuf() + b'data' + bytes(508)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            rows = unpack_tar(payload, root)
            self.assertEqual(len(rows), 1)
            self.assertEqual((root/'file').read_bytes(), b'data')

    def test_compressed_tar_members_and_metadata_obey_the_same_budget(self):
        import bz2
        import gzip
        import lzma
        from sonos_firmware.firmware_inventory import _Budget, InventoryLimits
        raw = tar_payload([('bin/tool', b'payload', tarfile.REGTYPE)])
        for encode in (gzip.compress, bz2.compress, lzma.compress):
            with self.subTest(compression=encode.__module__), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                rows = unpack_tar(encode(raw), root)
                self.assertEqual(rows[0]['sha256'], hashlib.sha256(b'payload').hexdigest())
                self.assertEqual((root/'bin/tool').read_bytes(), b'payload')
                # Padding and extended metadata are also bounded, before a
                # compressed stream can consume arbitrary worker storage.
                with self.assertRaisesRegex(ValueError, 'resource budget'):
                    unpack_tar(encode(raw + bytes(32768)), root,
                        budget=_Budget(InventoryLimits(expanded_bytes=100, files=1)))

    def test_tar_extended_headers_are_bounded_before_metadata_is_parsed(self):
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

    def test_legacy_scan_uses_bounded_views_and_a_deadline(self):
        import gzip
        import zlib
        payload = b'\x1f\x8b\x08invalid' * 20 + gzip.compress(b'kernel')
        original = zlib.decompressobj
        seen = []
        class Decoder:
            def __init__(self, *args):
                self.decoder = original(*args)
            @property
            def eof(self):
                return self.decoder.eof
            def decompress(self, data, limit):
                seen.append(data)
                return self.decoder.decompress(data, limit)
        with patch('sonos_firmware.firmware_inventory.zlib.decompressobj', Decoder):
            self.assertEqual(legacy_kernel_data(payload)[1], b'kernel')
        self.assertTrue(all(isinstance(data, memoryview) and len(data) <= 65536 for data in seen))
        with patch('sonos_firmware.firmware_inventory.time.monotonic', side_effect=[0, 2]):
            with self.assertRaisesRegex(ValueError, 'resource budget'):
                legacy_kernel_data(payload, seconds=1)

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
            with patch('sonos_firmware.firmware_inventory._unpack_squashfs', return_value={'entries': []}) as decode:
                report = extract_package(source, Path(temporary) / 'out')
            self.assertEqual(decode.call_args.args[0].read_bytes(), b'sqsh' + b'fixture')
            self.assertIn('filesystem', report['sections'][1])


if __name__ == '__main__':
    unittest.main()
