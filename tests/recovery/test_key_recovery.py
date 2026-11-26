import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
import tarfile
import os
import unittest
import zlib
import io
import urllib.error
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sonos_firmware import key_recovery as recovery
from sonos_firmware.retry import due
from sonos_firmware.extract import key_recipient_id, load_private_key, extract_available_components
from sonos_firmware.mdp import write_private_key_exclusive
from sonos_firmware.legacy import (SYSTEM_WORD, UPDATER_MARKER, WRAPPER_BYTES, legacy_seed,
                                   discover_legacy_updater_key)
from sonos_firmware.storage import atomic_json
from sonos_firmware.upd import parse_bytes
from tests.firmware.test_cramfs_little import _inode
from tests.firmware.test_legacy import _synthetic_updater_from_seed
from tests.firmware.test_upd import section


def digest(body):
    return hashlib.sha256(body).hexdigest()


def cramfs_image(payload, order='big'):
    compressed = zlib.compress(payload)
    fs = bytearray(0xA4 + len(compressed))
    fs[:4] = bytes.fromhex('28cd3d45' if order == 'big' else '453dcd28')
    fs[0x40:0x4C] = _inode(0o40755, 16, 0, 0x60, order)
    fs[0x60:0x6C] = _inode(0o40755, 20, 4, 0x80, order)
    fs[0x6C:0x70] = b'bin\0'
    fs[0x80:0x8C] = _inode(0o100755, len(payload), 8, 0xA0, order)
    fs[0x8C:0x94] = b'upgrade\0'
    fs[0xA0:0xA4] = struct.pack('>I' if order == 'big' else '<I', len(fs))
    fs[0xA4:] = compressed
    return bytes(fs)


def source_catalog(root, model, rootfs, *, raw=True, identity='arbitrary-source'):
    body = section(4, 0, rootfs)
    package = {'id': identity, 'package_model': model, 'artifact_status': 'preserved', 'artifact_type': 'upd',
               'bytes': len(body), 'sha256': digest(body), 'release_tag': 'fixture-source', 'release_asset': identity + '.upd'}
    atomic_json(root / 'data/upd' / (identity + '.json'), {'sha256': digest(body),
                'sections': [s.to_dict() for s in parse_bytes(body)]})
    catalog = {'packages': [package], 'raw_images': []}
    if raw:
        catalog['raw_images'].append({'package_id': identity, 'kind': 'rootfs', 'section_index': 0,
           'bytes': len(rootfs), 'sha256': digest(rootfs), 'release_tag': 'fixture-source', 'release_asset': identity + '.anything'})
    return catalog, body


class RecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.transport_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.recipient = key_recipient_id(cls.key)

    def chained_catalog(self, root):
        filesystem = cramfs_image(self.updater(17))
        catalog, _ = source_catalog(root, 17, filesystem)
        body = section(4, 4, envelope(filesystem, self.transport_key))
        package = catalog['packages'][0]
        package.update(sha256=digest(body), bytes=len(body))
        atomic_json(root/'data/upd/arbitrary-source.json', {'sha256': digest(body),
            'sections': [row.to_dict() for row in parse_bytes(body)]})
        component = catalog['raw_images'][0]
        component.update(section_type=4, source_encrypted=True, recipient_id=key_recipient_id(self.transport_key))
        receipt = {'source_sha256': digest(body), 'components': [dict(component)]}
        atomic_json(root/'data/raw/arbitrary-source.json', receipt)
        return catalog, filesystem, receipt

    def test_published_decrypted_updater_can_recover_a_later_recipient(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, filesystem, _ = self.chained_catalog(root)
            visited = []
            def fetch(source, path, repo):
                visited.append(source)
                path.write_bytes(filesystem)
            output = root/'keys/rotated.pem'
            proof = recovery.recover_catalog_key(root, catalog, 17, self.recipient, output, 'owner/repo',
                read_artifact=fetch)
            self.assertEqual(key_recipient_id(load_private_key(output)), self.recipient)
            self.assertEqual([row['kind'] for row in visited], ['raw'])
            self.assertEqual(proof['source']['source_sha256'], catalog['packages'][0]['sha256'])
            self.assertEqual(proof['source']['source_recipient_id'], key_recipient_id(self.transport_key))
            self.assertTrue(proof['source']['section_encrypted'])

    def test_decrypted_source_needs_a_matching_parent_and_component_receipt(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _, receipt = self.chained_catalog(root)
            self.assertEqual(len(recovery.select_updater_sources(root, catalog, 17)), 1)
            path = root/'data/raw/arbitrary-source.json'
            for bad in [{**receipt, 'source_sha256': '0'*64},
                        {**receipt, 'components': [{**receipt['components'][0], 'sha256': '0'*64}]},
                        {**receipt, 'components': [{**receipt['components'][0], 'recipient_id': '0'*40}]},
                        [], None, {**receipt, 'components': None}, {**receipt, 'components': {}},
                        {**receipt, 'components': [None]}]:
                with self.subTest(receipt=bad):
                    atomic_json(path, bad)
                    self.assertEqual(recovery.select_updater_sources(root, catalog, 17), [])
            path.unlink()
            self.assertEqual(recovery.select_updater_sources(root, catalog, 17), [])


    def updater(self, model, *, order='big', word=SYSTEM_WORD, relocated=False):
        body = _synthetic_updater_from_seed(legacy_seed(model, word, byte_order=order), self.key)
        if relocated:
            start = body.index(UPDATER_MARKER) + len(UPDATER_MARKER)
            body = bytes(125) + body[start:start + WRAPPER_BYTES] + bytes(27)
        header = b'\x7fELF\x01' + bytes([1 if order == 'little' else 2])
        return header + body[len(header):]

    def test_all_known_legacy_derivations_without_fixed_versions_or_offsets(self):
        for model, order, word in [(1, 'little', 0), (8, 'big', SYSTEM_WORD), (9, 'big', SYSTEM_WORD),
                                   (12, 'big', 0), (16, 'big', SYSTEM_WORD), (17, 'big', SYSTEM_WORD)]:
            with self.subTest(model=model):
                updater = self.updater(model, order=order, word=word, relocated=model == 12)
                result = discover_legacy_updater_key(updater, model, self.recipient)
                self.assertEqual(key_recipient_id(result.key), self.recipient)
                self.assertEqual(result.byte_order, order)
                if model == 12:
                    self.assertEqual(result.wrapper_offset, 125)

    def test_wrong_recipient_and_invalid_markers_are_explicit_failures(self):
        with self.assertRaisesRegex(ValueError, 'requested recipient'):
            discover_legacy_updater_key(self.updater(17), 17, '0' * 40)
        with self.assertRaises(ValueError):
            discover_legacy_updater_key(bytes(100), 17, self.recipient)
        with self.assertRaises(ValueError):
            discover_legacy_updater_key(UPDATER_MARKER * 65, 17, self.recipient)


    def test_full_cramfs_source_recovery_from_metadata_without_version_inventory(self):
        for order in ('big', 'little'):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                updater = self.updater(1, order=order, word=0)
                filesystem = cramfs_image(updater, order)
                catalog, _ = source_catalog(root, 1, filesystem)
                output = root / 'keys/model1.pem'
                proof = recovery.recover_catalog_key(root, catalog, 1, self.recipient, output, 'owner/repo',
                           read_artifact=lambda source, path, repo, data=filesystem: path.write_bytes(data))
                self.assertEqual(key_recipient_id(load_private_key(output)), self.recipient)
                self.assertEqual(proof['source']['package_id'], 'arbitrary-source')
                self.assertEqual(proof['updater_sha256'], digest(updater))
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual([path.name for path in output.parent.iterdir()], ['model1.pem'])
                self.assertNotIn('PRIVATE KEY', json.dumps(proof))

    def test_embedded_private_keys_are_recovered_only_for_the_exact_recipient(self):
        for encoding in (serialization.Encoding.PEM, serialization.Encoding.DER):
            for form in (serialization.PrivateFormat.PKCS8, serialization.PrivateFormat.TraditionalOpenSSL):
                with self.subTest(encoding=encoding, form=form), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    wrong = self.transport_key.private_bytes(encoding, form, serialization.NoEncryption())
                    wanted = self.key.private_bytes(encoding, form, serialization.NoEncryption())
                    updater = b'\x7fELF'+bytes(19)+wrong+b'noise'+wanted+bytes(23)
                    filesystem = cramfs_image(updater)
                    catalog, _ = source_catalog(root, 27, filesystem)
                    output = root/'keys/recipient.pem'
                    proof = recovery.recover_catalog_key(root, catalog, 27, self.recipient, output, 'owner/repo',
                        read_artifact=lambda source, path, repo, data=filesystem: path.write_bytes(data))
                    self.assertEqual(key_recipient_id(load_private_key(output)), self.recipient)
                    self.assertEqual(proof['method'], 'embedded-updater-private-key')
                    self.assertEqual(proof['key_offset'], 23+len(wrong)+5)
                    self.assertEqual(proof['key_bytes'], len(wanted))
                    self.assertNotIn('PRIVATE KEY', json.dumps(proof))

    def test_embedded_scan_ignores_public_encrypted_and_malformed_keys(self):
        public = self.key.public_key().public_bytes(serialization.Encoding.PEM,
                                                   serialization.PublicFormat.SubjectPublicKeyInfo)
        encrypted = self.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                            serialization.BestAvailableEncryption(b'synthetic-password'))
        malformed = b'-----BEGIN PRIVATE KEY-----\ninvalid\n-----END PRIVATE KEY-----\n'
        self.assertIsNone(recovery.embedded_private_key(public+encrypted+malformed, self.recipient))
        self.assertIsNone(recovery.embedded_private_key(bytes(100), self.recipient))

    def component_catalog(self, root, payload, *, section_type=6, raw=True, encrypted=False):
        catalog, _ = source_catalog(root, 27, payload, raw=False)
        body = section(section_type, 4 if encrypted else 0,
                       envelope(payload, self.transport_key) if encrypted else payload)
        package = catalog['packages'][0]
        package.update(bytes=len(body), sha256=digest(body))
        inventory = {'sha256': digest(body), 'sections': [s.to_dict() for s in parse_bytes(body)]}
        atomic_json(root/'data/upd/arbitrary-source.json', inventory)
        if raw:
            component = {'package_id': package['id'], 'section_index': 0, 'section_type': section_type,
                         'bytes': len(payload), 'sha256': digest(payload),
                         'release_tag': 'fixture-source', 'release_asset': 'component.bin'}
            if encrypted:
                component.update(source_encrypted=True, recipient_id=key_recipient_id(self.transport_key))
            catalog['raw_images'].append(component)
            atomic_json(root/'data/raw/arbitrary-source.json',
                        {'source_sha256': digest(body), 'components': [dict(component)]})
        return catalog, body

    def test_plain_non_updater_components_recover_exact_keys_from_raw_or_upd(self):
        for raw in (True, False):
            for section_type in (6, 22, 999):
                with self.subTest(raw=raw, section_type=section_type), tempfile.TemporaryDirectory() as temporary:
                    root = Path(temporary)
                    private = self.key.private_bytes(serialization.Encoding.DER,
                        serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
                    payload = b'component prefix'+private+b'component suffix'
                    catalog, body = self.component_catalog(root, payload, raw=raw, section_type=section_type)
                    output = root/'keys/recipient.pem'
                    with patch.object(recovery, 'read_updater_member', side_effect=AssertionError('not a filesystem')):
                        proof = recovery.recover_catalog_key(root, catalog, 27, self.recipient, output, 'owner/repo',
                            read_artifact=lambda source, path, repo, data=payload if raw else body: path.write_bytes(data))
                    self.assertEqual(key_recipient_id(load_private_key(output)), self.recipient)
                    self.assertEqual(proof['method'], 'embedded-component-private-key')
                    self.assertEqual(proof['component_sha256'], digest(payload))
                    self.assertEqual(proof['source']['section_type'], section_type)
                    self.assertNotIn('updater_sha256', proof)

    def test_decrypted_non_updater_component_requires_exact_extraction_proof(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = self.key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            catalog, _ = self.component_catalog(root, payload, encrypted=True)
            receipt_path = root/'data/raw/arbitrary-source.json'
            receipt = json.loads(receipt_path.read_text())
            for bad in ({**receipt, 'source_sha256': '0'*64},
                        {**receipt, 'components': [{**receipt['components'][0], 'section_type': 4}]},
                        {**receipt, 'components': [{**receipt['components'][0], 'recipient_id': '0'*40}]}):
                atomic_json(receipt_path, bad)
                fetch = unittest.mock.Mock(side_effect=AssertionError('unproven source'))
                with self.assertRaises(ValueError):
                    recovery.recover_catalog_key(root, catalog, 27, self.recipient, root/'keys/key.pem',
                        'owner/repo', read_artifact=fetch)
                fetch.assert_not_called()
            atomic_json(receipt_path, receipt)
            proof = recovery.recover_catalog_key(root, catalog, 27, self.recipient, root/'keys/key.pem',
                'owner/repo', read_artifact=lambda source, path, repo: path.write_bytes(payload))
            self.assertEqual(proof['method'], 'embedded-component-private-key')
            self.assertTrue(proof['source']['section_encrypted'])

    def test_non_updater_capture_must_match_verified_section_before_scanning(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            payload = b'no key'
            catalog, body = self.component_catalog(root, payload, raw=False)
            inventory_path = root/'data/upd/arbitrary-source.json'
            inventory = json.loads(inventory_path.read_text())
            inventory['sections'][0]['section_type'] = 22
            atomic_json(inventory_path, inventory)
            with patch.object(recovery, 'embedded_private_key') as scan, self.assertRaises(ValueError):
                recovery.recover_catalog_key(root, catalog, 27, self.recipient, root/'keys/key.pem', 'owner/repo',
                    read_artifact=lambda source, path, repo: path.write_bytes(body))
            scan.assert_not_called()

    def inventory_catalog(self, root, members):
        catalog, _ = source_catalog(root, 27, b'fixture', raw=False)
        package = catalog['packages'][0]
        members = [*members, ('inventory.json', json.dumps({'sha256': package['sha256']}).encode())]
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:xz') as archive:
            for name, body in members:
                entry = tarfile.TarInfo(name)
                if body is None:
                    entry.type, entry.linkname = tarfile.SYMTYPE, '/outside/private-key.pem'
                    archive.addfile(entry)
                else:
                    entry.size = len(body)
                    archive.addfile(entry, io.BytesIO(body))
        body = stream.getvalue()
        package['inventory'] = {'source_sha256': package['sha256'],
            'revision': {'source_sha256': package['sha256']}, 'publications': {'bundle': {
                'status': 'verified', 'repository': 'owner/repo', 'sha256': digest(body), 'bytes': len(body),
                'release_tag': 'fixture-source', 'release_asset': 'inventory.tar.xz'}}}
        return catalog, body


    def test_inventory_sources_require_verified_matching_parent_receipts(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, body = self.inventory_catalog(root, [('file', b'no key')])
            inventory = catalog['packages'][0]['inventory']
            for field, value in [('source_sha256', '0'*64), ('revision', {'source_sha256': '0'*64}),
                                 ('publications', {'bundle': {**inventory['publications']['bundle'], 'status': 'pending'}})]:
                bad = {**inventory, field: value}
                catalog['packages'][0]['inventory'] = bad
                sources = recovery._select_sources(root, catalog, 27)
                self.assertFalse(any(row['kind'] == 'inventory-bundle' for row in sources))
            catalog['packages'][0]['inventory'] = inventory
            self.assertTrue(any(row['kind'] == 'inventory-bundle'
                                for row in recovery._select_sources(root, catalog, 27)))


    def test_inventory_candidate_key_is_not_written_before_manifest_validation(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = self.key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            catalog, body = self.inventory_catalog(root, [('etc/private.pem', private),
                ('inventory.json', json.dumps({'sha256': '0'*64}).encode())])
            output = root/'keys/key.pem'
            with self.assertRaises(ValueError):
                recovery.recover_catalog_key(root, catalog, 27, self.recipient, output, 'owner/repo',
                    read_artifact=lambda source, path, repo: path.write_bytes(body))
            self.assertFalse(output.exists())

    def test_normal_inventory_publication_becomes_a_verified_key_recovery_source(self):
        from sonos_firmware import inventory_archive
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            private = self.key.private_bytes(serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8, serialization.NoEncryption())
            catalog, original = source_catalog(root, 27, cramfs_image(private))
            atomic_json(root/'data/catalog.json', catalog)
            assets = {}
            def publish(repo, tag, path, size, sha256):
                assets[path.name] = path.read_bytes()
                return {'url': 'https://github.com/owner/repo/releases/download/'+tag+'/'+path.name}
            with patch.object(inventory_archive, 'publish_verified', side_effect=publish):
                result = inventory_archive.inventory_preserved(root, root/'inventory-work', 'owner/repo', root/'keys',
                    acquire=lambda package, path, repo: path.write_bytes(original))
            self.assertEqual(result['published'], 1)
            catalog = json.loads((root/'data/catalog.json').read_text())
            output = root/'keys/recovered.pem'
            proof = recovery.recover_catalog_key(root, catalog, 27, self.recipient, output, 'owner/repo',
                read_artifact=lambda source, path, repo: path.write_bytes(assets[source['release_asset']]))
            self.assertEqual(proof['method'], 'embedded-inventory-private-key')
            self.assertTrue(proof['member'].endswith('/bin/upgrade'))
            self.assertEqual(key_recipient_id(load_private_key(output)), self.recipient)


    def test_bad_raw_capture_falls_back_to_original_upd_before_decoding(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            updater = self.updater(17)
            filesystem = cramfs_image(updater)
            catalog, upd = source_catalog(root, 17, filesystem)
            calls = []
            def read_artifact(source, path, repo):
                calls.append(source['kind'])
                path.write_bytes(b'bad capture' if source['kind'] == 'raw' else upd)
            with patch.object(recovery, 'read_updater_member', wraps=recovery.read_updater_member) as read_member:
                proof = recovery.recover_catalog_key(root, catalog, 17, self.recipient, root / 'keys/model17.pem',
                                                     'owner/repo', read_artifact=read_artifact)
            self.assertEqual(calls, ['raw', 'upd'])
            read_member.assert_called_once()
            self.assertEqual(proof['source']['kind'], 'upd')


    def test_failed_sources_do_not_block_success_and_replay_in_a_fresh_worker(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog = {'packages': [], 'raw_images': []}
            payloads = {}
            for identity, body in [('a', b'unsuitable first'), ('b', b'unsuitable second'), ('c', self.updater(17))]:
                item, _ = source_catalog(root, 17, body, identity=identity)
                catalog['packages'].extend(item['packages'])
                catalog['raw_images'].extend(item['raw_images'])
                payloads[identity] = body
            visited = []
            def fetch(source, path, repo):
                visited.append(source['package_id'])
                path.write_bytes(payloads[source['package_id']])
            output = root / 'keys/model17.pem'
            proof = recovery.recover_catalog_key(root, catalog, 17, self.recipient, output, 'owner/repo',
                read_artifact=fetch, read_member=lambda path: path.read_bytes())
            self.assertEqual(visited[0], 'a')
            self.assertIn('b', visited)
            self.assertEqual(visited[-1], 'c')
            output.unlink()
            with patch.object(recovery, 'RECOVERY_REVISION', recovery.RECOVERY_REVISION+1):
                recovery.recover_catalog_key(root, catalog, 17, self.recipient, output, 'owner/repo',
                    read_artifact=fetch, read_member=lambda path: path.read_bytes())
            self.assertEqual(visited[-1], 'c')
            output.unlink()
            state_path = root/'data/discovery/key-recovery.json'
            state = json.loads(state_path.read_text())
            records = state['repositories']['owner/repo'][self.recipient]
            old_identity = digest(json.dumps(proof['source'], sort_keys=True).encode())
            for key in [key for key, row in records.items() if row.get('status') == 'recovered']:
                records[old_identity] = records.pop(key)
            atomic_json(state_path, state)
            recovery.recover_catalog_key(root, catalog, 17, self.recipient, output, 'owner/repo',
                read_artifact=fetch, read_member=lambda path: path.read_bytes())
            self.assertEqual(visited[-1], 'c')
            self.assertNotIn('PRIVATE KEY', (root / 'data/discovery/key-recovery.json').read_text())

    def test_recipient_without_sources_gets_durable_method_scoped_assessment(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with self.assertRaises(ValueError):
                recovery.recover_catalog_key(root, {'packages': []}, 13, self.recipient,
                    root/'keys/recipient.pem', 'owner/repo', read_artifact=lambda *args: self.fail('no source'))
            state = json.loads((root/'data/discovery/key-recovery.json').read_text())
            assessment = state['assessments']['owner/repo'][self.recipient]
            self.assertEqual(assessment['method'], 'catalog-components')
            self.assertIn('embedded-updater-private-key', assessment['methods'])
            self.assertEqual(assessment['model'], 13)
            self.assertEqual(assessment['eligible_sources'], 0)
            self.assertEqual(assessment['attempted_sources'], 0)
            self.assertEqual(assessment['status'], 'no-eligible-source')
            self.assertNotIn('PRIVATE KEY', json.dumps(state))


    def test_interrupted_first_attempt_leaves_assessment_and_retryable_source(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = source_catalog(root, 17, b'fixture', raw=False)
            def interrupt(*args):
                raise KeyboardInterrupt()
            with self.assertRaises(KeyboardInterrupt):
                recovery.recover_catalog_key(root, catalog, 17, self.recipient,
                    root/'keys/recipient.pem', 'owner/repo', read_artifact=interrupt)
            state = json.loads((root/'data/discovery/key-recovery.json').read_text())
            assessment = state['assessments']['owner/repo'][self.recipient]
            self.assertEqual(assessment['status'], 'in-progress')
            self.assertEqual(assessment['attempted_sources'], 1)
            self.assertEqual(assessment['pending_sources'], 1)
            self.assertEqual(state['repositories']['owner/repo'][self.recipient], {})

    def test_server_retry_timing_pauses_other_key_sources_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            updater = self.updater(17)
            catalog, _ = source_catalog(root, 17, updater)
            body = io.BytesIO(b'fixture throttle')
            failure = urllib.error.HTTPError('https://fixture.invalid', 429, 'rate limited',
                                            {'Retry-After': '7200'}, body)
            calls = []
            def fetch(source, path, repo):
                calls.append(source)
                if len(calls) == 1:
                    raise failure
                path.write_bytes(updater)
            output = root/'keys/recipient.pem'
            for revision in (recovery.RECOVERY_REVISION, recovery.RECOVERY_REVISION+1):
                with patch.object(recovery, 'RECOVERY_REVISION', revision):
                    with self.assertRaises(ValueError):
                        recovery.recover_catalog_key(root, catalog, 17, self.recipient, output,
                            'owner/repo', read_artifact=fetch, read_member=lambda path: path.read_bytes())
            self.assertEqual(len(calls), 1)
            self.assertTrue(body.closed)
            state = json.loads((root/'data/discovery/key-recovery.json').read_text())
            record = next(iter(state['repositories']['owner/repo'][self.recipient].values()))
            self.assertFalse(due(record, datetime.now(timezone.utc)+timedelta(hours=1)))
            with patch('sonos_firmware.retry._now', return_value=datetime.now(timezone.utc)+timedelta(hours=3)):
                recovery.recover_catalog_key(root, catalog, 17, self.recipient, output,
                    'owner/repo', read_artifact=fetch, read_member=lambda path: path.read_bytes())
            self.assertEqual(len(calls), 2)
            self.assertEqual(key_recipient_id(load_private_key(output)), self.recipient)


def envelope(payload, private_key):
    aes_key, iv = bytes(range(16)), bytes(range(16, 32))
    amount = 16 - len(payload) % 16
    encryptor = Cipher(algorithms.AES(aes_key), modes.CBC(iv)).encryptor()
    encrypted = iv + encryptor.update(payload + bytes([amount]) * amount) + encryptor.finalize()
    wrapped = private_key.public_key().encrypt(aes_key, padding.OAEP(mgf=padding.MGF1(hashes.SHA1()),
                                                                  algorithm=hashes.SHA1(), label=None))
    recipient = bytes.fromhex(key_recipient_id(private_key))
    length = 22 + len(recipient) + 8 + len(wrapped) + 8
    header = bytearray(22)
    header[:4] = bytes.fromhex('886499ca')
    header[4:8] = length.to_bytes(4, 'big')
    header[20] = len(recipient)
    return bytes(header) + recipient + bytes(4) + len(wrapped).to_bytes(4, 'big') + wrapped + \
        (1).to_bytes(4, 'big') + len(encrypted).to_bytes(4, 'big') + encrypted


class RecipientExtractionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.keys = [rsa.generate_private_key(public_exponent=65537, key_size=2048) for _ in range(2)]

    def test_each_section_uses_its_matching_recipient_and_missing_keys_preserve_plaintext(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'rotated.upd'
            package.write_bytes(section(4, 4, envelope(b'first rootfs', self.keys[0])) +
                                section(17, 4, envelope(b'second application', self.keys[1])) +
                                section(99, 0, b'plain metadata'))
            paths = {}
            for index, key in enumerate(self.keys):
                path = root / f'key-{index}.pem'
                write_private_key_exclusive(path, key)
                paths[key_recipient_id(key)] = path
            result = extract_available_components(package, root / 'complete', paths)
            self.assertEqual(result['skipped'], [])
            self.assertEqual([(root / 'complete' / row['filename']).read_bytes() for row in result['components']],
                             [b'first rootfs', b'second application', b'plain metadata'])
            paths.pop(key_recipient_id(self.keys[0]))
            result = extract_available_components(package, root / 'partial', paths)
            self.assertEqual([row['section_index'] for row in result['components']], [1, 2])
            self.assertEqual([row['section_index'] for row in result['skipped']], [0])
            bad = root / 'bad-key.pem'
            bad.write_text('invalid optional material')
            paths[key_recipient_id(self.keys[0])] = bad
            result = extract_available_components(package, root / 'invalid-key', paths)
            self.assertEqual([row['section_index'] for row in result['components']], [1, 2])


class WorkerPreparationTests(unittest.TestCase):
    def test_recipient_coverage_tracks_all_section_types_and_counts_packages_once(self):
        script = Path(__file__).resolve().parents[2] / 'scripts/recovery/refresh_key_recovery_ledger.py'
        spec = importlib.util.spec_from_file_location('key_coverage_fixture', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = {'id': 'source', 'package_model': 8, 'version': '1.0-1',
                       'artifact_status': 'preserved', 'artifact_type': 'upd'}
            atomic_json(root / 'data/catalog.json', {'packages': [package]})
            atomic_json(root / 'data/key-recovery-ledger.json', {'recovered_keys': {}, 'key_origin': {'local_status_current': 'old'}})
            atomic_json(root / 'data/upd/source.json', {'sections': [
                {'section_type': 17, 'encrypted': True, 'recipient_id': 'a' * 40},
                {'section_type': 23, 'encrypted': True, 'recipient_id': 'b' * 40},
                {'section_type': 99, 'encrypted': True}]})
            with patch.object(module, 'ROOT', root):
                self.assertEqual(module.main(), 0)
            ledger = json.loads((root / 'data/key-recovery-ledger.json').read_text())
            self.assertEqual(ledger['summary']['encrypted_upd_packages'], 1)
            self.assertEqual(ledger['summary']['unidentified_encrypted_packages'], 1)
            self.assertEqual(len(ledger['missing_keys']), 2)
            self.assertEqual(ledger['plaintext_packages'], [])
            self.assertNotIn('local_status_current', ledger['key_origin'])

    def test_worker_recovers_observed_missing_recipient_and_promotes_only_verified_metadata(self):
        script = Path(__file__).resolve().parents[2] / 'scripts/recovery/prepare_model_keys.py'
        spec = importlib.util.spec_from_file_location('key_worker_fixture', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        fingerprint = key_recipient_id(key)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            atomic_json(root / 'catalog.json', {'packages': [], 'raw_images': []})
            atomic_json(root / 'ledger.json', {'recovered_keys': {}, 'missing_keys': [
                {'recipient_id': fingerprint, 'packages': [{'package_model': 8, 'package_id': 'source'}]},
                {'recipient_id': '0' * 40, 'packages': [{'package_model': 9, 'package_id': 'other'}]}]})
            def recover(root, catalog, model, recipient, output, repo, **kwargs):
                if model == 9:
                    raise ValueError('unavailable fixture')
                write_private_key_exclusive(output, key)
                return {'method': 'legacy-updater', 'model': model, 'recipient_id': recipient}
            argv = [str(script), '--directory', str(root / 'keys'), '--report', str(root / 'report.json'),
                    '--repository', 'owner/repo', '--catalog', str(root / 'catalog.json'), '--ledger', str(root / 'ledger.json')]
            with patch.object(sys, 'argv', argv), patch.object(module, 'recover_catalog_key', side_effect=recover), \
                 patch.dict(os.environ, {'SONOS_PRIVATE_KEYS': '', 'SONOS_MODEL_8_PRIVATE_KEY': '', 'SONOS_MODEL_9_PRIVATE_KEY': ''}):
                self.assertEqual(module.main(), None)
            ledger = json.loads((root / 'ledger.json').read_text())
            self.assertEqual(set(ledger['recovered_keys']), {fingerprint})
            self.assertEqual([row['recipient_id'] for row in ledger['missing_keys']], ['0' * 40])
            self.assertNotIn('PRIVATE KEY', (root / 'report.json').read_text() + (root / 'ledger.json').read_text())
