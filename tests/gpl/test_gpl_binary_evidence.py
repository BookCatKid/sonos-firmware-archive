"""Offline fixture tests for archived controller formats and binary references."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
import io
import tarfile
from pathlib import Path

from sonos_firmware.gpl_binary_evidence import scan_binary


class GPLBinaryReferenceTests(unittest.TestCase):
    def test_ascii_and_utf16_references_have_no_fixed_numeric_version_width(self):
        body = (b'ELF fixture /documents/gpl/1000.1000/gpl.html\0' +
                '/documents/gpl/0.100/gpl.html'.encode('utf-16-le'))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'fixture.bin'
            path.write_bytes(body)
            report = scan_binary(path)
        self.assertTrue(report['complete'])
        self.assertEqual(set(report['version_evidence']), {'1000.1000', '0.100'})

    def test_nested_tar_suffix_is_preserved_and_malformed_xapk_is_incomplete(self):
        bundle = io.BytesIO()
        with tarfile.open(fileobj=bundle, mode='w:xz') as archive:
            content = b'/documents/gpl/22.17/gpl.html'
            info = tarfile.TarInfo('bin/upgrade')
            info.size = len(content)
            archive.addfile(info, io.BytesIO(content))
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'outer.zip'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('payload.tar.xz', bundle.getvalue())
            report = scan_binary(path)
            self.assertTrue(report['complete'])
            self.assertEqual(report['version_evidence']['22.17'][0]['entry'], 'payload.tar.xz!bin/upgrade')
            path = Path(temporary)/'broken.xapk'
            path.write_bytes(b'PK\x03\x04broken central directory')
            report = scan_binary(path)
            self.assertFalse(report['complete'])
            self.assertEqual(report['format'], 'unreadable-zip')
    def test_nested_apk_is_decompressed_in_full(self):
        inner = io.BytesIO()
        with zipfile.ZipFile(inner, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('classes.dex', b'/documents/gpl/22.17/gpl.html')
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'controller.xapk'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('base.apk', inner.getvalue())
            result = scan_binary(path)
            self.assertTrue(result['complete'])
            self.assertEqual(result['version_evidence']['22.17'][0]['entry'], 'base.apk!classes.dex')

    def test_published_tar_bundle_scans_unextended_firmware_executables(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'inventory.tar.xz'
            with tarfile.open(path, 'w:xz') as archive:
                content = b'\x7fELF\0/documents/gpl/19.17/gpl.html'
                info = tarfile.TarInfo('filesystem/bin/upgrade')
                info.size = len(content)
                archive.addfile(info, io.BytesIO(content))
            result = scan_binary(path)
            self.assertEqual(result['format'], 'tar')
            self.assertTrue(result['complete'])
            self.assertEqual(result['version_evidence']['19.17'][0]['entry'], 'filesystem/bin/upgrade')
            aliased = Path(temporary)/'payload.bin'
            aliased.write_bytes(path.read_bytes())
            result = scan_binary(aliased)
            self.assertTrue(result['complete'])
            self.assertEqual(result['format'], 'tar')
            self.assertIn('19.17', result['version_evidence'])

    def test_failed_tar_decoding_keeps_evidence_from_already_decoded_members(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'partial.tar'
            with tarfile.open(path, 'w') as archive:
                content = b'/documents/gpl/19.17/gpl.html'
                for name in ('first', '../escape'):
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
            result = scan_binary(path)
            self.assertFalse(result['complete'])
            self.assertIn('19.17', result['version_evidence'])

    def test_scan_decompressed_android_dex_not_only_raw_apk_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sonos.apk"
            target = b"https://www.sonos.com/documents/gpl/22.17/gpl.html"
            with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as zip:
                zip.writestr('classes.dex', b'\x00' * 3000 + target + b'\x01' * 150)
                zip.writestr('res/drawable/splash.png', target)
            self.assertNotIn(target, path.read_bytes(), "the ZIP container itself must hide the link")
            report = scan_binary(path)
            self.assertEqual(report['version_evidence']['22.17'][0]['entry'], 'classes.dex')
            self.assertEqual(report['version_evidence']['22.17'][0]['format'], 'ascii')
            self.assertEqual(report['format'], 'zip')
            self.assertGreater(report['bytes_scanned'], 0)

    def test_invalid_ipa_container_is_not_claimed_as_a_complete_app_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'Sonos-S1.ipa'
            path.write_bytes(b'PK\x03\x04' + b'\x00' * 320 +
                             b'https://www.sonos.com/documents/gpl/19.17/gpl.html')
            report=scan_binary(path)
            self.assertEqual(report['format'],'unreadable-zip')
            self.assertFalse(report['complete'])
            self.assertTrue(any('central directory' in reason for reason in report['limitations']))
            self.assertIn('19.17',report['version_evidence'])


    def test_binary_reference_across_megabyte_stream_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'libsonos-jni.so'
            target=b'/documents/gpl/21.17/gpl.html'
            path.write_bytes(b'Q' * (1024*1024-7) + target + b'R' * 23)
            result=scan_binary(path)
            self.assertEqual(len(result['version_evidence']['21.17']),1)
            self.assertEqual(result['version_evidence']['21.17'][0]['offset'],1024*1024-7)
