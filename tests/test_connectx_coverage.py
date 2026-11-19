"""ConnectX historical coverage is reported from evidence, not assumed.

ConnectX lives behind a mutable URL and is also advertised under a versioned
path in every firmware manifest. The audit must show which contents are
preserved with a receipt, prove they are distinct, and name the ones that are
gone rather than quietly counting them as covered.
"""
import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('connectx_coverage', ROOT/'scripts/audit_connectx_coverage.py')
coverage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(coverage)


def component(version, *, status='preserved', digest=None, manifest='67.1-27100', **extra):
    row = {
        'id': f'{version}-1-17', 'version': version, 'package_model': 17,
        'filename': f'{version}-1-17.upd',
        'source_url': f'https://update-firmware.sonos.com/firmware/Prod/{version}-GA-1/{version}-1-17.upd',
        'artifact_status': status,
    }
    if status == 'preserved':
        row.update(bytes=1_000, sha256=digest or (version.encode().hex() + '0' * 64)[:64],
                   release_tag=f'firmware-{version}', release_asset=f'{version}-1-17.upd',
                   source_manifest=manifest)
    row.update(extra)
    return row


def catalog(*rows, other=()):
    return {'schema_version': 1, 'packages': [*rows, *other]}


class ConnectXCoverageTests(unittest.TestCase):
    def test_preserved_contents_are_counted_with_their_receipts(self):
        report = coverage.audit(catalog(component('55.1-74250'), component('57.14-37030')))
        self.assertEqual(report['advertised_versions'], 2)
        self.assertEqual(report['preserved_components'], 2)
        self.assertEqual(report['distinct_preserved_contents'], 2)
        self.assertEqual(report['manifest_attributed'], 2)
        self.assertEqual(report['errors'], [])

    def test_only_model_17_components_are_claimed_as_connectx(self):
        other = {'id': 'x', 'version': '57.14-37030', 'package_model': 9,
                 'filename': 'x.upd', 'artifact_status': 'preserved'}
        report = coverage.audit({'schema_version': 1, 'packages': [component('57.14-37030'), other]})
        self.assertEqual(report['advertised_components'], 1)
        self.assertEqual(report['preserved_components'], 1)
        self.assertEqual(report['errors'], [])

    def test_one_content_republished_under_two_versions_is_reported_not_hidden(self):
        same = 'a' * 64
        report = coverage.audit(catalog(component('55.1-74250', digest=same),
                                        component('55.2-74260', digest=same)))
        self.assertEqual(report['preserved_components'], 2)
        self.assertEqual(report['distinct_preserved_contents'], 1)
        self.assertEqual(report['reused_content_across_versions'], [same])

    def test_a_preserved_component_without_a_release_receipt_is_an_error(self):
        row = component('55.1-74250')
        row.pop('release_asset')
        report = coverage.audit(catalog(row))
        self.assertEqual([e['error'] for e in report['errors']], ['preserved without release_asset'])

    def test_a_preserved_component_without_bytes_or_digest_is_an_error(self):
        for field in ('sha256', 'bytes'):
            with self.subTest(field=field):
                row = component('55.1-74250')
                row.pop(field)
                report = coverage.audit(catalog(row))
                self.assertEqual([e['error'] for e in report['errors']],
                                 [f'preserved without {field}'])

    def test_a_component_in_an_unknown_state_is_not_silently_accepted(self):
        report = coverage.audit(catalog(component('55.1-74250', status='maybe')))
        self.assertEqual([e['error'] for e in report['errors']],
                         ["unaccounted state 'maybe'"])

    def test_a_withdrawn_component_is_named_with_its_source_url(self):
        report = coverage.audit(catalog(component('96.1-79270', status='missing-cdn')))
        self.assertEqual(report['preserved_components'], 0)
        self.assertEqual([row['version'] for row in report['unavailable']], ['96.1-79270'])
        self.assertEqual(report['unavailable'][0]['state'], 'missing-cdn')
        self.assertTrue(report['unavailable'][0]['source_url'].startswith('https://update-firmware.sonos.com/'))
        self.assertEqual(report['errors'], [])

    def test_a_duplicate_package_identity_is_an_error(self):
        report = coverage.audit(catalog(component('55.1-74250'), component('55.1-74250')))
        self.assertIn('duplicate package identity', [e['error'] for e in report['errors']])

    def test_a_non_firmware_release_tag_is_an_error(self):
        row = component('55.1-74250', release_tag='seed-2026-08-31')
        report = coverage.audit(catalog(row))
        self.assertEqual([e['error'] for e in report['errors']],
                         ["unexpected release tag 'seed-2026-08-31'"])

    def test_historical_index_overlap_is_reported_both_ways(self):
        index = {'snapshots': [
            {'version': '55.1-74250', 'sha256': 'a'*64, 'extraction_asset': 'x.tar.gz'},
            {'version': '86.4-73207', 'sha256': 'b'*64, 'extraction_asset': 'y.tar.gz'},
        ]}
        report = coverage.audit(catalog(component('55.1-74250'),
                                        component('97.1-80312', status='missing-cdn')), index)
        historical = report['historical_index']
        self.assertEqual(historical['snapshots'], 2)
        self.assertEqual(historical['distinct_contents'], 2)
        self.assertEqual(historical['versions_only_in_index'], ['86.4-73207'])
        self.assertEqual(historical['versions_only_in_catalog'], ['97.1-80312'])
        self.assertEqual(historical['shared_versions'], 1)
        self.assertEqual(historical['snapshots_missing_extraction'], 0)

    def test_a_snapshot_without_an_extraction_receipt_is_counted(self):
        index = {'snapshots': [{'version': '55.1-74250', 'sha256': 'a'*64}]}
        report = coverage.audit(catalog(component('55.1-74250')), index)
        self.assertEqual(report['historical_index']['snapshots_missing_extraction'], 1)

    def test_the_committed_archive_reports_its_own_coverage(self):
        """Whatever archive this checkout holds, the audit explains every link.

        The historical index is optional evidence; a different checkout may not
        have it. The invariants below must hold either way.
        """
        index_path = ROOT/'data/discovery/connectx-index.json'
        index = json.loads(index_path.read_text()) if index_path.exists() else None
        report = coverage.audit(json.loads((ROOT/'data/catalog.json').read_text()), index)
        self.assertEqual(report['errors'], [])
        # Preserved contents are distinct, not one file relabelled many times.
        self.assertEqual(report['distinct_preserved_contents'], report['preserved_components'])
        # Every preserved component falls in exactly one provenance class: it was
        # reached through a manifest document, at a version-pinned URL, or neither.
        self.assertEqual(sum(report['provenance'].values()), report['preserved_components'])
        self.assertLessEqual(report['manifest_attributed'], report['preserved_components'])
        # Withdrawn components are named rather than counted as covered.
        self.assertEqual(len(report['unavailable']),
                         report['advertised_components'] - report['preserved_components'])
        self.assertLessEqual(report['preserved_components'], report['advertised_components'])

    def test_the_cli_writes_a_report_and_fails_only_on_inconsistency(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'catalog.json').write_text(json.dumps(catalog(component('55.1-74250'))))
            output = root/'coverage.json'
            with patch.object(sys, 'argv', ['audit', '--catalog', str(root/'catalog.json'),
                                            '--index', str(root/'missing.json'), '--output', str(output)]):
                self.assertEqual(coverage.main(), 0)
            self.assertEqual(json.loads(output.read_text())['preserved_components'], 1)


if __name__ == '__main__':
    unittest.main()
