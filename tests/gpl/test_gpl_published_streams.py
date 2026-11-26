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

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('audit_gpl_published_streams', ROOT / 'scripts/gpl/audit_gpl_published_streams.py')
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

    def test_normal_worker_processes_all_due_streams_without_batch_caps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = {f'{i}.tgz': self.published(f'{i}.tgz', str(i)*64, 10) for i in range(1, 4)}
            (root/'artifacts.json').write_text(json.dumps({'artifacts': {'7.0': rows}}))
            args = Namespace(output=root, store=root/'ephemeral', version=[], retry_failed=False,
                             delay=0)
            calls = []
            def download(item, store, **kwargs):
                calls.append(item['filename'])
                return {'state': 'verified', 'sha256': rows[item['filename']]['sha256'], 'bytes': 10,
                        'verification': 'tar-and-compression-integrity', 'members': 1}
            result = MOD.run(args, download=download, wait=lambda delay: None)
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['verified'], 3)
            self.assertEqual(result['remaining_before'], 3)
            self.assertEqual(result['remaining_after'], 0)
            self.assertEqual(calls, ['1.tgz', '2.tgz', '3.tgz'])
            self.assertEqual(json.loads((root/'stream-audit.json').read_text()), result)

    def test_shared_deadline_preserves_unfinished_audit_work_for_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = {f'{i}.tgz': self.published(f'{i}.tgz', str(i)*64, 10) for i in range(1, 4)}
            (root/'artifacts.json').write_text(json.dumps({'artifacts': {'7.0': rows}}))
            args = Namespace(output=root, store=root/'ephemeral', version=[], retry_failed=False,
                             delay=0)
            elapsed = [0]
            calls = []
            def download(item, store, **kwargs):
                elapsed[0] += 1
                calls.append(item['filename'])
                return {'state': 'verified', 'sha256': rows[item['filename']]['sha256'], 'bytes': 10,
                        'verification': 'tar-and-compression-integrity', 'members': 1}
            with patch.object(MOD, 'winding_down', side_effect=lambda: len(calls) >= 2):
                first = MOD.run(args, download=download, wait=lambda delay: None)
            self.assertEqual(first['status'], 'partial')
            self.assertEqual(first['processed'], 2)
            self.assertEqual(first['remaining_after'], 1)
            second = MOD.run(args, download=download, wait=lambda delay: None)
            self.assertEqual(second['status'], 'ok')
            self.assertEqual(calls, ['1.tgz', '2.tgz', '3.tgz'])

    def test_normal_worker_stops_on_throttle_and_does_not_spin_on_cooldown(self):
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory, patch.object(retry, '_now', return_value=now):
            root = Path(directory)
            row = self.published('limited.tgz', 'a'*64, 10)
            (root/'artifacts.json').write_text(json.dumps({'artifacts': {'7.0': {'limited.tgz': row}}}))
            args = Namespace(output=root, store=root/'ephemeral', version=[], retry_failed=False,
                             delay=0)
            download = Mock(return_value={'state': 'http-error', 'http_status': 429, 'retry_after': 7200})
            first = MOD.run(args, download=download, wait=lambda delay: None)
            second = MOD.run(args, download=download, wait=lambda delay: None)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(first['failed'], 1)
            self.assertEqual(second['processed'], 0)
            self.assertEqual(second['status'], 'partial')

    def test_identical_hash_can_reuse_proven_full_stream_without_downloading(self):
        payload = b"some complete tar blob"
        digest = hashlib.sha256(payload).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = {"schema_version": 1, "artifacts": {
                "7.0": {"source1.tgz": self.published("source1.tgz", digest, len(payload))},
                "7.1": {"source2.tgz": self.published("source2.tgz", digest, len(payload))}}}
            (root / 'artifacts.json').write_text(json.dumps(data))
            args = Namespace(output=root, store=root / 'blobstore', version=[], retry_failed=False,
                             delay=2.5)
            calls = []
            def download(item, store):
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
            args = Namespace(output=root, store=root / 'blobstore', version=[], retry_failed=False,
                             delay=2.5)
            def fake(item, store):
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
            args = Namespace(output=root, store=root/'worker', version=[], retry_failed=False,
                             delay=0)
            for attempt, day in enumerate((0, 1, 7)):
                with patch.object(retry, '_now', return_value=now+timedelta(days=day)):
                    result = MOD.run(args, download=download, wait=lambda delay: None)
                report = json.loads((root/'stream-audit.json').read_text())
                self.assertEqual(report['status'], 'partial' if attempt < 2 else 'ok')
            self.assertEqual(result['remaining_after'], 0)
        self.assertEqual(calls, [bad, fresh, bad])


    def test_rate_limit_persists_server_deadline_across_worker_restart(self):
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            row = self.published('limited.tgz', 'a'*64, 10)
            (root/'artifacts.json').write_text(json.dumps({'artifacts': {'7.0': {'limited.tgz': row}}}))
            args = Namespace(output=root, store=root/'worker', version=[], retry_failed=False,
                             delay=0)
            download = Mock(return_value={'state': 'http-error', 'http_status': 429, 'retry_after': 7200})
            with patch.object(retry, '_now', return_value=now):
                MOD.run(args, download=download, wait=lambda delay: None)
            with patch.object(retry, '_now', return_value=now + timedelta(minutes=90)):
                result = MOD.run(args, download=download, wait=lambda delay: None)
            self.assertEqual(download.call_count, 1)
            self.assertEqual(result['selected'], 0)
            stream = json.loads((root/'artifacts.json').read_text())['artifacts']['7.0']['limited.tgz']['stream_integrity']
            self.assertEqual(stream['next_attempt_at'], (now + timedelta(hours=2)).isoformat())
            self.assertEqual(stream['error']['retry_after'], 7200)
