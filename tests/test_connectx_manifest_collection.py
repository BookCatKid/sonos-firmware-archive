import hashlib
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('discover_connectx_manifests', ROOT / 'scripts/discover_connectx_manifests.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class CollectionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.output = self.root / 'collection.json'
        self.xml = b'<update_manifest version="1.1"><image model="17" version="57.18-44230">https://update-firmware.sonos.com/firmware/Prod/known/57.18-44230-1-17.upd</image><image model="17" version="1.0-1">https://outside.test/file.upd</image></update_manifest>'
        self.blob = hashlib.sha1(b'blob ' + str(len(self.xml)).encode() + b'\0' + self.xml).hexdigest()
        self.tree = {'truncated': False, 'tree': [{'type': 'blob', 'path': 'manifest_files/known.upm', 'sha': self.blob}]}

    def api(self, path):
        return {'': {'default_branch': 'master'}, 'commits/master': {'sha': 'abc123'},
                'git/trees/abc123?recursive=1': self.tree}[path]

    def test_uses_default_branch_verifies_blob_and_keeps_only_official_model17_urls(self):
        with patch.object(MODULE, 'github', side_effect=self.api), patch.object(MODULE, 'fetch', return_value=self.xml):
            result = MODULE.discover(self.output, self.root / 'cache')
        self.assertEqual(len(result['manifests']), 1)
        self.assertEqual(result['manifests'][0]['github_blob_sha'], self.blob)
        self.assertEqual(len(result['candidates']), 1)
        self.assertEqual(result['candidates'][0]['version'], '57.18-44230')
        self.assertIn('/abc123/', result['candidates'][0]['evidence'][0]['url'])

    def test_unchanged_commit_needs_no_manifest_download(self):
        self.output.write_text(json.dumps({'commit': 'abc123', 'manifests': [], 'candidates': []}))
        with patch.object(MODULE, 'github', side_effect=self.api), patch.object(MODULE, 'fetch') as fetch:
            MODULE.discover(self.output, self.root / 'cache')
        fetch.assert_not_called()

    def test_hash_failure_preserves_previous_discovery(self):
        original = json.dumps({'commit': 'old', 'manifests': [], 'candidates': []})
        self.output.write_text(original)
        with patch.object(MODULE, 'github', side_effect=self.api), patch.object(MODULE, 'fetch', return_value=b'wrong data'):
            with self.assertRaisesRegex(ValueError, 'blob hash mismatch'):
                MODULE.discover(self.output, self.root / 'cache')
        self.assertEqual(self.output.read_text(), original)

    def test_incomplete_repository_tree_is_rejected(self):
        self.tree['truncated'] = True
        with patch.object(MODULE, 'github', side_effect=self.api):
            with self.assertRaisesRegex(ValueError, 'incomplete'):
                MODULE.discover(self.output, self.root / 'cache')
        self.assertFalse(self.output.exists())

    def test_manifest_publication_is_checkpointed_only_after_verified_upload(self):
        with patch.object(MODULE, 'github', side_effect=self.api), patch.object(MODULE, 'fetch', return_value=self.xml):
            result = MODULE.discover(self.output, self.root / 'cache')
        with patch.object(MODULE, 'ensure_release'), patch.object(MODULE, 'upload_small_verified', side_effect=RuntimeError('failed')):
            with self.assertRaisesRegex(RuntimeError, 'failed'):
                MODULE.publish_collection(result, self.output, self.root / 'cache', 'owner/repo')
        self.assertNotIn('release_asset', json.loads(self.output.read_text())['manifests'][0])
        with patch.object(MODULE, 'ensure_release'), patch.object(MODULE, 'upload_small_verified') as upload:
            MODULE.publish_collection(result, self.output, self.root / 'cache', 'owner/repo')
            upload.assert_called_once()
        saved = json.loads(self.output.read_text())['manifests'][0]
        self.assertTrue(saved['release_asset'].endswith('.upm'))
        self.assertEqual(saved['release_tag'], 'manifest-snapshots-connectx-abc123-1')

    def test_changed_upstream_file_keeps_old_candidates_and_publication_receipts(self):
        with patch.object(MODULE, 'github', side_effect=self.api), patch.object(MODULE, 'fetch', return_value=self.xml):
            result = MODULE.discover(self.output, self.root / 'cache')
        result['manifests'][0].update(release_tag='old-tag', release_asset='old.upm')
        self.output.write_text(json.dumps(result))
        changed = self.xml.replace(b'57.18-44230', b'57.21-50280')
        blob = hashlib.sha1(b'blob ' + str(len(changed)).encode() + b'\0' + changed).hexdigest()
        def api(path):
            if path == '': return {'default_branch': 'master'}
            if path == 'commits/master': return {'sha': 'def456'}
            return {'truncated': False, 'tree': [{'type': 'blob', 'path': 'manifest_files/known.upm', 'sha': blob}]}
        with patch.object(MODULE, 'github', side_effect=api), patch.object(MODULE, 'fetch', return_value=changed):
            updated = MODULE.discover(self.output, self.root / 'cache')
        self.assertEqual({c['version'] for c in updated['candidates']}, {'57.18-44230', '57.21-50280'})
        old = next(m for m in updated['manifests'] if m['github_blob_sha'] == self.blob)
        self.assertEqual(old['release_asset'], 'old.upm')

    def test_publication_retries_keep_the_same_release_partition(self):
        record = {'path': 'manifest_files/new.upm', 'url': 'https://example.test/unused',
                  'github_blob_sha': self.blob, 'sha256': hashlib.sha256(self.xml).hexdigest(), 'bytes': len(self.xml)}
        result = {'commit': 'abc123', 'manifests': [{'release_asset': 'published.upm'} for _ in range(1000)] + [record]}
        cache = self.root / 'cache'
        cache.mkdir()
        (cache / self.blob).write_bytes(self.xml)
        with patch.object(MODULE, 'ensure_release') as release, patch.object(MODULE, 'upload_small_verified'):
            MODULE.publish_collection(result, self.output, cache, 'owner/repo')
        self.assertEqual(release.call_args.args[1], 'manifest-snapshots-connectx-abc123-2')
        self.assertEqual(record['release_tag'], 'manifest-snapshots-connectx-abc123-2')

    def test_publication_rejects_a_changed_cached_manifest(self):
        with patch.object(MODULE, 'github', side_effect=self.api), patch.object(MODULE, 'fetch', return_value=self.xml):
            result = MODULE.discover(self.output, self.root / 'cache')
        (self.root / 'cache' / self.blob).write_bytes(b'corrupt')
        with patch.object(MODULE, 'ensure_release'), patch.object(MODULE, 'upload_small_verified') as upload:
            with self.assertRaisesRegex(ValueError, 'body hash mismatch'):
                MODULE.publish_collection(result, self.output, self.root / 'cache', 'owner/repo')
            upload.assert_not_called()


if __name__ == '__main__':
    unittest.main()
