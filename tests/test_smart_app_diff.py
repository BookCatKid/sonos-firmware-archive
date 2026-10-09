import importlib.util
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "smart_diff_app_bundles.py"
SPEC = importlib.util.spec_from_file_location("smart_diff_app_bundles", SCRIPT)
SMART = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(SMART)


def make_resource_db(path: Path, *, hello: str, extra_json: bool = False) -> None:
    conn = sqlite3.connect(path)
    try:
        conn.execute("pragma page_size=4096")
        conn.execute(
            """create table strings(
                string_index integer,
                string_name text,
                string_language text,
                string_gender text,
                string_plurality text,
                string_value text
            )"""
        )
        conn.execute(
            """create table images(
                image_index integer,
                image_scale_factor text,
                image_name text,
                image_type text,
                image_blob blob
            )"""
        )
        conn.execute(
            """create table jsons(
                json_name text,
                json_language text,
                json_content text
            )"""
        )
        conn.execute(
            "insert into strings values (?,?,?,?,?,?)",
            (1, "HELLO", "en-US", "", "", hello),
        )
        conn.execute(
            "insert into strings values (?,?,?,?,?,?)",
            (1, "HELLO", "fr-FR", "", "", "Bonjour"),
        )
        conn.execute(
            "insert into images values (?,?,?,?,?)",
            (1, "1x", "speaker", "png", b"fake-image"),
        )
        conn.execute(
            "insert into jsons values (?,?,?)",
            ("config", "en-US", '{"enabled":true,"count":1}'),
        )
        if extra_json:
            conn.execute(
                "insert into jsons values (?,?,?)",
                ("new-config", "en-US", '{"value":"new"}'),
            )
        conn.commit()
        conn.execute("vacuum")
    finally:
        conn.close()


def encrypt_resource_db(sqlite_path: Path, output_path: Path, *, iv: bytes) -> None:
    data = sqlite_path.read_bytes()
    assert len(data) % SMART.SCLIB_PAGE_SIZE == 0
    output = bytearray(len(data))
    output[:16] = iv

    def encrypt(block: bytes) -> bytes:
        enc = Cipher(
            algorithms.AES(SMART.SCLIB_KEY),
            modes.CBC(iv),
        ).encryptor()
        return enc.update(block) + enc.finalize()

    output[16:SMART.SCLIB_PAGE_SIZE] = encrypt(
        data[16:SMART.SCLIB_PAGE_SIZE]
    )
    for off in range(SMART.SCLIB_PAGE_SIZE, len(data), SMART.SCLIB_PAGE_SIZE):
        output[off:off + SMART.SCLIB_PAGE_SIZE] = encrypt(
            data[off:off + SMART.SCLIB_PAGE_SIZE]
        )
    output_path.write_bytes(output)


class SmartApplicationDiffTests(unittest.TestCase):
    def test_encrypted_libutils_is_detected_by_content_and_semantically_diffed(self):
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            old_sqlite = tmp / "old.sqlite"
            new_sqlite = tmp / "new.sqlite"
            old_blob = tmp / "old" / "libutils.so"
            new_blob = tmp / "new" / "libutils.so"
            old_blob.parent.mkdir()
            new_blob.parent.mkdir()

            make_resource_db(old_sqlite, hello="Hello")
            make_resource_db(new_sqlite, hello="Hello there", extra_json=True)
            encrypt_resource_db(
                old_sqlite,
                old_blob,
                iv=bytes.fromhex("00112233445566778899aabbccddeeff"),
            )
            encrypt_resource_db(
                new_sqlite,
                new_blob,
                iv=bytes.fromhex("ffeeddccbbaa99887766554433221100"),
            )

            old_kind, _ = SMART.detect_type(old_blob)
            new_kind, _ = SMART.detect_type(new_blob)
            self.assertEqual("sonos-resource-db", old_kind)
            self.assertEqual("sonos-resource-db", new_kind)
            inventory = SMART.inventory(old_blob.parent)
            self.assertEqual("packed-resources", inventory["libutils.so"]["role"])

            result = SMART.sonos_resource_db_diff(old_blob, new_blob)
            self.assertEqual("sonos-sclib-resource-db", result["type"])
            self.assertEqual(1, result["strings"]["counts"]["changed"])
            self.assertEqual(1, result["strings"]["counts"]["same"])
            self.assertEqual(1, result["jsons"]["counts"]["added"])
            self.assertEqual(1, result["jsons"]["counts"]["same"])
            self.assertEqual(1, result["images"]["counts"]["same"])

            change = result["strings"]["changes"][0]
            self.assertEqual("HELLO", change["key"]["name"])
            self.assertEqual("en-US", change["key"]["language"])
            self.assertEqual("Hello", change["old"])
            self.assertEqual("Hello there", change["new"])

    def test_encrypted_resource_detection_is_filename_independent(self):
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            sqlite_path = tmp / "resource.sqlite"
            encrypted = tmp / "totally-misleading.dat"
            make_resource_db(sqlite_path, hello="Hello")
            encrypt_resource_db(
                sqlite_path,
                encrypted,
                iv=bytes.fromhex("102132435465768798a9bacbdcedfe0f"),
            )
            kind, metadata = SMART.detect_type(encrypted)
            self.assertEqual("sonos-resource-db", kind)
            self.assertTrue(metadata["encrypted"])

            random_page = tmp / "random.bin"
            random_page.write_bytes(b"\x00" * SMART.SCLIB_PAGE_SIZE)
            random_kind, _ = SMART.detect_type(random_page)
            self.assertNotEqual("sonos-resource-db", random_kind)

    def test_generic_sqlite_diff_uses_primary_key_rows(self):
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            old = tmp / "old.sqlite"
            new = tmp / "new.sqlite"
            for path, rows in (
                (old, [("a", "1"), ("b", "2")]),
                (new, [("a", "1"), ("b", "3"), ("c", "4")]),
            ):
                conn = sqlite3.connect(path)
                try:
                    conn.execute("create table settings(key text primary key, value text)")
                    conn.executemany("insert into settings values (?,?)", rows)
                    conn.commit()
                finally:
                    conn.close()

            diff = SMART.sqlite_diff(old, new)
            table = next(row for row in diff["tables"] if row["table"] == "settings")
            self.assertEqual("changed", table["status"])
            self.assertEqual(["key"], table["primary_key"])
            self.assertEqual(1, table["rows"]["counts"]["same"])
            self.assertEqual(1, table["rows"]["counts"]["changed"])
            self.assertEqual(1, table["rows"]["counts"]["added"])

    def test_zip_diff_compares_members(self):
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            old = tmp / "old.zip"
            new = tmp / "new.zip"
            with zipfile.ZipFile(old, "w") as archive:
                archive.writestr("same.txt", "same")
                archive.writestr("changed.txt", "old")
                archive.writestr("removed.txt", "gone")
            with zipfile.ZipFile(new, "w") as archive:
                archive.writestr("same.txt", "same")
                archive.writestr("changed.txt", "new")
                archive.writestr("added.txt", "hello")

            diff = SMART.zip_diff(old, new)
            self.assertEqual(1, diff["counts"]["same"])
            self.assertEqual(1, diff["counts"]["changed"])
            self.assertEqual(1, diff["counts"]["added"])
            self.assertEqual(1, diff["counts"]["removed"])

    def test_utf16_localizable_strings_are_key_diffed(self):
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            old = tmp / "old.strings"
            new = tmp / "new.strings"
            old.write_text(
                '"HELLO" = "Hello";\n"BYE" = "Goodbye";\n',
                encoding="utf-16",
            )
            new.write_text(
                '"HELLO" = "Hello there";\n"NEW" = "New text";\n',
                encoding="utf-16",
            )

            old_kind, old_meta = SMART.detect_type(old)
            new_kind, _ = SMART.detect_type(new)
            self.assertEqual("strings-table", old_kind)
            self.assertEqual("strings-table", new_kind)
            self.assertEqual(2, old_meta["entries"])

            diff = SMART.strings_table_diff(old, new)
            self.assertEqual(1, diff["counts"]["added"])
            self.assertEqual(1, diff["counts"]["removed"])
            self.assertEqual(1, diff["counts"]["changed"])
            changes = {row["path"]: row for row in diff["changes"]}
            self.assertEqual("Hello", changes["HELLO"]["old"])
            self.assertEqual("Hello there", changes["HELLO"]["new"])

    def test_path_role_detection_handles_code_localization_and_compiled_ui(self):
        self.assertEqual(
            "native-code",
            SMART.role_for_path("Contents/MacOS/Sonos"),
        )
        self.assertEqual(
            "localization",
            SMART.role_for_path("Contents/Resources/fr.lproj/Localizable.strings"),
        )
        self.assertEqual(
            "compiled-ui",
            SMART.role_for_path(
                "Contents/Resources/Base.lproj/Controller.nib/keyedobjects-101300.nib"
            ),
        )

    def test_nibarchive_magic_is_recognized_and_semantically_diffed(self):
        with tempfile.TemporaryDirectory() as tmp_raw:
            tmp = Path(tmp_raw)
            old = tmp / "old.nib"
            new = tmp / "new.nib"
            old.write_bytes(
                b"NIBArchive" + b"\x00" * 16
                + b"SMOldController\x00NSButton\x00titleKey\x00Old label\x00"
            )
            new.write_bytes(
                b"NIBArchive" + b"\x00" * 16
                + b"SMNewController\x00NSButton\x00titleKey\x00New label\x00"
            )
            kind, metadata = SMART.detect_type(old)
            self.assertEqual("nibarchive", kind)
            self.assertEqual("Apple NIBArchive compiled UI", metadata["description"])

            diff = SMART.nibarchive_diff(old, new)
            self.assertEqual("nibarchive", diff["type"])
            self.assertGreaterEqual(diff["tokens"]["counts"]["added"], 1)
            self.assertGreaterEqual(diff["tokens"]["counts"]["removed"], 1)
            self.assertEqual(1, diff["classes"]["counts"]["added"])
            self.assertEqual(1, diff["classes"]["counts"]["removed"])


if __name__ == "__main__":
    unittest.main()
