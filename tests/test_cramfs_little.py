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


def _inode(mode: int, size: int, name_bytes: int, offset: int, byte_order="little") -> bytes:
    if byte_order == "little":
        return struct.pack(
            "<III",
            mode,
            size,
            (offset // 4) << 6 | name_bytes // 4,
        )
    return struct.pack(
        ">III",
        mode << 16,
        size << 8,
        name_bytes // 4 << 26 | offset // 4,
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


def test_find_and_read_file_from_big_endian_filesystem() -> None:
    payload = b"synthetic model-5 utility\n"
    compressed = zlib.compress(payload)
    filesystem = bytearray(0xA4 + len(compressed))
    filesystem[:4] = bytes.fromhex("28cd3d45")

    filesystem[0x40:0x4C] = _inode(0o40755, 16, 0, 0x60, "big")
    filesystem[0x60:0x6C] = _inode(0o40755, 20, 4, 0x80, "big")
    filesystem[0x6C:0x70] = b"bin\0"
    filesystem[0x80:0x8C] = _inode(0o100755, len(payload), 8, 0xA0, "big")
    filesystem[0x8C:0x94] = b"mdputil\0"
    filesystem[0xA0:0xA4] = struct.pack(">I", 0xA4 + len(compressed))
    filesystem[0xA4:] = compressed

    inode = cramfs.find_inode(bytes(filesystem), "/bin/mdputil", "big")
    assert cramfs.read_file(bytes(filesystem), inode, "big") == payload


def test_detect_and_read_padded_big_endian_filesystem() -> None:
    payload = b"padded synthetic model-5 utility\n"
    compressed = zlib.compress(payload)
    base = cramfs.PADDED_SUPERBLOCK_OFFSET
    filesystem = bytearray(base + 0xA4 + len(compressed))
    filesystem[base : base + 4] = bytes.fromhex("28cd3d45")

    root = base + 0x40
    directory = base + 0x60
    contents = base + 0x80
    table = base + 0xA0
    data = base + 0xA4
    filesystem[root : root + 12] = _inode(0o40755, 16, 0, directory, "big")
    filesystem[directory : directory + 12] = _inode(
        0o40755, 20, 4, contents, "big"
    )
    filesystem[directory + 12 : directory + 16] = b"bin\0"
    filesystem[contents : contents + 12] = _inode(
        0o100755, len(payload), 8, table, "big"
    )
    filesystem[contents + 12 : contents + 20] = b"mdputil\0"
    filesystem[table : table + 4] = struct.pack(">I", data + len(compressed))
    filesystem[data:] = compressed

    detected, superblock_offset = cramfs.detect_filesystem(bytes(filesystem))
    assert (detected, superblock_offset) == ("big", base)
    inode = cramfs.find_inode(
        bytes(filesystem),
        "/bin/mdputil",
        detected,
        superblock_offset + cramfs.ROOT_INODE_OFFSET,
    )
    assert cramfs.read_file(bytes(filesystem), inode, detected) == payload
