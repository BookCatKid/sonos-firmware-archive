import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from sonos_firmware.extract import key_recipient_id
from sonos_firmware.keyring import prepare_keys


class KeyringTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.recipient = key_recipient_id(cls.key)
        cls.pem = cls.key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                       serialization.NoEncryption()).decode()

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


if __name__ == '__main__':
    unittest.main()
