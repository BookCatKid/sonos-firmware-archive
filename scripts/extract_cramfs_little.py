#!/usr/bin/env python3
"""Extract one path from the little- or big-endian CRAMFS used by Sonos UPDs."""

import argparse
import math
import struct
import zlib
from pathlib import Path

ROOT_INODE_OFFSET = 0x40
PADDED_SUPERBLOCK_OFFSET = 0x200
CRAMFS_MAGICS = {
    bytes.fromhex("453dcd28"): "little",
    bytes.fromhex("28cd3d45"): "big",
}


def inode_at(fs, offset, byte_order="little"):
    prefix = "<" if byte_order == "little" else ">"
    w1, w2, w3 = struct.unpack_from(f"{prefix}III", fs, offset)
    if byte_order == "little":
        return {
            "mode": w1 & 0xFFFF,
            "uid": w1 >> 16,
            "size": w2 & 0x00FFFFFF,
            "gid": w2 >> 24,
            "namelen": (w3 & 0x3F) * 4,
            "offset": (w3 >> 6) * 4,
        }
    return {
        "mode": w1 >> 16,
        "uid": w1 & 0xFFFF,
        "size": w2 >> 8,
        "gid": w2 & 0xFF,
        "namelen": (w3 >> 26) * 4,
        "offset": (w3 & 0x03FFFFFF) * 4,
    }


def entries(fs, inode, byte_order="little"):
    pos = inode["offset"]
    end = pos + inode["size"]
    while pos < end:
        child = inode_at(fs, pos, byte_order)
        raw_name = fs[pos + 12 : pos + 12 + child["namelen"]]
        name = raw_name.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
        yield name, child
        pos += 12 + child["namelen"]


def find_inode(fs, path, byte_order="little", root_inode_offset=ROOT_INODE_OFFSET):
    current = inode_at(fs, root_inode_offset, byte_order)
    for part in [part for part in path.split("/") if part]:
        for name, child in entries(fs, current, byte_order):
            if name == part:
                current = child
                break
        else:
            raise FileNotFoundError(path)
    return current


def read_file(fs, inode, byte_order="little"):
    prefix = "<" if byte_order == "little" else ">"
    blocks = math.ceil(inode["size"] / 4096)
    table = inode["offset"]
    previous = table + blocks * 4
    output = bytearray()
    for index in range(blocks):
        end = struct.unpack_from(f"{prefix}I", fs, table + index * 4)[0]
        if end & 0xC0000000:
            raise ValueError(f"unsupported CRAMFS block flags: 0x{end:08x}")
        compressed = fs[previous:end]
        if compressed:
            output.extend(zlib.decompress(compressed))
        else:
            output.extend(b"\0" * 4096)
        previous = end
    return bytes(output[: inode["size"]])


def detect_filesystem(fs):
    for superblock_offset in (0, PADDED_SUPERBLOCK_OFFSET):
        detected = CRAMFS_MAGICS.get(fs[superblock_offset : superblock_offset + 4])
        if detected is not None:
            return detected, superblock_offset
    raise ValueError("not CRAMFS")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rootfs")
    parser.add_argument("path")
    parser.add_argument("output")
    parser.add_argument("--byte-order", choices=("auto", "little", "big"), default="auto")
    args = parser.parse_args()
    filesystem = Path(args.rootfs).read_bytes()
    detected, superblock_offset = detect_filesystem(filesystem)
    byte_order = detected if args.byte_order == "auto" else args.byte_order
    if byte_order != detected:
        raise ValueError(f"CRAMFS is {detected}-endian, not {byte_order}-endian")
    inode = find_inode(
        filesystem,
        args.path,
        byte_order,
        superblock_offset + ROOT_INODE_OFFSET,
    )
    Path(args.output).write_bytes(read_file(filesystem, inode, byte_order))


if __name__ == "__main__":
    main()
