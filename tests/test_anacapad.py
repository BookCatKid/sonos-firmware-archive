import hashlib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from sonos_firmware.extract import extract_anacapad
from sonos_firmware.upd import parse_bytes
from test_upd import section


def tar_payload(members):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name, data, kind in members:
            member = tarfile.TarInfo(name)
            member.type = kind
            member.size = len(data) if kind == tarfile.REGTYPE else 0
            if kind in {tarfile.SYMTYPE, tarfile.LNKTYPE}:
                member.linkname = "../../outside"
            archive.addfile(member, io.BytesIO(data))
    return output.getvalue()


class AnacapadTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.package = self.root / "connectx.upd"
        self.output = self.root / "raw"
        self.binary = b"\x7fELF\x01\x02" + b"PowerPC test fixture"
        self.tar = tar_payload([("bin/anacapad", self.binary, tarfile.REGTYPE)])

    def test_finds_typed_section_after_variable_headers_ignores_encrypted_os(self):
        data = section(21, 0, b"variable metadata") + section(23, 0, self.tar)
        data += section(4, 4, b"encrypted rootfs")
        self.package.write_bytes(data)
        record = extract_anacapad(self.package, self.output)
        self.assertEqual((self.output / record["filename"]).read_bytes(), self.binary)
        self.assertEqual(record["section_index"], 1)
        self.assertEqual(record["archive_sha256"], hashlib.sha256(self.tar).hexdigest())
        self.assertEqual(record["source_sha256"], hashlib.sha256(data).hexdigest())
        self.assertEqual(record["sha256"], hashlib.sha256(self.binary).hexdigest())
        self.assertEqual(record["bytes"], len(self.binary))
        self.assertFalse(record["source_encrypted"])
        self.assertEqual(parse_bytes(data)[1].name, "application-archive")

    def test_only_reads_named_member_without_unpacking_other_paths(self):
        payload = tar_payload([
            ("../../outside", b"unrelated", tarfile.REGTYPE),
            ("/absolute", b"unrelated", tarfile.REGTYPE),
            ("./bin/anacapad", self.binary, tarfile.REGTYPE),
        ])
        self.package.write_bytes(section(23, 0, payload))
        record = extract_anacapad(self.package, self.output)
        self.assertEqual([p.name for p in self.output.iterdir()], [record["filename"]])
        self.assertEqual((self.output / record["filename"]).read_bytes(), self.binary)

    def test_rejects_symlink_and_hardlink_members(self):
        for kind in (tarfile.SYMTYPE, tarfile.LNKTYPE):
            with self.subTest(kind=kind):
                payload = tar_payload([("bin/anacapad", b"", kind)])
                self.package.write_bytes(section(23, 0, payload))
                with self.assertRaisesRegex(ValueError, "regular tar member"):
                    extract_anacapad(self.package, self.output)
                self.assertFalse(self.output.exists())

    def test_rejects_ambiguous_members_within_or_across_sections(self):
        duplicate_tar = tar_payload([("bin/anacapad", self.binary, tarfile.REGTYPE)] * 2)
        for data in (section(23, 0, duplicate_tar), section(23, 0, self.tar) * 2):
            with self.subTest(length=len(data)):
                self.package.write_bytes(data)
                with self.assertRaisesRegex(ValueError, "found 2"):
                    extract_anacapad(self.package, self.output)
                self.assertFalse(self.output.exists())

    def test_does_not_search_wrong_section_types_or_encrypted_archives(self):
        for data in (section(17, 0, self.tar), section(23, 4, self.tar)):
            self.package.write_bytes(data)
            with self.assertRaisesRegex(ValueError, "found 0"):
                extract_anacapad(self.package, self.output)
        self.assertFalse(self.output.exists())

    def test_rejects_missing_member_and_malformed_tar(self):
        self.package.write_bytes(section(23, 0, tar_payload([])))
        with self.assertRaisesRegex(ValueError, "found 0"):
            extract_anacapad(self.package, self.output)
        self.package.write_bytes(section(23, 0, b"not a tar"))
        with self.assertRaises(tarfile.ReadError):
            extract_anacapad(self.package, self.output)
        self.assertFalse(self.output.exists())

    def test_cli_writes_receipt_and_binary(self):
        self.package.write_bytes(section(23, 0, self.tar) + section(3, 4, b"encrypted"))
        receipt = self.root / "receipt.json"
        subprocess.run([
            sys.executable, "-m", "sonos_firmware.cli", "extract-anacapad",
            str(self.package), "--directory", str(self.output), "--receipt", str(receipt),
        ], check=True, capture_output=True, text=True)
        result = json.loads(receipt.read_text())
        self.assertEqual(result["source"], "connectx.upd")
        self.assertEqual(len(result["components"]), 1)
        record = result["components"][0]
        self.assertEqual((self.output / record["filename"]).read_bytes(), self.binary)


if __name__ == "__main__":
    unittest.main()
