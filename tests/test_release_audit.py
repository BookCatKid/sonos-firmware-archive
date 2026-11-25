"""Release auditing follows configured targets and verified publication state."""
import importlib.util
import json
import sys
import tempfile
import unittest
import urllib.error
import hashlib
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from sonos_firmware.storage import atomic_json
from sonos_firmware.firmware_archive import reconcile_documents, document_name, register_document

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

    def test_a_duplicate_publication_is_distinguished_from_an_unexplained_asset(self):
        """An asset already recorded under another release explains its own bytes."""
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root/'config/targets.json', {'schema_version': 1, 'targets': [
                {'name': 'apps', 'provider': 'fixture', 'description': 'Fixture',
                 'interval_hours': 6, 'metadata': ['data/apps']}]})
            atomic_json(root/'data/apps/archive.json', {'artifacts': [
                asset('source.bin', release_tag='firmware-fixture', status='verified')]})
            duplicated = {'bytes': 10, 'sha256': 'a'*64,
                          'url': 'https://github.com/owner/archive/releases/download/seed/source.bin'}
            unexplained = {'bytes': 99, 'sha256': 'f'*64,
                           'url': 'https://github.com/owner/archive/releases/download/seed/other.bin'}
            output = root/'audit.json'
            with patch.object(audit, 'github_assets', return_value=(2, {
                    ('firmware-fixture', 'source.bin'): {'bytes': 10, 'sha256': 'a'*64,
                        'url': 'https://github.com/owner/archive/releases/download/firmware-fixture/source.bin'},
                    ('seed', 'source.bin'): duplicated,
                    ('seed', 'other.bin'): unexplained})), \
                 patch.object(audit, 'recorded_assets', return_value={('firmware-fixture', 'source.bin'): {
                     'bytes': 10, 'sha256': 'a'*64, 'source': 'data/apps/archive.json'}}), \
                 patch('sys.argv', ['audit', '--repo', 'owner/archive', '--output', str(output)]):
                self.assertEqual(audit.main(), 0)
            report = json.loads(output.read_text())
        self.assertEqual(report['missing_total'], 0)
        self.assertEqual(report['unrecorded_extra_total'], 2)
        self.assertEqual(report['duplicated_extra_total'], 1)
        self.assertEqual(report['unexplained_extra_total'], 1)
        self.assertEqual([row['release_asset'] for row in report['duplicated_extras']], ['source.bin'])
        self.assertEqual([row['recorded_as'] for row in report['duplicated_extras']],
                         [[{'release_tag': 'firmware-fixture', 'release_asset': 'source.bin'}]])
        self.assertEqual([row['release_asset'] for row in report['unexplained_extras']], ['other.bin'])
        self.assertEqual(report['status'], 'partial')


class DocumentRecoveryTests(unittest.TestCase):
    source = 'https://update.sonos.com/software/old-page.html'

    def publication(self, body, *, source=None):
        digest = hashlib.sha256(body).hexdigest()
        name = document_name({'url': source or self.source, 'sha256': digest, 'document_type': 'landing-page'})
        tag = 'manifest-snapshots-2026-01-01'
        return {(tag, name): {'bytes': len(body), 'sha256': digest,
            'url': 'https://github.com/owner/archive/releases/download/'+tag+'/'+name}}

    def recover(self, root, assets, fetch, **options):
        return reconcile_documents(root, 'owner/archive', assets, root/'worker', fetch=fetch, **options)

    def seed(self, root, assets, source):
        (tag, name), row = next(iter(assets.items()))
        proof = {'status': 'verified', 'repository': 'owner/archive', 'release_tag': tag,
                 'release_asset': name, 'release_url': row['url'], 'sha256': row['sha256'], 'bytes': row['bytes']}
        register_document(root/'data/catalog.json', {'url': source, 'sha256': row['sha256'],
                          'bytes': row['bytes'], 'document_type': 'landing-page'}, proof)

    def run_normal_audit(self, root, assets, bodies):
        from types import SimpleNamespace
        output = root/'data/platform/audits/release-assets.json'
        def recover(*args, **kwargs):
            return reconcile_documents(*args, **kwargs,
                fetch=lambda capture, path, repo: path.write_bytes(bodies[capture['sha256']]))
        # Stub only this script's environment lookup; the real process remains
        # outside CI and all acquisition uses generated fixture bytes.
        with patch.object(audit, '__file__', str(root/'scripts/audit_release_assets.py')), \
             patch.object(audit, 'os', SimpleNamespace(environ={'GITHUB_ACTIONS': 'true'})), \
             patch.object(audit, 'github_assets', return_value=(len({key[0] for key in assets}), assets)), \
             patch('sonos_firmware.firmware_archive.reconcile_documents', side_effect=recover), \
             patch('sys.argv', ['audit', '--repo', 'owner/archive', '--output', str(output), '--recover-documents']):
            self.assertEqual(audit.main(), 0)
        return json.loads(output.read_text())

    def test_main_recovers_each_origin_even_when_the_body_was_already_preserved(self):
        other = self.source.replace('old-page', 'alternate-page')
        body = ('<a href="'+self.source+'">one</a><a href="'+other+'">two</a>').encode()
        first, second = self.publication(body), self.publication(body, source=other)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.seed(root, first, self.source)
            report = self.run_normal_audit(root, {**first, **second}, {hashlib.sha256(body).hexdigest(): body})
            records = json.loads((root/'data/catalog.json').read_text())['source_documents']
            self.assertEqual({row['source_url'] for row in records}, {self.source, other})
            self.assertEqual(report['recorded_asset_total'], 2)
            self.assertEqual(report['document_recovery']['pending'], 0)
            self.assertEqual(report['status'], 'ok')

    def test_pending_origin_recovery_keeps_duplicate_only_audit_partial(self):
        body = ('<a href="'+self.source+'">one</a>').encode()
        first = self.publication(body)
        second = self.publication(body, source=self.source.replace('old-page', 'lost-origin'))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.seed(root, first, self.source)
            report = self.run_normal_audit(root, {**first, **second}, {hashlib.sha256(body).hexdigest(): body})
            self.assertEqual(report['unexplained_extra_total'], 0)
            self.assertEqual(report['document_recovery']['pending'], 1)
            self.assertEqual(report['status'], 'partial')

    def test_alternate_publications_do_not_overwrite_the_prior_capture_location(self):
        body = ('<a href="'+self.source+'">source</a>').encode()
        first = self.publication(body)
        (_, name), row = next(iter(first.items()))
        second = {('manifest-snapshots-2026-01-01-part-2', name): {
            **row, 'url': row['url'].replace('2026-01-01/', '2026-01-01-part-2/')}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.seed(root, first, self.source)
            self.recover(root, second, lambda capture, path, repo: path.write_bytes(body))
            self.assertEqual(set(audit.recorded_assets(root, 'owner/archive')), set(first)|set(second))
            records = json.loads((root/'data/catalog.json').read_text())['source_documents']
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['alternate_publications'][0]['release_tag'], next(iter(first))[0])

    def test_legacy_publication_ownership_is_retained_when_a_new_repository_captures_it(self):
        digest = 'a'*64
        source = self.source.replace('old-page.html', 'update.upm')
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root/'data/catalog.json', {'schema_version': 1, 'packages': [], 'raw_images': [],
                'source_manifests': [{'source_url': source, 'sha256': digest, 'bytes': 10,
                    'release_tag': 'old', 'release_asset': 'old.upm',
                    'release_url': 'https://github.com/other/archive/releases/download/old/old.upm'}]})
            publication = {'status': 'verified', 'repository': 'owner/archive', 'sha256': digest, 'bytes': 10,
                'release_tag': 'new', 'release_asset': 'new.upm',
                'release_url': 'https://github.com/owner/archive/releases/download/new/new.upm'}
            register_document(root/'data/catalog.json', {'url': source, 'sha256': digest, 'bytes': 10}, publication)
            self.assertEqual(set(audit.recorded_assets(root, 'owner/archive')), {('new', 'new.upm')})
            self.assertEqual(set(audit.recorded_assets(root, 'other/archive')), {('old', 'old.upm')})

    def test_recovery_binds_published_bytes_to_an_observed_origin(self):
        body = b'<html>an earlier capture</html>'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root/'data/discovery/firmware-frontier.json', {'documents': [{'url': self.source}]})
            result = self.recover(root, self.publication(body), lambda capture, path, repo: path.write_bytes(body))
            record = json.loads((root/'data/catalog.json').read_text())['source_documents'][0]
            self.assertEqual(record['source_url'], self.source)
            self.assertEqual(record['publication']['sha256'], hashlib.sha256(body).hexdigest())
            self.assertEqual(record['publication']['repository'], 'owner/archive')
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(result['pending'], 0)

    def test_recovery_uses_observations_from_every_declared_target_metadata_path(self):
        body = b'<html>fixture capture with no self link</html>'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            atomic_json(root/'config/targets.json', {'schema_version': 1, 'targets': [
                {'name': 'another-source', 'provider': 'fixture', 'description': 'Fixture',
                 'interval_hours': 6, 'metadata': ['data/other-source']}]})
            atomic_json(root/'data/other-source/observations.json', {'observed_url': self.source})
            result = self.recover(root, self.publication(body), lambda capture, path, repo: path.write_bytes(body))
            self.assertEqual(result['status'], 'ok')
            record = json.loads((root/'data/catalog.json').read_text())['source_documents'][0]
            self.assertEqual(record['source_url'], self.source)

    def test_real_recovery_is_rejected_locally_before_acquisition(self):
        with tempfile.TemporaryDirectory() as directory, patch.dict('os.environ', {'GITHUB_ACTIONS': 'false'}):
            root = Path(directory)
            with self.assertRaisesRegex(ValueError, 'runs in CI'):
                reconcile_documents(root, 'owner/archive', {}, root/'worker')
            self.assertEqual(list(root.iterdir()), [])

    def test_manifest_origin_can_be_restored_from_its_native_payload_references(self):
        source = 'https://update.sonos.com/firmware/old/update.upm'
        body = (b'<update_manifest version="1.1" system_version="1.2-3"><image model="17" version="1.2-3">'
                b'https://update.sonos.com/firmware/old/1.2-3-1-17.upd?opaque=part/path</image></update_manifest>')
        digest = hashlib.sha256(body).hexdigest()
        name = document_name({'url': source, 'sha256': digest})
        assets = {('manifest-snapshots-2026-01-01', name): {'sha256': digest, 'bytes': len(body),
                  'url': 'https://github.com/owner/archive/releases/download/fixture/'+name}}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            self.recover(root, assets, lambda capture, path, repo: path.write_bytes(body))
            record = json.loads((root/'data/catalog.json').read_text())['source_manifests'][0]
            self.assertEqual(record['source_url'], source)
            self.assertEqual(record['version'], '1.2-3')

    def test_recovery_budget_is_per_run_and_all_remaining_captures_resume(self):
        assets, payloads = {}, {}
        for i in range(3):
            source = self.source+str(i)
            body = ('<a href="'+source+'">source</a>').encode()
            assets.update(self.publication(body, source=source))
            payloads[hashlib.sha256(body).hexdigest()] = body
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            state = None
            for expected_pending in (2, 1, 0):
                recorded = audit.recorded_assets(root, 'owner/archive')
                remaining = {key: row for key, row in assets.items() if key not in recorded}
                state = self.recover(root, remaining,
                    lambda capture, path, repo: path.write_bytes(payloads[capture['sha256']]),
                    previous=state, limit=1)
                self.assertEqual(state['pending'], expected_pending)
            self.assertEqual(len(audit.recorded_assets(root, 'owner/archive')), 3)
            self.assertEqual(state['status'], 'ok')

    def test_orphan_body_can_identify_its_origin_without_a_prepared_catalog(self):
        body = ('<link rel="canonical" href="'+self.source+'">').encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            result = self.recover(root, self.publication(body), lambda capture, path, repo: path.write_bytes(body))
            self.assertEqual(result['recovered'][0]['source_url'], self.source)
            self.assertIn(next(iter(self.publication(body))), audit.recorded_assets(root, 'owner/archive'))

    def test_unknown_origins_remain_unrecorded_and_retry_without_busy_looping(self):
        from sonos_firmware import retry
        from test_app_archive import NOW
        from unittest.mock import Mock
        body = b'<html>no origin metadata survived</html>'
        with tempfile.TemporaryDirectory() as directory, patch.object(retry, '_now', return_value=NOW):
            root = Path(directory)
            fetch = Mock(side_effect=lambda capture, path, repo: path.write_bytes(body))
            first = self.recover(root, self.publication(body), fetch)
            second = self.recover(root, self.publication(body), fetch, previous=first)
            self.assertEqual(fetch.call_count, 1)
            self.assertFalse((root/'data/catalog.json').exists())
            self.assertEqual(first['pending'], 1)
            self.assertEqual(second['status'], 'partial')
            self.assertEqual(next(iter(second['observations'].values()))['state'], 'origin-unresolved')

    def test_changed_bytes_and_external_origins_are_not_promoted(self):
        body = b'<html>original</html>'
        for changed in (True, False):
            with self.subTest(changed=changed), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = self.source if changed else 'https://github.com/example/manifests/page.html'
                original = body if changed else ('<a href="'+source+'">mirror</a>').encode()
                received = b'<html>modified</html>' if changed else original
                result = self.recover(root, self.publication(original, source=source),
                                      lambda capture, path, repo: path.write_bytes(received))
                self.assertFalse((root/'data/catalog.json').exists())
                self.assertEqual(result['status'], 'partial' if changed else 'ok')
                self.assertEqual(result['recovered'], [])
                if not changed:
                    observation = next(iter(result['observations'].values()))
                    self.assertEqual(observation['state'], 'external-origin')
                    self.assertEqual(observation['origin_host'], 'github.com')
                    self.assertNotIn('source_url', observation)

    def test_own_metadata_history_restores_an_origin_removed_from_current_state(self):
        body = b'<html>an interrupted capture without a self link</html>'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            def git(*args):
                subprocess.run(['git', *args], cwd=root, check=True,
                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            git('init', '-q')
            git('config', 'user.name', 'Fixture')
            git('config', 'user.email', 'fixture@example.test')
            metadata = root/'data/discovery/firmware-frontier.json'
            atomic_json(metadata, {'documents': [{'url': self.source}]})
            git('add', 'data')
            git('commit', '-qm', 'Observe source')
            atomic_json(metadata, {})
            git('commit', '-qam', 'Replace current observations')
            for i in range(5):
                atomic_json(metadata, {'tick': i})
                git('commit', '-qam', 'Advance metadata')
            assets = self.publication(body)
            fetch = lambda capture, path, repo: path.write_bytes(body)
            first = self.recover(root, assets, fetch, limit=1)
            self.assertEqual(first['pending'], 1)
            second = self.recover(root, assets, fetch, previous=first, limit=1)
            self.assertEqual(second['pending'], 0)
            record = json.loads((root/'data/catalog.json').read_text())['source_documents'][0]
            self.assertEqual(record['source_url'], self.source)
            self.assertTrue(second['origin_history']['matches'])

    def test_verified_foreign_publications_are_accounted_without_becoming_sonos_receipts(self):
        source = 'https://outside.test/reference.html'
        body = ('<a href="'+source+'">reference</a>').encode()
        assets = self.publication(body, source=source)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            first = self.run_normal_audit(root, assets, {hashlib.sha256(body).hexdigest(): body})
            second = self.run_normal_audit(root, assets, {hashlib.sha256(body).hexdigest(): body})
            for report in (first, second):
                self.assertEqual(report['external_origin_extra_total'], 1)
                self.assertEqual(report['unexplained_extra_total'], 0)
                self.assertEqual(report['recorded_asset_total'], 0)
                self.assertEqual(report['status'], 'ok')
                self.assertEqual(report['document_recovery']['pending'], 0)
            self.assertFalse((root/'data/catalog.json').exists())
            self.assertEqual(audit.recorded_assets(root, 'owner/archive'), {})

    def test_changed_foreign_asset_cannot_reuse_a_prior_exclusion(self):
        source = 'https://outside.test/reference.html'
        body = ('<a href="'+source+'">reference</a>').encode()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            assets = self.publication(body, source=source)
            first = self.recover(root, assets, lambda capture, path, repo: path.write_bytes(body))
            changed = {key: {**value, 'sha256': '0'*64} for key, value in assets.items()}
            second = self.recover(root, changed, lambda *args: self.fail('conflict must fail before download'), previous=first)
            self.assertEqual(second['external_origins'], [])
            self.assertEqual(second['pending'], 1)
            self.assertEqual(second['status'], 'partial')

    def test_non_metadata_git_history_does_not_supply_an_origin(self):
        body = b'<html>no source link</html>'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(['git', 'init', '-q'], cwd=root, check=True)
            (root/'README.md').write_text(json.dumps({'url': self.source}))
            subprocess.run(['git', 'add', 'README.md'], cwd=root, check=True)
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.test',
                            'commit', '-qm', 'Documentation only'], cwd=root, check=True)
            result = self.recover(root, self.publication(body), lambda capture, path, repo: path.write_bytes(body))
            self.assertEqual(result['pending'], 1)
            self.assertFalse((root/'data/catalog.json').exists())

    def test_history_lookup_failures_remain_deferred_then_resume_without_false_completion(self):
        from sonos_firmware import firmware_archive, retry
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        revision = 'a'*40
        identity = hashlib.sha256(self.source.encode()).hexdigest()
        history = {}
        log = revision+' 123\n'
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            args = (root, [Path('data/catalog.json')], {identity: None}, history)
            with patch.object(retry, '_now', return_value=now), patch.object(
                    firmware_archive.subprocess, 'check_output', side_effect=[log, subprocess.TimeoutExpired('git', 30)]):
                firmware_archive._historical_origins(*args, limit=1)
            self.assertEqual(history['status'], 'unavailable')
            self.assertIn(revision, history['retries'])
            with patch.object(retry, '_now', return_value=now), patch.object(
                    firmware_archive.subprocess, 'check_output', return_value=log) as command:
                firmware_archive._historical_origins(*args, limit=1)
            self.assertEqual(command.call_count, 1)
            self.assertEqual(history['status'], 'deferred')
            with patch.object(retry, '_now', return_value=now+timedelta(hours=2)), patch.object(
                    firmware_archive.subprocess, 'check_output', side_effect=[log, '+"url": '+json.dumps(self.source)]):
                firmware_archive._historical_origins(*args, limit=1)
            self.assertEqual(history['status'], 'searched')
            self.assertEqual(history['matches'][identity]['url'], self.source)
            self.assertEqual(history['retries'], {})

    def test_server_retry_after_blocks_unattempted_document_downloads_across_restarts(self):
        from sonos_firmware import retry
        from test_app_archive import NOW
        from unittest.mock import Mock
        body = b'<html>capture</html>'
        assets = {**self.publication(body), **self.publication(body+b'2')}
        with tempfile.TemporaryDirectory() as directory, patch.object(retry, '_now', return_value=NOW):
            root = Path(directory)
            fetch = Mock(side_effect=urllib.error.HTTPError('https://github.com/fixture', 429, 'later',
                                                          {'Retry-After': '7200'}, None))
            first = self.recover(root, assets, fetch)
            second = self.recover(root, assets, fetch, previous=first)
            self.assertEqual(fetch.call_count, 1)
            self.assertEqual(second['pending'], 2)

    def test_interruption_keeps_successful_receipts_in_the_normal_catalog(self):
        bodies = [('<!-- source --><a href="'+self.source+str(i)+'">link</a>').encode() for i in range(2)]
        assets = {}
        payloads = {}
        for i, body in enumerate(bodies):
            assets.update(self.publication(body, source=self.source+str(i)))
            payloads[hashlib.sha256(body).hexdigest()] = body
        attempts = []
        def interrupted(capture, path, repository):
            attempts.append(capture['sha256'])
            if len(attempts) == 2:
                raise KeyboardInterrupt('worker stopped')
            path.write_bytes(payloads[capture['sha256']])
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(KeyboardInterrupt):
                self.recover(root, assets, interrupted)
            records = json.loads((root/'data/catalog.json').read_text())['source_documents']
            self.assertEqual(len(records), 1)
            self.assertEqual(records[0]['sha256'], attempts[0])
