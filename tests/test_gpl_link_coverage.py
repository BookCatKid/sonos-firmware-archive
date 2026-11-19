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
import lzma
import tarfile
import tempfile
import unittest
from pathlib import Path

from sonos_firmware.gpl_archive import verify_artifact

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('gpl_link_coverage', ROOT/'scripts/audit_gpl_link_coverage.py')
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

    def test_a_rejected_link_is_accounted_for_by_its_terminal_state(self):
        report = coverage.coverage(catalog(link('libusb.txz')),
                                   receipts(**{'libusb.txz': {'state': 'invalid-artifact',
                                                              'verification': 'empty-tar'}}))
        self.assertEqual(report['unattempted_total'], 0)
        self.assertEqual(report['state_totals'], {'recorded:invalid-artifact': 1})

    def test_a_link_with_no_receipt_is_unattempted_and_actionable(self):
        report = coverage.coverage(catalog(link('libusb.txz')), receipts())
        self.assertEqual(report['unattempted_total'], 1)
        self.assertEqual([row['filename'] for row in report['unattempted']], ['libusb.txz'])
        self.assertEqual(report['unattempted'][0]['url'],
                         'https://www.sonos.com/documents/gpl/14.12/libusb.txz')

    def test_upstream_links_are_covered_too(self):
        report = coverage.coverage(catalog(link('glibc-2.17.tar.gz', origin='upstream')), receipts())
        self.assertEqual(report['unattempted_total'], 1)
        self.assertEqual(report['unattempted'][0]['origin'], 'upstream')

    def test_an_unverified_index_is_not_claimed_as_coverage(self):
        report = coverage.coverage(catalog(link('x.txz'), state='access-denied'), receipts())
        self.assertEqual(report['versions'], 0)
        self.assertEqual(report['link_total'], 0)
        self.assertEqual(report['unattempted_total'], 0)

    def test_a_legacy_receipt_keyed_by_filename_still_counts_as_published(self):
        """Old receipts use the bare filename; identity must not be lost."""
        report = coverage.coverage(catalog(link('busybox.txz')),
                                   receipts(**{'busybox.txz': {'published': True}}))
        self.assertEqual(report['unattempted_total'], 0)

    def test_a_link_sharing_a_filename_with_another_url_is_tracked_separately(self):
        first = link('glibc-2.17.tar.gz')
        second = {**link('glibc-2.17.tar.gz'),
                  'url': 'https://ftp.gnu.org/gnu/glibc/glibc-2.17.tar.gz', 'origin': 'upstream'}
        report = coverage.coverage(
            catalog(first, second),
            receipts(**{'glibc-2.17.tar.gz': {'published': True,
                                              'source_url': first['url'],
                                              'filename': 'glibc-2.17.tar.gz'}}))
        # The Sonos copy is published; the upstream copy has its own identity.
        self.assertEqual(report['state_totals'], {'published': 1, 'unattempted': 1})
        self.assertEqual(report['unattempted'][0]['origin'], 'upstream')

    def test_the_real_catalog_and_receipts_report_their_own_coverage(self):
        """The committed archive data is consistent with this classifier.

        This asserts the invariant, not production counts: a different checkout
        may legitimately hold a smaller or larger catalogue.
        """
        report = coverage.coverage(
            json.loads((ROOT/'data/gpl/live/catalog.json').read_text()),
            json.loads((ROOT/'data/gpl/live/artifacts.json').read_text()))
        self.assertGreaterEqual(report['versions'], 1)
        self.assertGreaterEqual(report['link_total'], 1)
        self.assertEqual(report['unattempted_total'], len(report['unattempted']))
        # The classifier accounts for every advertised link exactly once.
        self.assertEqual(sum(report['state_totals'].values()), report['link_total'])
        self.assertEqual(sum(report['state_totals'].values()), sum(row['links'] for row in report['versions_detail']))
        for state in report['state_totals']:
            self.assertTrue(state == 'published' or state == 'unattempted' or state.startswith('recorded:'), state)
        # Every unattempted link is named with the URL a worker would retry.
        for row in report['unattempted']:
            self.assertTrue(row['url'].startswith('https://'))
            self.assertTrue(row['filename'])

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

    def empty_xz_tar(self, path):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:xz'):
            pass
        path.write_bytes(stream.getvalue())
        return stream.getvalue()

    def test_a_valid_compressed_stream_with_no_members_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'libusb-1.0.23.txz'
            body = self.empty_xz_tar(path)
            # The stream itself is well-formed, so only member validation catches it.
            self.assertEqual(lzma.decompress(body)[:0], b'')
            ok, verification, members = verify_artifact(path, 'source')
        self.assertFalse(ok)
        self.assertEqual(verification, 'empty-tar')
        self.assertEqual(members, 0)

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
