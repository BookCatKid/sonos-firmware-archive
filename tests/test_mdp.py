import hashlib
import os
import struct
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from sonos_firmware.extract import key_recipient_id
from sonos_firmware.mdp import (
    MDP1_MAGIC,
    MDP3_BYTES,
    MDP3_MAGIC,
    MDP3_OFFSET_IN_SMDP,
    MODEL_KEY_MAGIC,
    MODEL_KEY_OFFSET,
    SMDP_BYTES,
    inspect_mdp,
    recipient_id_for_key_file,
    recover_amlogic_model_key,
    write_private_key_exclusive,
)


def _xor_repeating(left: bytes, right: bytes) -> bytes:
    return bytes(value ^ right[index % len(right)] for index, value in enumerate(left))


def wrap_amlogic_blob(plaintext: bytes, otp: bytes, key_modifier: bytes = b"model") -> bytes:
    padding_length = 16 - len(plaintext) % 16
    padded = plaintext + bytes([padding_length]) * padding_length
    iv = bytes(range(12))
    counter_iv = _xor_repeating(iv, key_modifier.ljust(8, b"\x00"))
    counters = b"".join(
        counter_iv + struct.pack(">I", 2 + index)
        for index in range(len(padded) // 16)
    )
    aes_key = hashlib.sha256(otp[0xD0:0xE0]).digest()[:16]
    encryptor = Cipher(algorithms.AES(aes_key), modes.ECB()).encryptor()
    stream = encryptor.update(counters) + encryptor.finalize()
    ciphertext = bytes(a ^ b for a, b in zip(padded, stream, strict=True))
    return ciphertext + iv + bytes(16)


def mdp3_with_key(key, otp: bytes) -> bytes:
    key_der = key.private_bytes(
        serialization.Encoding.DER,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    )
    blob = wrap_amlogic_blob(key_der, otp)
    page = bytearray(MDP3_BYTES)
    page[:4] = MDP3_MAGIC
    struct.pack_into("<I", page, 4, 2)
    struct.pack_into("<III", page, MODEL_KEY_OFFSET, MODEL_KEY_MAGIC, len(blob), 0)
    page[MODEL_KEY_OFFSET + 12 : MODEL_KEY_OFFSET + 12 + len(blob)] = blob
    return bytes(page)


class MDPTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cls.otp = bytes(range(256))
        cls.mdp3 = mdp3_with_key(cls.key, cls.otp)

    def test_recovers_key_from_standalone_mdp3(self):
        recovered = recover_amlogic_model_key(self.mdp3, self.otp)
        self.assertEqual(key_recipient_id(recovered), key_recipient_id(self.key))

    def test_locates_mdp3_and_identity_in_complete_smdp(self):
        smdp = bytearray(SMDP_BYTES)
        struct.pack_into("<IIIII", smdp, 0, MDP1_MAGIC, 1, 26, 1, 7)
        smdp[MDP3_OFFSET_IN_SMDP:] = self.mdp3
        result = inspect_mdp(bytes(smdp))

        self.assertEqual(result["mdp3_offset"], MDP3_OFFSET_IN_SMDP)
        self.assertEqual((result["model"], result["submodel"], result["revision"]), (26, 1, 7))
        self.assertTrue(result["fields"][-1]["valid"])

        recovered = recover_amlogic_model_key(bytes(smdp), self.otp, expected_model=26)
        self.assertEqual(key_recipient_id(recovered), key_recipient_id(self.key))

    def test_rejects_unverifiable_or_mismatched_expected_model(self):
        with self.assertRaisesRegex(ValueError, "cannot validate the expected model"):
            recover_amlogic_model_key(self.mdp3, self.otp, expected_model=26)

        smdp = bytearray(SMDP_BYTES)
        struct.pack_into("<IIIII", smdp, 0, MDP1_MAGIC, 1, 25, 1, 7)
        smdp[MDP3_OFFSET_IN_SMDP:] = self.mdp3
        with self.assertRaisesRegex(ValueError, "model mismatch: got 25, expected 26"):
            recover_amlogic_model_key(bytes(smdp), self.otp, expected_model=26)

    def test_wrong_otp_does_not_produce_a_key(self):
        with self.assertRaises(ValueError):
            recover_amlogic_model_key(self.mdp3, bytes(256))

    def test_private_key_output_is_exclusive_and_owner_only(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "model-private.pem"
            write_private_key_exclusive(output, self.key)
            self.assertEqual(os.stat(output).st_mode & 0o777, 0o600)
            self.assertEqual(recipient_id_for_key_file(output), key_recipient_id(self.key))
            with self.assertRaises(FileExistsError):
                write_private_key_exclusive(output, self.key)


if __name__ == "__main__":
    unittest.main()
