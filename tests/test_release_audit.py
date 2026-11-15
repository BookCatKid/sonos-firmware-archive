"""Release auditing follows configured targets and verified publication state."""
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

from sonos_firmware.storage import atomic_json

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('release_audit', ROOT/'scripts/audit_release_assets.py')
audit = importlib.util.module_from_spec(spec)
spec.loader.exec_module(audit)


def asset(name='source.bin', **extra):
    return {'release_tag': 'fixture', 'release_asset': name, 'sha256': 'a'*64, 'bytes': 10,
            'repository': 'owner/archive', **extra}


class ReleaseAuditTests(unittest.TestCase):
    def test_pending_capture_journals_are_not_published_asset_claims(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'data/apps/archive.json', {'artifacts': [asset()],
                'pending_captures': [{'artifact': asset('pending.bin')}],
                'unpublished_captures': [asset('unpublished.bin')],
                'observations': {'source': {'pending_publication': asset('interrupted.bin')}}})
            self.assertEqual(set(audit.recorded_assets(root)), {('fixture', 'source.bin')})

    def test_new_target_metadata_is_audited_without_another_path_registry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'config/targets.json', {'schema_version': 1, 'targets': [
                {'name': 'new-source', 'provider': 'fixture', 'description': 'Fixture',
                 'interval_hours': 6, 'metadata': ['data/new-source']}]})
            atomic_json(root/'data/new-source/captures.json', {'artifacts': [asset()]})
            self.assertEqual(set(audit.recorded_assets(root)), {('fixture', 'source.bin')})

    def test_observed_capture_identity_is_not_itself_publication_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'data/apps/archive.json', {'observations': {'source': asset()},
                'verified_capture': asset('verified.bin', status='verified')})
            self.assertEqual(set(audit.recorded_assets(root)), {('fixture', 'verified.bin')})

    def test_audit_output_cannot_promote_its_own_unrecorded_extras(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'config/targets.json', {'schema_version': 1, 'targets': [
                {'name': 'release-audit', 'provider': 'release-audit', 'description': 'Fixture',
                 'interval_hours': 6, 'metadata': ['data/platform/audit.json']}]})
            atomic_json(root/'data/platform/audit.json', {'unrecorded_extras': [asset(status='verified')]})
            self.assertEqual(audit.recorded_assets(root), {})

    def test_child_assets_inherit_their_published_collection_tag(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            child = asset()
            del child['release_tag']
            atomic_json(root/'data/apps/archive.json', {'artifacts': [
                {'release_tag': 'fixture', 'components': [child]}]})
            self.assertEqual(set(audit.recorded_assets(root)), {('fixture', 'source.bin')})

    def test_legacy_alternate_web_bundles_remain_expected_assets(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'data/apps/archive.json', {'deployments': [
                {'release_tag': 'fixture', 'alternate_bundles': [asset('alternate.tar.gz')]}]})
            self.assertEqual(set(audit.recorded_assets(root)), {('fixture', 'alternate.tar.gz')})

    def test_selected_repository_does_not_inherit_another_repositories_publications(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'data/apps/archive.json', {'artifacts': [asset('other.bin', repository='other/archive'),
                asset('current.bin'), asset('legacy-url.bin', repository=None,
                    release_url='https://github.com/other/archive/releases/download/fixture/legacy-url.bin')]})
            self.assertEqual(set(audit.recorded_assets(root, 'owner/archive')), {('fixture', 'current.bin')})

    def test_conflicting_claims_are_reported_instead_of_overwriting_each_other(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'data/apps/one.json', {'artifacts': [asset()]})
            atomic_json(root/'data/apps/two.json', {'artifacts': [asset(sha256='b'*64)]})
            with self.assertRaisesRegex(ValueError, 'conflicting'):
                audit.recorded_assets(root)

    def test_asset_inventory_uses_shared_paginated_release_reader(self):
        listed = [[{'tag_name': 'fixture', 'assets': []}]]
        observed = {'source.bin': {'size': 10, 'digest': 'sha256:'+'a'*64,
            'browser_download_url': 'https://github.com/owner/archive/releases/download/fixture/source.bin'}}
        with patch.object(audit.subprocess, 'check_output', return_value=json.dumps(listed)), \
             patch.object(audit, 'remote_assets', return_value=observed) as fetch:
            count, result = audit.github_assets('owner/archive')
        self.assertEqual(count, 1)
        self.assertEqual(set(result), {('fixture', 'source.bin')})
        fetch.assert_called_once_with('owner/archive', 'fixture')

    def test_authenticated_release_listing_can_use_shared_graphql_fallback(self):
        listed = [[{'tag_name': 'fixture', 'assets': []}]]
        observed = [{'name': 'source.bin', 'size': 10, 'digest': 'sha256:'+'a'*64,
            'browser_download_url': 'https://github.com/owner/archive/releases/download/fixture/source.bin'}]
        with patch.object(audit.subprocess, 'check_output', return_value=json.dumps(listed)), \
             patch.object(audit, 'remote_assets', side_effect=urllib.error.HTTPError('fixture', 404, 'hidden', {}, None)), \
             patch.object(audit, 'graphql_assets', return_value=observed) as fetch:
            count, result = audit.github_assets('owner/archive')
        self.assertEqual(count, 1)
        self.assertEqual(set(result), {('fixture', 'source.bin')})
        fetch.assert_called_once_with('owner/archive', 'fixture')
