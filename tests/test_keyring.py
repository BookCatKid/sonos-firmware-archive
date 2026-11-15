import tempfile
import json
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sonos_firmware.extract import key_recipient_id
from sonos_firmware.keyring import prepare_keys, recipient_models, recipient_key_path
from sonos_firmware.mdp import write_private_key_exclusive


class KeyringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.recipient = key_recipient_id(cls.key)
        cls.pem = cls.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()).decode()

    def test_unclassified_package_can_use_an_exact_recipient_key_without_a_model_alias(self):
        ledger = {'missing_keys': [{'recipient_id': self.recipient, 'packages': [{'package_model': None}]}]}
        recipients = recipient_models(ledger)
        self.assertEqual(recipients, {None: {self.recipient}})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = prepare_keys(directory, recipients, {'SONOS_PRIVATE_KEYS': json.dumps({self.recipient: self.pem})}, {})
            self.assertEqual(result['ready'], {})
            self.assertEqual(result['ready_recipients'][self.recipient]['models'], [])
            self.assertEqual(result['unavailable'], [])
            self.assertTrue(recipient_key_path(directory, None, self.recipient).is_file())
            self.assertFalse((directory/'modelNone.pem').exists())
            self.assertNotIn('PRIVATE KEY', json.dumps(result))

    def test_known_and_unclassified_packages_share_the_same_validated_recipient(self):
        with tempfile.TemporaryDirectory() as temporary:
            result = prepare_keys(Path(temporary), {None: {self.recipient}, 17: {self.recipient}},
                                  {'SONOS_MODEL_17_PRIVATE_KEY': self.pem}, {})
            self.assertEqual(result['ready_recipients'][self.recipient]['models'], [17])
            self.assertEqual(result['unavailable'], [])

    def test_worker_prepares_and_records_a_configured_key_without_model_context(self):
        import importlib.util
        import os
        import sys
        from unittest.mock import patch
        root = Path(__file__).resolve().parents[1]
        spec = importlib.util.spec_from_file_location('unclassified_keys', root/'scripts/prepare_model_keys.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            ledger = directory/'ledger.json'
            ledger.write_text(json.dumps({'missing_keys': [{'recipient_id': self.recipient,
                'packages': [{'package_model': None, 'package_id': 'opaque'}]}]}))
            with patch.object(sys, 'argv', ['prepare_model_keys.py', '--directory', str(directory/'keys'),
                    '--report', str(directory/'report.json'), '--catalog', str(directory/'absent-catalog.json'),
                    '--ledger', str(ledger), '--repository', 'fresh/archive']), \
                 patch.dict(os.environ, {'SONOS_PRIVATE_KEYS': json.dumps({self.recipient: self.pem})}), \
                 patch.object(module, 'recover_catalog_key') as recover:
                module.main()
            recover.assert_not_called()
            report = json.loads((directory/'report.json').read_text())
            self.assertEqual(report['ready_recipients'][self.recipient]['models'], [])
            self.assertEqual(json.loads(ledger.read_text())['key_origin']['summary']['models'], [])
            self.assertTrue(recipient_key_path(directory/'keys', None, self.recipient).is_file())

    def test_optional_recovery_failure_does_not_discard_a_configured_key(self):
        def unavailable(): raise ConnectionError('source unavailable')
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary) / 'keys'
            result = prepare_keys(directory, {8: self.recipient, 17: '0' * 40},
                                  {'SONOS_MODEL_8_PRIVATE_KEY': self.pem}, {17: unavailable})
            self.assertEqual(result['ready'], {'8': self.recipient})
            self.assertEqual(result['unavailable'][0]['model'], 17)
            self.assertTrue((directory / 'model8.pem').exists())
            self.assertEqual((directory / 'model8.pem').stat().st_mode & 0o777, 0o600)

    def test_invalid_configured_material_is_not_reported_or_written(self):
        secret = 'invalid-private-input-that-must-not-appear-in-the-report'
        with tempfile.TemporaryDirectory() as temporary:
            result = prepare_keys(Path(temporary), {8: self.recipient}, {'SONOS_MODEL_8_PRIVATE_KEY': secret}, {})
            self.assertEqual(result['ready'], {})
            self.assertNotIn(secret, repr(result))
            self.assertFalse((Path(temporary) / 'model8.pem').exists())

    def test_invalid_existing_key_is_recovered_in_the_same_attempt(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            alias = directory / 'model17.pem'
            alias.write_text('bad existing material')
            def recover():
                write_private_key_exclusive(alias, self.key)
                return alias
            result = prepare_keys(directory, {17: self.recipient}, {}, {17: recover})
            self.assertEqual(result['ready'], {'17': self.recipient})
            self.assertEqual(result['unavailable'], [])

    def test_rotation_and_missing_groups_keep_every_recipient(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        fingerprint = key_recipient_id(other)
        other_pem = other.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                        serialization.NoEncryption()).decode()
        ledger = {'recovered_keys': {self.recipient: {'package_model': 8}},
                  'missing_keys': [{'recipient_id': fingerprint, 'packages': [{'package_model': 8}]}]}
        recipients = recipient_models(ledger)
        self.assertEqual(recipients, {8: {self.recipient, fingerprint}})
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = prepare_keys(directory, recipients, {'SONOS_PRIVATE_KEYS': json.dumps({
                self.recipient: self.pem, fingerprint: other_pem})}, {})
            self.assertEqual(set(result['ready']['8']), {self.recipient, fingerprint})
            self.assertFalse((directory / 'model8.pem').exists())
            self.assertEqual(set(result['ready_recipients']), {self.recipient, fingerprint})
            for fp in (self.recipient, fingerprint):
                self.assertTrue(recipient_key_path(directory, 8, fp).is_file())
            self.assertNotIn('PRIVATE KEY', json.dumps(result))

    def test_bad_optional_recovery_is_sanitized_and_does_not_block_other_models(self):
        def fail():
            raise ValueError('private-derivation-input-do-not-log')
        with tempfile.TemporaryDirectory() as temporary:
            result = prepare_keys(Path(temporary), {8: self.recipient, 17: '0' * 40},
                {'SONOS_MODEL_8_PRIVATE_KEY': self.pem}, {(17, '0' * 40): fail})
            self.assertEqual(result['ready'], {'8': self.recipient})
            self.assertNotIn('private-derivation', json.dumps(result))

    def test_rotated_recoverers_using_one_legacy_alias_do_not_block_each_other(self):
        other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        keys = {key_recipient_id(key): key for key in (self.key, other)}
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            alias = directory / 'model8.pem'
            def recover(key):
                write_private_key_exclusive(alias, key)
                return alias
            recoverers = {(8, fp): lambda key=key: recover(key) for fp, key in keys.items()}
            result = prepare_keys(directory, {8: set(keys)}, {}, recoverers)
            self.assertEqual(set(result['ready']['8']), set(keys))
            self.assertEqual(result['unavailable'], [])
            self.assertFalse(alias.exists())

    def test_shared_recipient_is_ready_for_earlier_models_when_prepared_by_a_later_model(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            result = prepare_keys(directory, {1: self.recipient, 17: self.recipient},
                                  {'SONOS_MODEL_17_PRIVATE_KEY': self.pem}, {})
            self.assertEqual(result['ready'], {'1': self.recipient, '17': self.recipient})
            self.assertEqual(result['ready_recipients'][self.recipient]['models'], [1, 17])
            self.assertEqual(result['unavailable'], [])
            self.assertTrue((directory / 'model1.pem').is_file())
            self.assertTrue((directory / 'model17.pem').is_file())


if __name__ == '__main__':
    unittest.main()
