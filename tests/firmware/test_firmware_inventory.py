import unittest
import tempfile
from pathlib import Path
from sonos_firmware.firmware_inventory import inventory_package as extract_package
from sonos_firmware.firmware_inventory import bundle
from sonos_firmware.storage import atomic_json


def elf_fixture(bits=32, order='big', machine=20, entry=0x10000000):
    import struct
    ident = b'\x7fELF' + bytes([1 if bits == 32 else 2, 1 if order == 'little' else 2, 1, 3, 0]) + bytes(7)
    layout = ('<' if order == 'little' else '>') + ('HHIIIIIHHHHHH' if bits == 32 else 'HHIQQQIHHHHHH')
    return ident + struct.pack(layout, 2, machine, 1, entry, 0, 0, 0, 52 if bits == 32 else 64, 0, 0, 0, 0, 0)


class ExecutableInventoryTests(unittest.TestCase):
    def test_headers_describe_both_classes_and_byte_orders_without_architecture_whitelist(self):
        from sonos_firmware.firmware_inventory import elf_header
        for bits in (32, 64):
            for order in ('little', 'big'):
                with self.subTest(bits=bits, order=order):
                    entry = 0x10000000 if bits == 32 else 0x1234567890
                    metadata = elf_header(elf_fixture(bits, order, machine=65000, entry=entry))
                    self.assertEqual(metadata['bits'], bits)
                    self.assertEqual(metadata['byte_order'], order)
                    self.assertEqual(metadata['machine'], 65000)
                    self.assertIsNone(metadata['architecture'])
                    self.assertEqual(metadata['entry_point'], entry)
                    self.assertNotIn('header_error', metadata)
        self.assertIsNone(elf_header(b'ordinary file'))

    def test_application_members_and_raw_components_include_executable_architecture(self):
        import struct
        import tarfile
        from tests.firmware.test_upd import section
        from tests.firmware.test_anacapad import tar_payload
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'model.upd'
            ppc = elf_fixture()
            arm = elf_fixture(64, 'little', 183)
            source.write_bytes(section(1, 0, struct.pack('<III', 86, 4, 73207)) +
                section(23, 0, tar_payload([('bin/anacapad', ppc, tarfile.REGTYPE)])) + section(99, 0, arm))
            report = extract_package(source, root/'out')
            self.assertTrue(report['complete'], report['errors'])
            self.assertEqual(report['version'], '86.4-73207')
            member = report['sections'][1]['members'][0]
            self.assertEqual(member['elf']['architecture'], 'PowerPC')
            self.assertEqual(member['elf']['bits'], 32)
            self.assertEqual(member['elf']['byte_order'], 'big')
            self.assertEqual(report['components'][-1]['elf']['architecture'], 'AArch64')
            self.assertTrue((root/'out'/report['components'][-1]['filename']).is_file())


    def test_squashfs_executable_headers_are_described_without_following_links(self):
        from sonos_firmware import firmware_inventory as inventory
        from tests.firmware.test_upd import section
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'model.upd'
            source.write_bytes(section(4, 0, b'hsqs fixture'))
            def decoder(command, **kwargs):
                directory = Path(command[command.index('-d')+1])
                directory.mkdir()
                (directory/'tool').write_bytes(elf_fixture(64, 'little', 183))
                (directory/'external').symlink_to('/unavailable/firmware-link')
                return inventory.subprocess.CompletedProcess(command, 0)
            with patch.object(inventory.subprocess, 'run', side_effect=decoder):
                report = extract_package(source, root/'out')
            self.assertTrue(report['complete'], report['errors'])
            entries = {row['path']: row for row in report['sections'][0]['filesystem']['entries']}
            self.assertEqual(entries['tool']['elf']['architecture'], 'AArch64')
            self.assertNotIn('elf', entries['external'])

    def test_malformed_elf_header_is_reported_without_losing_extracted_bytes(self):
        import tarfile
        from tests.firmware.test_upd import section
        from tests.firmware.test_anacapad import tar_payload
        for body in (b'\x7fELF', elf_fixture()[:40], elf_fixture()[:4] + bytes([9]) + elf_fixture()[5:]):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source = root/'model.upd'
                source.write_bytes(section(23, 0, tar_payload([('bin/damaged', body, tarfile.REGTYPE)])))
                report = extract_package(source, root/'out')
                self.assertTrue(report['payloads_complete'])
                self.assertFalse(report['complete'])
                self.assertEqual(report['errors'][0]['type'], 'ELFHeaderError')
                self.assertEqual(report['errors'][0]['path'], 'bin/damaged')
                self.assertEqual((root/'out/model-section-00-application-archive-files/bin/damaged').read_bytes(), body)


class FirmwareBundleTests(unittest.TestCase):

    def test_extraction_bundles_match_across_host_umasks_and_preserve_firmware_modes(self):
        import os
        import io
        import struct
        import tarfile
        from tests.firmware.test_upd import section
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
                    report = extract_package(source, directory)
                    atomic_json(directory / 'inventory.json', report)
                    destination = root / f'{mask}.tar.xz'
                    bundle(directory, destination)
                    bundles.append(destination.read_bytes())
                    with tarfile.open(destination) as archive:
                        self.assertEqual(archive.getmember('connectx-section-01-application-archive-files/bin/anacapad').mode, 0o755)
                finally:
                    os.umask(original_umask)
            self.assertEqual(*bundles)


class GenericInventoryTests(unittest.TestCase):


    def test_every_plaintext_section_is_inventoried_without_a_version_header(self):
        from sonos_firmware.firmware_inventory import inventory_package
        from tests.firmware.test_upd import section
        from tests.firmware.test_anacapad import tar_payload
        import tarfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'arbitrary-model.upd'
            source.write_bytes(section(23, 0, tar_payload([('bin/tool', b'ELF', tarfile.REGTYPE)])) + section(99, 0, b'opaque'))
            report = inventory_package(source, root / 'out')
            self.assertTrue(report['complete'])
            self.assertIsNone(report['version'])
            self.assertEqual(len(report['components']), 2)
            self.assertEqual(report['derived'], [])
            self.assertEqual((root / 'out/arbitrary-model-section-00-application-archive-files/bin/tool').read_bytes(), b'ELF')
            self.assertEqual(report['sections'][1]['source_span']['bytes'], 6)

    def test_cramfs_contents_are_decoded_for_both_byte_orders_and_padded_superblocks(self):
        from sonos_firmware.firmware_inventory import inventory_package
        from tests.recovery.test_key_recovery import cramfs_image
        from tests.firmware.test_cramfs_little import _inode
        from tests.firmware.test_upd import section
        import hashlib
        import struct
        for order in ('big', 'little'):
            for pad in (0, 512):
                with self.subTest(order=order, pad=pad), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    fs = cramfs_image(b'updater fixture', order)
                    if pad:
                        data = bytearray(bytes(pad) + fs)
                        data[pad+0x40:pad+0x4c] = _inode(0o40755, 16, 0, pad+0x60, order)
                        data[pad+0x60:pad+0x6c] = _inode(0o40755, 20, 4, pad+0x80, order)
                        data[pad+0x80:pad+0x8c] = _inode(0o100755, 15, 8, pad+0xa0, order)
                        data[pad+0xa0:pad+0xa4] = struct.pack('>I' if order == 'big' else '<I', len(data))
                        fs = bytes(data)
                    source = root / 'model.upd'
                    source.write_bytes(section(4, 0, fs))
                    report = inventory_package(source, root / 'out')
                    self.assertTrue(report['complete'], report['errors'])
                    manifest = report['sections'][0]['filesystem']
                    self.assertEqual(manifest['byte_order'], order)
                    file = next(row for row in manifest['entries'] if row['path'] == 'bin/upgrade')
                    self.assertEqual(file['sha256'], hashlib.sha256(b'updater fixture').hexdigest())
                    self.assertEqual((root / 'out' / manifest['root'] / 'bin/upgrade').read_bytes(), b'updater fixture')

    def test_bad_decoder_does_not_lose_other_section_payloads(self):
        from sonos_firmware.firmware_inventory import inventory_package
        from tests.firmware.test_upd import section
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / 'mixed.upd'
            source.write_bytes(section(23, 0, b'invalid tar') + section(99, 0, b'other payload'))
            report = inventory_package(source, root / 'out')
            self.assertFalse(report['complete'])
            self.assertTrue(report['payloads_complete'])
            self.assertEqual(len(report['errors']), 1)
            self.assertEqual((root / 'out' / report['components'][1]['filename']).read_bytes(), b'other payload')


    def test_bad_archive_section_does_not_block_following_sections(self):
        from sonos_firmware.firmware_inventory import inventory_package
        from tests.firmware.test_upd import section
        from tests.firmware.test_anacapad import tar_payload
        import tarfile
        bad = tar_payload([('first', bytes(9), tarfile.REGTYPE), ('../escape', b'x', tarfile.REGTYPE)])
        good = tar_payload([('second', b'available', tarfile.REGTYPE)])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root/'mixed.upd'
            source.write_bytes(section(23, 0, bad) + section(23, 0, good))
            report = inventory_package(source, root/'out')
            self.assertEqual(len(report['errors']), 1)
            self.assertEqual(report['sections'][1]['members'][0]['path'], 'second')
            self.assertEqual((root/'out/mixed-section-01-application-archive-files/second').read_bytes(), b'available')

    def test_supported_uimage_compression_streams_are_extracted(self):
        from sonos_firmware.firmware_inventory import inventory_package
        from tests.firmware.test_upd import section
        import gzip
        import bz2
        import lzma
        import zlib
        compressors = {0: lambda data: data, 1: lambda data: gzip.compress(data, mtime=0),
                       2: bz2.compress, 3: lambda data: lzma.compress(data, format=lzma.FORMAT_ALONE)}
        for kind, compress in compressors.items():
            with self.subTest(compression=kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                compressed = compress(b'linux kernel')
                header = bytearray(64)
                header[:4] = bytes.fromhex('27051956')
                header[12:16] = len(compressed).to_bytes(4, 'big')
                header[31] = kind
                header[24:28] = zlib.crc32(compressed).to_bytes(4, 'big')
                header[4:8] = zlib.crc32(header).to_bytes(4, 'big')
                source = root / 'kernel.upd'
                source.write_bytes(section(6, 0, bytes(header) + compressed))
                report = inventory_package(source, root / 'out')
                self.assertTrue(report['complete'], report['errors'])
                self.assertEqual((root / 'out' / report['derived'][0]['filename']).read_bytes(), b'linux kernel')
                self.assertTrue(report['sections'][0]['uimage']['header_crc_valid'])
                self.assertTrue(report['sections'][0]['uimage']['data_crc_valid'])
