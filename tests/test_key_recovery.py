import hashlib
import importlib.util
import json
import struct
import sys
import tempfile
import os
import unittest
import zlib
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives import serialization, hashes
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from sonos_firmware import key_recovery as recovery
from sonos_firmware.extract import key_recipient_id, load_private_key, extract_available_components
from sonos_firmware.mdp import write_private_key_exclusive
from sonos_firmware.legacy import (SYSTEM_WORD, UPDATER_MARKER, WRAPPER_BYTES, legacy_seed,
                                   discover_legacy_updater_key)
from sonos_firmware.storage import atomic_json
from sonos_firmware.upd import parse_bytes
from test_cramfs_little import _inode
from test_legacy import _synthetic_updater_from_seed
from test_upd import section


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
        cls.recipient = key_recipient_id(cls.key)

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

    def test_wrong_recipient_and_bounded_search_are_explicit_failures(self):
        with self.assertRaisesRegex(ValueError, 'requested recipient'):
            discover_legacy_updater_key(self.updater(17), 17, '0' * 40)
        with self.assertRaisesRegex(ValueError, 'size limit'):
            discover_legacy_updater_key(bytes(100), 17, self.recipient, max_bytes=99)
        with self.assertRaisesRegex(ValueError, 'too many'):
            discover_legacy_updater_key(UPDATER_MARKER * 65, 17, self.recipient)

    def test_multiple_marker_candidates_are_filtered_by_exact_recipient(self):
        wrong = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        first = _synthetic_updater_from_seed(legacy_seed(17), wrong)
        result = discover_legacy_updater_key(first + self.updater(17), 17, self.recipient)
        self.assertGreater(result.wrapper_offset, len(first))

    def test_full_cramfs_source_recovery_from_metadata_without_version_inventory(self):
        for order in ('big', 'little'):
            with self.subTest(order=order), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                updater = self.updater(1, order=order, word=0)
                filesystem = cramfs_image(updater, order)
                catalog, _ = source_catalog(root, 1, filesystem)
                output = root / 'keys/model1.pem'
                proof = recovery.recover_catalog_key(root, catalog, 1, self.recipient, output, 'owner/repo',
                           read_artifact=lambda source, path, repo: path.write_bytes(filesystem))
                self.assertEqual(key_recipient_id(load_private_key(output)), self.recipient)
                self.assertEqual(proof['source']['package_id'], 'arbitrary-source')
                self.assertEqual(proof['updater_sha256'], digest(updater))
                self.assertEqual(output.stat().st_mode & 0o777, 0o600)
                self.assertEqual([path.name for path in output.parent.iterdir()], ['model1.pem'])
                self.assertNotIn('PRIVATE KEY', json.dumps(proof))

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

    def test_selection_requires_matching_section_inventory_and_publication(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = source_catalog(root, 17, b'fixture')
            self.assertEqual(len(recovery.select_updater_sources(root, catalog, 17)), 2)
            catalog['raw_images'][0]['sha256'] = '0' * 64
            self.assertEqual([r['kind'] for r in recovery.select_updater_sources(root, catalog, 17)], ['upd'])
            catalog['packages'][0]['sha256'] = '0' * 64
            self.assertEqual(recovery.select_updater_sources(root, catalog, 17), [])

    def test_source_limits_and_optional_failure_do_not_expose_input_details(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            catalog, _ = source_catalog(root, 17, b'fixture')
            def fail(*args):
                raise ValueError('private-input-that-must-not-appear')
            with self.assertRaisesRegex(ValueError, '1 attempted sources; 1 sources deferred') as error:
                recovery.recover_catalog_key(root, catalog, 17, self.recipient, root / 'keys/model17.pem',
                                             'owner/repo', read_artifact=fail, max_sources=1)
            self.assertNotIn('private-input', str(error.exception))

    def test_bounded_runs_rotate_failed_sources_and_replay_success_in_a_fresh_worker(self):
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
            for _ in range(2):
                with self.assertRaises(ValueError):
                    recovery.recover_catalog_key(root, catalog, 17, self.recipient, output, 'owner/repo',
                        read_artifact=fetch, read_member=lambda path: path.read_bytes(), max_sources=1)
            recovery.recover_catalog_key(root, catalog, 17, self.recipient, output, 'owner/repo',
                read_artifact=fetch, read_member=lambda path: path.read_bytes(), max_sources=1)
            self.assertEqual(visited, ['a', 'b', 'c'])
            output.unlink()
            recovery.recover_catalog_key(root, catalog, 17, self.recipient, output, 'owner/repo',
                read_artifact=fetch, read_member=lambda path: path.read_bytes(), max_sources=1)
            self.assertEqual(visited[-1], 'c')
            self.assertNotIn('PRIVATE KEY', (root / 'data/discovery/key-recovery.json').read_text())


class MemberReaderTests(unittest.TestCase):
    def test_decoder_output_and_wall_time_are_bounded(self):
        command = [sys.executable, '-c']
        self.assertEqual(recovery.bounded_output(command + ['print("abc", end="")'], max_bytes=3), b'abc')
        with self.assertRaisesRegex(ValueError, 'size limit'):
            recovery.bounded_output(command + ['print("too large")'], max_bytes=3)
        with self.assertRaises(TimeoutError):
            recovery.bounded_output(command + ['import time; time.sleep(30)'], max_bytes=3, timeout=.1)

    def test_squashfs_member_read_is_a_literal_bounded_decoder_call(self):
        with tempfile.TemporaryDirectory() as temporary, patch.object(recovery, 'bounded_output', return_value=b'updater') as read:
            path = Path(temporary) / 'extension-does-not-matter'
            path.write_bytes(b'sqsh' + bytes(16))
            self.assertEqual(recovery.read_updater_member(path), b'updater')
            self.assertEqual(read.call_args.args[0], ['unsquashfs', '-cat', str(path), 'bin/upgrade'])
            self.assertEqual(read.call_args.kwargs['max_bytes'], recovery.MAX_UPDATER_BYTES)


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

    def test_worker_uses_ready_rotated_key_then_retries_immediately_when_the_other_arrives(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/decrypt_pending_firmware.py'
        spec = importlib.util.spec_from_file_location('recipient_worker_fixture', script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        keys = sorted(self.keys, key=key_recipient_id)
        fingerprints = [key_recipient_id(key) for key in keys]
        body = section(4, 4, envelope(b'older recipient', keys[0])) + section(6, 4, envelope(b'newer recipient', keys[1])) + section(3, 0, b'plaintext')
        for policy in ('all-sections', 'legacy'):
            with self.subTest(policy=policy), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                package = {'id': 'rotating-source', 'filename': 'advertised.upd', 'package_model': 8,
                    'artifact_status': 'preserved', 'artifact_type': 'upd', 'extraction_policy': policy,
                    'bytes': len(body), 'sha256': digest(body), 'release_tag': 'fixture-source', 'release_asset': 'exact-capture.upd'}
                atomic_json(root / 'data/catalog.json', {'packages': [package]})
                atomic_json(root / 'data/upd/rotating-source.json', {'sections': [s.to_dict() for s in parse_bytes(body)]})
                atomic_json(root / 'ledger.json', {'recovered_keys': {fp: {'package_model': 8,
                    'coverage_packages': [{'package_id': package['id'], 'artifact_status': 'preserved'}]} for fp in fingerprints}})
                (root / 'keys').mkdir()
                write_private_key_exclusive(root / 'keys' / ('recipient-' + fingerprints[1] + '.pem'), keys[1])
                (root / 'sources').mkdir()
                source = root / 'sources/rotating-source.upd'
                source.write_bytes(body)
                argv = [str(script), '--key-directory', str(root / 'keys'), '--catalog', str(root / 'data/catalog.json'),
                        '--ledger', str(root / 'ledger.json'), '--staging', str(root / 'sources'), '--raw-directory', str(root / 'raw'),
                        '--upd-manifest-directory', str(root / 'data/upd'), '--receipt-directory', str(root / 'receipts')]
                with patch.object(sys, 'argv', argv):
                    self.assertEqual(module.main(), 0)
                receipt = root / 'receipts/rotating-source.json'
                self.assertEqual({row['section_index'] for row in json.loads(receipt.read_text())['components']}, {1, 2})
                write_private_key_exclusive(root / 'keys' / ('recipient-' + fingerprints[0] + '.pem'), keys[0])
                source.write_bytes(body)
                with patch.object(sys, 'argv', argv):
                    self.assertEqual(module.main(), 0)
                self.assertEqual({row['section_index'] for row in json.loads(receipt.read_text())['components']}, {0, 1, 2})


class WorkerPreparationTests(unittest.TestCase):
    def test_recipient_coverage_tracks_all_section_types_and_counts_packages_once(self):
        script = Path(__file__).resolve().parents[1] / 'scripts/refresh_key_recovery_ledger.py'
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
        script = Path(__file__).resolve().parents[1] / 'scripts/prepare_model_keys.py'
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
