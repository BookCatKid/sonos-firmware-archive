from __future__ import annotations

import importlib.util
import struct
import zlib
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/extract_cramfs_little.py"
SPEC = importlib.util.spec_from_file_location("extract_cramfs_little", SCRIPT)
assert SPEC and SPEC.loader
cramfs = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(cramfs)


def _inode(mode: int, size: int, name_bytes: int, offset: int) -> bytes:
    return struct.pack(
        "<III",
        mode,
        size,
        (offset // 4) << 6 | name_bytes // 4,
    )


def test_find_and_read_file_from_standard_root_inode() -> None:
    payload = b"synthetic model-6 updater\n"
    compressed = zlib.compress(payload)
    filesystem = bytearray(0xA4 + len(compressed))
    filesystem[:4] = bytes.fromhex("453dcd28")

    # Root inode -> one `bin` directory entry at 0x60.
    filesystem[0x40:0x4C] = _inode(0o40755, 16, 0, 0x60)
    filesystem[0x60:0x6C] = _inode(0o40755, 20, 4, 0x80)
    filesystem[0x6C:0x70] = b"bin\0"

    # `bin` -> one `upgrade` file entry; its one-block table starts at 0xA0.
    filesystem[0x80:0x8C] = _inode(0o100755, len(payload), 8, 0xA0)
    filesystem[0x8C:0x94] = b"upgrade\0"
    filesystem[0xA0:0xA4] = struct.pack("<I", 0xA4 + len(compressed))
    filesystem[0xA4:] = compressed

    inode = cramfs.find_inode(bytes(filesystem), "/bin/upgrade")
    assert cramfs.read_file(bytes(filesystem), inode) == payload
