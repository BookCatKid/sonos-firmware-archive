"""Regression check for the published Sonos One / Tupelo field-and-OTP pair."""

from __future__ import annotations

import hashlib
import struct
import unittest
from pathlib import Path

from sonos_firmware.extract import key_recipient_id
from sonos_firmware.mdp import recover_amlogic_model_key_field

ROOT = Path(__file__).resolve().parents[1]
INPUT_DIR = ROOT / "data" / "key-material" / "model26"
FIELD = INPUT_DIR / "mdp3-model-key.bin"
OTP = INPUT_DIR / "otp.bin"
FIELD_SHA256 = "140843cf0a950ab3891498fecb7c658dd996ded54bafeefdfc886b8b39956b5c"
OTP_SHA256 = "5c218954d7ee17fb84d75c937e6fae1f4c7b0ae434f2185992786c8c08208e5b"
RECIPIENT = "b271039ff7ab0f3d395039f1f079ff71af9dea71"


class PublishedModel26InputTests(unittest.TestCase):
    def test_published_inputs_recover_matching_recipient(self):
        self.assertTrue(FIELD.is_file(), "published MDP3 field must be tracked")
        self.assertTrue(OTP.is_file(), "published OTP must be tracked")
        field = FIELD.read_bytes()
        otp = OTP.read_bytes()
        self.assertEqual(len(field), 0x800)
        self.assertEqual(len(otp), 0x100)
        self.assertEqual(hashlib.sha256(field).hexdigest(), FIELD_SHA256)
        self.assertEqual(hashlib.sha256(otp).hexdigest(), OTP_SHA256)
        magic, size, _ = struct.unpack_from("<III", field)
        self.assertEqual(magic, 0xF4CCC68D)
        self.assertEqual(size, 0x4CC)
        self.assertFalse(any(field[12 + size :]))
        key = recover_amlogic_model_key_field(field, otp)
        self.assertEqual(key_recipient_id(key), RECIPIENT)


if __name__ == "__main__":
    unittest.main()
