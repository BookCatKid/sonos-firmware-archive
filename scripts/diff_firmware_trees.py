#!/usr/bin/env python3
"""Produce an exhaustive, reproducible file-level diff of extracted firmware trees.

Usage: python3 scripts/diff_firmware_trees.py OLD NEW > inventory.json
Only reads input trees; it does not interpret binary changes as functional changes.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path


def inventory(root: Path) -> dict:
    entries = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            relative = str(path.relative_to(root))
            if path.is_symlink():
                entries[relative] = {"kind": "symlink", "target": os.readlink(path)}
            elif path.is_file():
                digest = hashlib.sha256()
                with path.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                entries[relative] = {
                    "kind": "file", "bytes": path.stat().st_size,
                    "sha256": digest.hexdigest(),
                }
            elif path.is_dir():
                entries[relative] = {"kind": "directory"}
    return entries


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("old", type=Path)
    parser.add_argument("new", type=Path)
    parser.add_argument("--old-label", help="stable name to place in the JSON instead of the input path")
    parser.add_argument("--new-label", help="stable name to place in the JSON instead of the input path")
    args = parser.parse_args()
    for root in (args.old, args.new):
        if not root.is_dir():
            parser.error(f"not a directory: {root}")
    old, new = inventory(args.old), inventory(args.new)
    paths = sorted(old.keys() | new.keys())
    result = {
        "old": args.old_label or str(args.old), "new": args.new_label or str(args.new),
        "counts": {
            "old": len(old), "new": len(new),
            "added": sum(p not in old for p in paths),
            "removed": sum(p not in new for p in paths),
            "changed": sum(p in old and p in new and old[p] != new[p] for p in paths),
            "identical": sum(p in old and p in new and old[p] == new[p] for p in paths),
        },
        "entries": [
            {"path": p, "status": "added" if p not in old else "removed" if p not in new
             else "identical" if old[p] == new[p] else "changed",
             "old": old.get(p), "new": new.get(p)} for p in paths
        ],
    }
    json.dump(result, __import__("sys").stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
