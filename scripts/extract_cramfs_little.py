#!/usr/bin/env python3
"""Extract one path from the little- or big-endian CRAMFS used by Sonos UPDs."""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from sonos_firmware.cramfs import (
    ROOT_INODE_OFFSET, PADDED_SUPERBLOCK_OFFSET, CRAMFS_MAGICS,
    inode_at, entries, find_inode, read_file, detect_filesystem,
)


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
