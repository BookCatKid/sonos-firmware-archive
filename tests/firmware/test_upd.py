import struct
import unittest

from sonos_firmware.upd import ENVELOPE_MAGIC, UPD_MAGIC, parse_bytes


def section(section_type, flags, payload):
    return UPD_MAGIC + struct.pack("<III", section_type, 16 + len(payload), flags) + payload


class UpdTests(unittest.TestCase):
    def test_plain_section(self):
        parsed = parse_bytes(section(6, 0, b"kernel"))
        self.assertEqual(parsed[0].name, "kernel")
        self.assertFalse(parsed[0].encrypted)

    def test_envelope_recipient(self):
        recipient = bytes.fromhex("1234abcd")
        payload = ENVELOPE_MAGIC + b"\0" * 16 + bytes([len(recipient), 3]) + recipient
        parsed = parse_bytes(section(4, 4, payload))
        self.assertTrue(parsed[0].encrypted)
        self.assertEqual(parsed[0].recipient_id, recipient.hex())

    def test_rejects_truncated_section(self):
        with self.assertRaises(ValueError):
            parse_bytes(UPD_MAGIC + struct.pack("<III", 4, 100, 0))


if __name__ == "__main__":
    unittest.main()

