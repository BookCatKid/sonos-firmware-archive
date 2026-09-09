#!/usr/bin/env python3
"""Extract one path from the little-endian CRAMFS used by Sonos model-6 UPDs."""

import argparse
import math
import struct
import zlib
from pathlib import Path

ROOT_INODE_OFFSET = 0x40


def inode_at(fs, offset):
    w1, w2, w3 = struct.unpack_from("<III", fs, offset)
    return {
        "mode": w1 & 0xFFFF,
        "uid": w1 >> 16,
        "size": w2 & 0x00FFFFFF,
        "gid": w2 >> 24,
        "namelen": (w3 & 0x3F) * 4,
        "offset": (w3 >> 6) * 4,
    }


def entries(fs, inode):
    pos = inode["offset"]
    end = pos + inode["size"]
    while pos < end:
        child = inode_at(fs, pos)
        raw_name = fs[pos + 12 : pos + 12 + child["namelen"]]
        name = raw_name.split(b"\0", 1)[0].decode("utf-8", "surrogateescape")
        yield name, child
        pos += 12 + child["namelen"]


def find_inode(fs, path):
    current = inode_at(fs, ROOT_INODE_OFFSET)
    for part in [part for part in path.split("/") if part]:
        for name, child in entries(fs, current):
            if name == part:
                current = child
                break
        else:
            raise FileNotFoundError(path)
    return current


def read_file(fs, inode):
    blocks = math.ceil(inode["size"] / 4096)
    table = inode["offset"]
    previous = table + blocks * 4
    output = bytearray()
    for index in range(blocks):
        end = struct.unpack_from("<I", fs, table + index * 4)[0]
        if end & 0xC0000000:
            raise ValueError(f"unsupported CRAMFS block flags: 0x{end:08x}")
        compressed = fs[previous:end]
        if compressed:
            output.extend(zlib.decompress(compressed))
        else:
            output.extend(b"\0" * 4096)
        previous = end
    return bytes(output[: inode["size"]])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("rootfs")
    parser.add_argument("path")
    parser.add_argument("output")
    args = parser.parse_args()
    filesystem = Path(args.rootfs).read_bytes()
    if filesystem[:4] != bytes.fromhex("453dcd28"):
        raise ValueError("not little-endian CRAMFS")
    inode = find_inode(filesystem, args.path)
    Path(args.output).write_bytes(read_file(filesystem, inode))


if __name__ == "__main__":
    main()
