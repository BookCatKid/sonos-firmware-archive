"""No-network regression tests for real archived Sonos controller binaries."""
from __future__ import annotations

import tempfile
import unittest
import zipfile
from pathlib import Path

from sonos_firmware.gpl_binary_evidence import scan_binary


class GPLBinaryReferenceTests(unittest.TestCase):
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

    def test_scan_actual_macho_style_utf16le_strings(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'Sonos'
            url = '/documents/gpl/20.13/gpl.html'
            path.write_bytes(b'\xca\xfe\xba\xbe' + url.encode('utf-16le') + b'\x00\x00')
            report = scan_binary(path)
            self.assertEqual(report['version_evidence']['20.13'][0]['format'], 'utf16le')
            self.assertEqual(report['version_evidence']['20.13'][0]['entry'], None)
            self.assertEqual(report['format'], 'raw')

    def test_zip_with_no_executables_is_not_claimed_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'fake.apk'
            with zipfile.ZipFile(path,'w',zipfile.ZIP_DEFLATED) as archive:
                archive.writestr('res/drawable/logo.png',b'\x89PNG'+b'0'*320)
            report=scan_binary(path)
            self.assertEqual(report['entries_scanned'],0)
            self.assertFalse(report['complete'])
            self.assertTrue(any('no executable' in msg for msg in report['limitations']))

    def test_does_not_claim_full_scan_when_budget_exceeded(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'app.apk'
            with zipfile.ZipFile(path, 'w', zipfile.ZIP_DEFLATED) as zip:
                zip.writestr('classes.dex', b'https://www.sonos.com/documents/gpl/19.12/gpl.html' + b'X' * 8192)
            result = scan_binary(path, max_bytes=100)
            self.assertFalse(result['complete'])
            self.assertTrue(result['limitations'])

    def test_binary_discovery_does_not_infer_unrelated_numbers(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'sonos.so'
            path.write_bytes(b'firmware 85.0 + version 16.2, no GPL paths')
            result=scan_binary(path)
            self.assertEqual(result['version_evidence'], {})

    def test_binary_reference_across_megabyte_stream_boundary(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'libsonos-jni.so'
            target=b'/documents/gpl/21.17/gpl.html'
            path.write_bytes(b'Q' * (1024*1024-7) + target + b'R' * 23)
            result=scan_binary(path)
            self.assertEqual(len(result['version_evidence']['21.17']),1)
            self.assertEqual(result['version_evidence']['21.17'][0]['offset'],1024*1024-7)

    def test_binary_manifest_contains_no_user_absolute_path(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location('discover_gpl_bin_cli', Path(__file__).resolve().parents[1]/'scripts/discover_gpl_binary_references.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as tmp:
            inp=Path(tmp)/'sonos.ipa'
            inp.write_bytes(b'https://www.sonos.com/documents/gpl/22.19/gpl.html')
            scanned=scan_binary(inp)
            dst=Path(tmp)/'result.json'
            result=module.update(dst,[scanned])
            self.assertEqual(len(result['scans']),1)
            self.assertEqual(len(result['version_evidence']['22.19']),1)
            self.assertNotIn(tmp,dst.read_text())
            self.assertEqual(module.update(dst,[scanned])['version_evidence'],result['version_evidence'])

    def test_archive_discover_prioritizes_version_proven_by_app_binary(self):
        import importlib.util
        from argparse import Namespace
        from unittest.mock import patch
        from sonos_firmware.gpl_archive import HttpResult
        spec=importlib.util.spec_from_file_location('archive_gpl_binary_discover',Path(__file__).resolve().parents[1]/'scripts/archive_gpl_direct.py')
        mod=importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'source.apk'
            path.write_bytes(b'https://www.sonos.com/documents/gpl/22.17/gpl.html')
            args=Namespace(output=Path(tmp)/'out',binary=[path],recheck=False,major_max=25,minor_max=20,limit=1,delay=2.5)
            requested=[]
            def fake_get_page(url):
                requested.append(url)
                return HttpResult(403,b'<Error><Code>AccessDenied</Code></Error>','application/xml',url)
            with patch.object(mod,'seed_versions',return_value=set()), \
                 patch.object(mod,'offline_candidates',return_value={}), \
                 patch.object(mod,'get_page',side_effect=fake_get_page), \
                 patch.object(mod,'pause'):
                self.assertEqual(mod.discover(args),0)
            self.assertEqual(requested,['https://www.sonos.com/documents/gpl/22.17/gpl.html'])
            import json
            state=json.loads((args.output/'discovery.json').read_text())['candidates']['22.17']
            self.assertEqual(state['priority'],0)
            self.assertTrue(any(x.startswith('binary:source.apk:<raw>') for x in state['evidence']))
            self.assertEqual(mod.live_catalog(args.output)['releases'],{})
