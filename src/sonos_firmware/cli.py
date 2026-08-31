from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .catalog import filesystem_manifest, validate_catalog
from .upd import parse_file


def _inspect(path: Path) -> int:
    data = path.read_bytes()
    result = {
        "file": path.name,
        "bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "sections": [section.to_dict() for section in parse_file(path)],
    }
    print(json.dumps(result, indent=2))
    return 0


def _verify(root: Path) -> int:
    errors = validate_catalog(root)
    if errors:
        for error in errors:
            print(f"error: {error}")
        return 1
    print("catalog: ok")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(prog="sonos-fw")
    sub = parser.add_subparsers(dest="command", required=True)
    inspect = sub.add_parser("inspect", help="inspect a Sonos UPD container")
    inspect.add_argument("file", type=Path)
    verify = sub.add_parser("verify", help="validate the archive catalog")
    verify.add_argument("--root", type=Path, default=Path.cwd())
    manifest = sub.add_parser("fs-manifest", help="inventory an extracted rootfs")
    manifest.add_argument("directory", type=Path)
    manifest.add_argument("--output", type=Path)
    args = parser.parse_args()

    if args.command == "inspect":
        return _inspect(args.file)
    if args.command == "verify":
        return _verify(args.root)
    result = filesystem_manifest(args.directory)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

