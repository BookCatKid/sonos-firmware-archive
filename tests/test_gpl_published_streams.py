"""No-network tests for published GPL tar compressed-stream verification."""

import hashlib
import importlib.util
import json
import tempfile
import unittest
from argparse import Namespace
from pathlib import Path
from unittest.mock import patch, Mock
from datetime import datetime, timedelta, timezone
from sonos_firmware import retry

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('audit_gpl_published_streams', ROOT / 'scripts/audit_gpl_published_streams.py')
MOD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MOD)


class PublishedStreamAuditTests(unittest.TestCase):
    @staticmethod
    def published(name, digest, size, *, strong=False):
        return {"filename": name, "kind": "source", "state": "published", "published": True,
                "bytes": size, "sha256": digest,
                "source_url": "https://www.sonos.com/documents/gpl/7.0/" + name,
                "fetch_url": "https://www.sonos.com/documents/gpl/7.0/" + name,
                "verification": "tar-integrity",
                **({"stream_integrity": {"state": "verified", "expected_sha256": digest, "checked_at": "2026-11-12T00:00:00Z"}} if strong else {})}

    def test_identical_hash_can_reuse_proven_full_stream_without_downloading(self):
        payload = b"some complete tar blob"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"schema_version": 1, "artifacts": {
                "7.0": {"source1.tgz": self.published("source1.tgz", digest, len(payload))},
                "7.1": {"source2.tgz": self.published("source2.tgz", digest, len(payload))}}}
            (root / 'artifacts.json').write_text(json.dumps(data))
            args = Namespace(output=root, store=root / 'blobstore', limit=2, version=[], retry_failed=False,
                             reserve_bytes=0, delay=2.5)
            calls = []
            def download(item, store, *, min_free_bytes):
                calls.append(item['filename'])
                f = store / 'sha256' / digest[:2] / digest
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(payload)
                return {"state": "verified", "sha256": digest, "bytes": len(payload),
                        "verification": "tar-and-compression-integrity", "members": 1}
            result = MOD.run(args, download=download, wait=lambda delay: None)
            self.assertEqual(result['verified'], 2)
            self.assertEqual(result['downloaded'], 1)
            self.assertEqual(result['reused'], 1)
            self.assertEqual(calls, ['source1.tgz'])
            updated = json.loads((root / 'artifacts.json').read_text())['artifacts']
            self.assertEqual(updated['7.0']['source1.tgz']['stream_integrity']['method'], 'tar-and-compression-integrity')
            self.assertEqual(updated['7.1']['source2.tgz']['stream_integrity']['proof']['version'], '7.0')
            self.assertFalse((root / 'blobstore' / 'sha256' / digest[:2] / digest).exists())
            self.assertEqual(MOD.pending({"artifacts": updated}, versions=None), [])

    def test_digest_mismatch_never_rewrites_immutable_publication_receipt(self):
        wanted, observed = '1' * 64, '2' * 64
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            initial = self.published('mismatch.tgz', wanted, 12)
            data = {"schema_version": 1, "artifacts": {"8.1": {"mismatch.tgz": initial}}}
            (root / 'artifacts.json').write_text(json.dumps(data))
            args = Namespace(output=root, store=root / 'blobstore', limit=1, version=[], retry_failed=False,
                             reserve_bytes=0, delay=2.5)
            def fake(item, store, *, min_free_bytes):
                f = store / 'sha256' / observed[:2] / observed
                f.parent.mkdir(parents=True, exist_ok=True)
                f.write_bytes(b'different')
                return {"state": "verified", "sha256": observed, "bytes": 9,
                        "verification": "tar-and-compression-integrity", "members": 2}
            result = MOD.run(args, download=fake, wait=lambda delay: None)
            self.assertEqual(result['mismatched'], 1)
            row = json.loads((root / 'artifacts.json').read_text())['artifacts']['8.1']['mismatch.tgz']
            self.assertEqual(row['sha256'], wanted)
            self.assertEqual(row['bytes'], 12)
            self.assertTrue(row['published'])
            self.assertEqual(row['verification'], 'tar-integrity')
            self.assertEqual(row['stream_integrity']['state'], 'content-mismatch')
            self.assertEqual(row['stream_integrity']['observed_sha256'], observed)
            self.assertEqual(len(MOD.pending({"artifacts": {"8.1": {"mismatch.tgz": row}}},
                                         versions=None)), 1)

    def test_bare_verified_claim_cannot_serve_as_proof(self):
        digest = 'b' * 64
        row = self.published('bad.tgz', digest, 10, strong=True)
        records = {'artifacts': {'7.0': {'bad.tgz': row}}}
        self.assertEqual(MOD.strong_proofs(records), {})
        self.assertEqual(len(MOD.pending(records, versions=None)), 1)

    def test_failed_audits_retry_automatically_without_starving_new_sources(self):
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        bad, fresh = 'failed.tgz', 'fresh.tgz'
        rows = {name: self.published(name, hashlib.sha256(name.encode()).hexdigest(), 10) for name in (bad, fresh)}
        rows[bad]['stream_integrity'] = {'state': 'http-error', 'error': {'http_status': 403},
                                        'checked_at': '2026-01-01T00:00:00Z'}
        calls = []
        def download(item, store, **kwargs):
            name = item['filename']
            calls.append(name)
            if len(calls) == 1:
                return {'state': 'http-error', 'http_status': 404}
            return {'state': 'verified', 'sha256': rows[name]['sha256'], 'bytes': 10,
                    'verification': 'tar-and-compression-integrity', 'members': 1}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root/'artifacts.json').write_text(json.dumps({'artifacts': {'7.0': rows}}))
            args = Namespace(output=root, store=root/'worker', limit=1, version=[], retry_failed=False,
                             reserve_bytes=0, delay=0)
            for day in range(3):
                with patch.object(retry, '_now', return_value=now+timedelta(days=day)):
                    result = MOD.run(args, download=download, wait=lambda delay: None)
                report = json.loads((root/'stream-audit.json').read_text())
                self.assertEqual(report['status'], 'partial' if day < 2 else 'ok')
            self.assertEqual(result['remaining_after'], 0)
        self.assertEqual(calls, [bad, fresh, bad])

    def test_rate_limit_retains_all_pending_checks_and_honors_saved_cooldown(self):
        now = datetime(2026, 11, 16, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            row = self.published('limited.tgz', 'a'*64, 10)
            (root/'artifacts.json').write_text(json.dumps({'artifacts': {'7.0': {'limited.tgz': row}}}))
            args = Namespace(output=root, store=root/'worker', limit=1, version=[], retry_failed=False,
                             reserve_bytes=0, delay=0)
            with patch.object(retry, '_now', return_value=now):
                download = Mock(return_value={'state': 'http-error', 'http_status': 429})
                first = MOD.run(args, download=download, wait=lambda delay: None)
                second = MOD.run(args, download=download, wait=lambda delay: None)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(first['remaining_after'], 1)
            self.assertEqual(second['selected'], 0)
            self.assertEqual(second['status'], 'partial')

    def test_genuine_strong_prior_check_already_accepted(self):
        h = 'a' * 64
        row = self.published('a.txz', h, 3, strong=True)
        row['stream_integrity'].update(method='tar-and-compression-integrity',
                                       observed_sha256=h, observed_bytes=3, members=1)
        rows = {'artifacts': {'7.0': {'a.txz': row}}}
        self.assertEqual(len(MOD.strong_proofs(rows)), 1)
        self.assertEqual(MOD.pending(rows, versions=None), [])
        row['sha256'] = 'b' * 64
        self.assertEqual(MOD.strong_proofs(rows), {})
        self.assertEqual(len(MOD.pending(rows, versions=None)), 1)

class GPLReportIntegrityCoverageTests(unittest.TestCase):
    def test_report_distinguishes_published_tar_headers_from_full_stream_verification(self):
        spec = importlib.util.spec_from_file_location('archive_gpl_integrity_coverage', ROOT / 'scripts/archive_gpl_direct.py')
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            out.joinpath('catalog.json').write_text(json.dumps({'schema_version': 1, 'releases': {
                '7.0': {'state': 'verified', 'links': [
                    {'filename': 'a.tgz', 'kind': 'source', 'origin': 'sonos', 'fetch_url': 'https://www.sonos.com/documents/gpl/7.0/a.tgz'},
                    {'filename': 'b.tgz', 'kind': 'source', 'origin': 'sonos', 'fetch_url': 'https://www.sonos.com/documents/gpl/7.0/b.tgz'}]},
                '7.1': {'state': 'verified', 'links': [
                    {'filename': 'c.tgz', 'kind': 'source', 'origin': 'sonos', 'fetch_url': 'https://www.sonos.com/documents/gpl/7.1/c.tgz'}]},
            }}))
            a = PublishedStreamAuditTests.published('a.tgz', 'a'*64, 10)
            b = PublishedStreamAuditTests.published('b.tgz', 'b'*64, 10)
            a['stream_integrity'] = {'state': 'verified', 'expected_sha256': 'a'*64}
            out.joinpath('artifacts.json').write_text(json.dumps({'artifacts': {
                '7.0': {'gpl.html': {'published': True}, 'a.tgz': a, 'b.tgz': b},
                '7.1': {'gpl.html': {'published': True}},
            }}))
            from argparse import Namespace
            self.assertEqual(mod.report(Namespace(output=out)), 0)
            report = json.loads((out/'report.json').read_text())
            self.assertEqual(report['fully_published_releases'], 1)
            self.assertEqual(report['published_source_archives'], 2)
            self.assertEqual(report['full_compressed_stream_verified_source_archives'], 1)
            self.assertEqual(report['published_source_archives_awaiting_full_stream_verification'], 1)
            self.assertTrue(report['release_coverage']['7.0']['complete_publication'])
            self.assertFalse(report['release_coverage']['7.1']['complete_publication'])
            self.assertEqual(report['release_coverage']['7.1']['remaining_assets'], 1)
