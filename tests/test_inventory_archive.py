import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import inventory_archive as inventory
from sonos_firmware.catalog import validate_catalog
from sonos_firmware.firmware_archive import register_capture
from sonos_firmware.storage import atomic_json
from test_firmware_archive import captured, candidate
from test_upd import section


class InventoryArchiveTests(unittest.TestCase):
    def source(self, root, name='one', body=None):
        body = body or section(99, 0, name.encode())
        row = candidate(name, filename=name+'.upd', package_model=8)
        package = register_capture(root, captured(row, body))
        return package, body

    def test_inventory_is_published_for_any_model_and_does_not_duplicate_ciphertext(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, body = self.source(root)
            uploads = []
            def publish(repo, tag, path, size, digest):
                self.assertEqual(path.stat().st_size, size)
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
                uploads.append(path.name)
                return {'url': 'https://github.com/fixture/asset'}
            with patch.object(inventory, 'publish_verified', side_effect=publish):
                result = inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys',
                    acquire=lambda package, path, repo: path.write_bytes(body))
            self.assertEqual(result['published'], 1)
            self.assertEqual(len(uploads), 2)
            record = json.loads((root/'data/catalog.json').read_text())['packages'][0]['inventory']
            self.assertTrue(record['complete'])
            self.assertEqual(record['source_sha256'], package['sha256'])
            self.assertEqual(validate_catalog(root), [])
            self.assertEqual(list((root/'worker').iterdir()), [])
            with patch.object(inventory, 'publish_verified') as publish:
                again = inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys', acquire=lambda *a: self.fail('source reacquired'))
            self.assertEqual(again['attempted'], 0)
            publish.assert_not_called()

    def test_bad_source_does_not_block_another_and_budget_counts_failures(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            first, _ = self.source(root, 'one')
            second, body = self.source(root, 'two')
            def acquire(package, path, repo):
                path.write_bytes(b'wrong' if package['id'] == first['id'] else body)
            with patch.object(inventory, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}):
                result = inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys', acquire=acquire, limit=2)
            self.assertEqual(result['attempted'], 2)
            self.assertEqual(result['published'], 1)
            rows = {row['id']: row for row in json.loads((root/'data/catalog.json').read_text())['packages']}
            self.assertNotIn('inventory', rows[first['id']])
            self.assertIn('inventory', rows[second['id']])

    def test_lost_upload_response_recovers_without_source_or_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, body = self.source(root)
            remote = {}
            def publish(repo, tag, path, size, digest):
                remote[path.name] = {'url': 'https://github.com/fixture/'+path.name, 'size': size, 'digest': digest}
                if path.name.endswith('.tar.xz'):
                    raise TimeoutError('lost response')
                return remote[path.name]
            with patch.object(inventory, 'publish_verified', side_effect=publish):
                first = inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys',
                    acquire=lambda package, path, repo: path.write_bytes(body))
            self.assertEqual(first['published'], 0)
            self.assertNotIn('inventory', json.loads((root/'data/catalog.json').read_text())['packages'][0])
            with patch.object(inventory, 'verified_asset', side_effect=lambda repo, tag, name, size, digest: remote.get(name)):
                result = inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys',
                    acquire=lambda *args: self.fail('source required for publication recovery'))
            self.assertEqual(result['published'], 1)
            self.assertEqual(result['pending'], 0)
            self.assertEqual(validate_catalog(root), [])

    def test_changed_decoder_or_new_key_makes_a_new_inventory_eligible(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, body = self.source(root)
            calls=[]
            def acquire(package, path, repo):
                calls.append(package['id'])
                path.write_bytes(body)
            with patch.object(inventory, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}), \
                 patch.object(inventory, 'decoder_revision', return_value='first'):
                inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys', acquire=acquire)
            with patch.object(inventory, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}), \
                 patch.object(inventory, 'decoder_revision', return_value='second'):
                result=inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys', acquire=acquire)
            self.assertEqual(result['published'], 1)
            self.assertEqual(len(calls),2)

    def test_missing_upload_rebuilds_with_single_slot_even_after_decoder_changes(self):
        for changed_decoder in (False, True):
            with self.subTest(changed_decoder=changed_decoder), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package, body = self.source(root)
                remote = {}
                def publish(repo, tag, path, size, digest):
                    if path.name.endswith('.tar.xz'):
                        raise TimeoutError('upload never reached GitHub')
                    remote[path.name] = {'url': 'https://github.com/fixture/'+path.name}
                    return remote[path.name]
                with patch.object(inventory, 'decoder_revision', return_value='old'), \
                     patch.object(inventory, 'publish_verified', side_effect=publish):
                    inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys', limit=1,
                        acquire=lambda package, path, repo: path.write_bytes(body))
                calls = []
                def acquire(package, path, repo):
                    calls.append(package['id'])
                    path.write_bytes(body)
                with patch.object(inventory, 'decoder_revision', return_value='new' if changed_decoder else 'old'), \
                     patch.object(inventory, 'verified_asset', side_effect=lambda repo, tag, name, size, digest: remote.get(name)), \
                     patch.object(inventory, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}):
                    result = inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys',
                        limit=1, acquire=acquire)
                self.assertEqual(calls, [package['id']])
                self.assertEqual(result['published'], 1)
                self.assertEqual(result['attempted'], 1)
                self.assertEqual(result['pending'], 0)
                self.assertEqual(validate_catalog(root), [])
                if changed_decoder:
                    state = json.loads((root/'data/discovery/firmware-inventories.json').read_text())
                    captures = list(state['repositories']['owner/repo']['captures'].values())
                    self.assertEqual(len(captures), 2)
                    old = next(row for row in captures if row['revision']['decoder_revision'] == 'old')
                    self.assertEqual(old['status'], 'incomplete')
                    self.assertEqual(old['publications']['manifest']['status'], 'verified')

    def test_catalog_rejects_inventory_bound_to_another_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary)
            self.source(root)
            path=root/'data/catalog.json'
            catalog=json.loads(path.read_text())
            catalog['packages'][0]['inventory']={'source_sha256':'0'*64,'complete':True,'publications':{}}
            atomic_json(path,catalog)
            errors=validate_catalog(root)
            self.assertTrue(any('another' in error for error in errors))
            self.assertTrue(any('bundle' in error for error in errors))

    def test_unavailable_keys_leave_explicit_spans_without_duplicating_ciphertext(self):
        import tarfile
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, body = self.source(root, body=section(4, 4, b'encrypted fixture'))
            def publish(repo, tag, path, size, digest):
                if path.name.endswith('.tar.xz'):
                    with tarfile.open(path) as archive:
                        self.assertEqual(archive.getnames(), ['inventory.json'])
                        report = json.load(archive.extractfile('inventory.json'))
                        self.assertEqual(report['sections'][0]['extraction'], 'blocked-key')
                        self.assertEqual(report['sections'][0]['source_span']['bytes'], len(b'encrypted fixture'))
                return {'url': 'https://github.com/fixture/asset'}
            with patch.object(inventory, 'publish_verified', side_effect=publish):
                result = inventory.inventory_preserved(root, root/'worker', 'owner/repo', root/'keys',
                    acquire=lambda package, path, repo: path.write_bytes(body))
            self.assertEqual(result['published'], 1)
            receipt = json.loads((root/'data/catalog.json').read_text())['packages'][0]['inventory']
            self.assertFalse(receipt['complete'])
            self.assertEqual(validate_catalog(root), [])

    def test_losing_a_key_cannot_replace_an_already_complete_public_inventory(self):
        from cryptography.hazmat.primitives.asymmetric import rsa
        from sonos_firmware.extract import key_recipient_id
        from sonos_firmware.mdp import write_private_key_exclusive
        from test_key_recovery import envelope
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
            package, body = self.source(root, body=section(99, 4, envelope(b'decoded metadata', key)))
            directory = root/'keys'
            directory.mkdir()
            path = directory/('recipient-'+key_recipient_id(key)+'.pem')
            write_private_key_exclusive(path, key)
            with patch.object(inventory, 'publish_verified', return_value={'url': 'https://github.com/fixture/asset'}):
                inventory.inventory_preserved(root, root/'worker', 'owner/repo', directory,
                    acquire=lambda package, destination, repo: destination.write_bytes(body))
                path.unlink()
                inventory.inventory_preserved(root, root/'worker', 'owner/repo', directory,
                    acquire=lambda package, destination, repo: destination.write_bytes(body))
            receipt = json.loads((root/'data/catalog.json').read_text())['packages'][0]['inventory']
            self.assertTrue(receipt['complete'])
            self.assertEqual(receipt['revision']['ready_recipients'], [key_recipient_id(key)])
