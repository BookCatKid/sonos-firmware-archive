from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .catalog import filesystem_manifest, validate_catalog
from .discovery import download_all, parse_manifest, probe_all, write_json
from .extract import extract_components
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
    discover = sub.add_parser("discover", help="probe every artifact named by a Sonos UPM")
    discover.add_argument("manifest", type=Path)
    discover.add_argument("--output", type=Path, required=True)
    discover.add_argument("--workers", type=int, default=6)
    fetch = sub.add_parser("fetch", help="download available artifacts from discovery JSON")
    fetch.add_argument("discovery", type=Path)
    fetch.add_argument("--directory", type=Path, required=True)
    fetch.add_argument("--receipt", type=Path, required=True)
    fetch.add_argument("--workers", type=int, default=3)
    extract = sub.add_parser("extract", help="extract raw firmware components from a UPD")
    extract.add_argument("file", type=Path)
    extract.add_argument("--directory", type=Path, required=True)
    extract.add_argument("--private-key", type=Path)
    extract.add_argument("--receipt", type=Path)
    args = parser.parse_args()

    if args.command == "inspect":
        return _inspect(args.file)
    if args.command == "verify":
        return _verify(args.root)
    if args.command == "discover":
        metadata, candidates = parse_manifest(args.manifest)
        results = probe_all(candidates, args.workers)
        write_json(args.output, {**metadata, "candidates": results})
        available = sum(item["available"] for item in results)
        print(f"discovered {len(results)} candidates; {available} available")
        return 0
    if args.command == "fetch":
        discovery = json.loads(args.discovery.read_text(encoding="utf-8"))
        results = download_all(discovery["candidates"], args.directory, args.workers)
        write_json(args.receipt, {"source": args.discovery.name, "artifacts": results})
        downloaded = sum(item["downloaded"] for item in results)
        print(f"downloaded {downloaded}/{len(results)} available artifacts")
        return 0 if downloaded == len(results) else 1
    if args.command == "extract":
        records = extract_components(args.file, args.directory, args.private_key)
        result = {"source": args.file.name, "components": records}
        rendered = json.dumps(result, indent=2) + "\n"
        if args.receipt:
            args.receipt.write_text(rendered, encoding="utf-8")
        else:
            print(rendered, end="")
        print(f"extracted {len(records)} components from {args.file.name}")
        return 0
    result = filesystem_manifest(args.directory)
    rendered = json.dumps(result, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
