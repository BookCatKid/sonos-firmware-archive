"""Every advertised GPL link must end in an accounted state.

A verified index asserts that its sources exist. Each link therefore has to be
either published with a release receipt or recorded with the terminal reason it
could not be. A link with no receipt is silently unaccounted for: it never
reaches GitHub, so the publication audit cannot see it, and the stream audit
only revalidates published receipts.
"""
import importlib.util
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

from sonos_firmware.gpl_archive import verify_artifact

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('gpl_link_coverage', ROOT/'scripts/gpl/audit_gpl_link_coverage.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


def link(filename, *, origin='sonos', kind='source', version='14.12'):
    url = f'https://www.sonos.com/documents/gpl/{version}/{filename}'
    return {'filename': filename, 'kind': kind, 'url': url,
            'fetch_url': url if origin == 'sonos' else None, 'origin': origin, 'label': filename}


def catalog(*links, version='14.12', state='verified'):
    return {'schema_version': 1, 'releases': {version: {'state': state, 'links': list(links)}}}


def receipts(version='14.12', **entries):
    """Receipts are keyed by filename and bind the source URL they were fetched from."""
    for name, record in entries.items():
        record.setdefault('filename', name)
        record.setdefault('source_url', f'https://www.sonos.com/documents/gpl/{version}/{name}')
    return {'schema_version': 1, 'artifacts': {version: entries}}


class LinkCoverageTests(unittest.TestCase):
    def test_a_published_link_is_accounted_for(self):
        report = coverage.coverage(catalog(link('busybox.txz')),
                                   receipts(**{'busybox.txz': {'published': True,
                                                               'release_asset': 'busybox.txz'}}))
        self.assertEqual(report['unattempted_total'], 0)
        self.assertEqual(report['state_totals'], {'published': 1})
        self.assertEqual(report['link_total'], 1)


    def test_a_link_with_no_receipt_is_unattempted_and_actionable(self):
        report = coverage.coverage(catalog(link('libusb.txz')), receipts())
        self.assertEqual(report['unattempted_total'], 1)
        self.assertEqual([row['filename'] for row in report['unattempted']], ['libusb.txz'])
        self.assertEqual(report['unattempted'][0]['url'],
                         'https://www.sonos.com/documents/gpl/14.12/libusb.txz')


    def test_an_unverified_index_is_not_claimed_as_coverage(self):
        report = coverage.coverage(catalog(link('x.txz'), state='access-denied'), receipts())
        self.assertEqual(report['versions'], 0)
        self.assertEqual(report['link_total'], 0)
        self.assertEqual(report['unattempted_total'], 0)


    def test_the_cli_fails_on_unattempted_links_and_can_report_without_failing(self):
        import sys
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temporary:
            data = Path(temporary)
            (data/'catalog.json').write_text(json.dumps(catalog(link('libusb.txz'))))
            (data/'artifacts.json').write_text(json.dumps(receipts()))
            output = data/'coverage.json'
            with patch.object(sys, 'argv', ['audit', '--data', str(data), '--output', str(output)]):
                self.assertEqual(coverage.main(), 1)
            self.assertEqual(json.loads(output.read_text())['unattempted_total'], 1)
            with patch.object(sys, 'argv', ['audit', '--data', str(data),
                                            '--output', str(output), '--allow-unattempted']):
                self.assertEqual(coverage.main(), 0)


class EmptyArchiveValidationTests(unittest.TestCase):
    """A served file can be a valid stream that contains nothing."""


    def test_a_truncated_stream_with_a_plausible_member_list_is_rejected(self):
        """Tar iteration stops at its end marker; the compressor footer must too."""
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'source.txz'
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w:xz') as archive:
                info = tarfile.TarInfo('pkg/README')
                payload = b'fixture'
                info.size = len(payload)
                archive.addfile(info, io.BytesIO(payload))
            body = stream.getvalue()
            path.write_bytes(body[:-16])
            ok, verification, _ = verify_artifact(path, 'source')
        self.assertFalse(ok)
        self.assertTrue(verification.startswith('invalid-archive') or verification == 'unknown-compression',
                        verification)

    def test_a_complete_archive_is_accepted_with_its_member_count(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'source.txz'
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w:xz') as archive:
                for name in ('pkg/README', 'pkg/COPYING'):
                    info = tarfile.TarInfo(name)
                    info.size = 3
                    archive.addfile(info, io.BytesIO(b'abc'))
            path.write_bytes(stream.getvalue())
            ok, verification, members = verify_artifact(path, 'source')
        self.assertTrue(ok)
        self.assertEqual(verification, 'tar-and-compression-integrity')
        self.assertEqual(members, 2)

    def test_a_member_escaping_the_archive_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'source.txz'
            stream = io.BytesIO()
            with tarfile.open(fileobj=stream, mode='w:xz') as archive:
                info = tarfile.TarInfo('../escape')
                info.size = 3
                archive.addfile(info, io.BytesIO(b'abc'))
            path.write_bytes(stream.getvalue())
            ok, verification, _ = verify_artifact(path, 'source')
        self.assertFalse(ok)
        self.assertEqual(verification, 'unsafe-member-path')


if __name__ == '__main__':
    unittest.main()
