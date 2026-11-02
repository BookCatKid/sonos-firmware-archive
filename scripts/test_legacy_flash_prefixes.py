#!/usr/bin/env python3
"""Test candidate boot/flash files against a legacy flash-derived key wrapper.

The model-5 updater hashes exactly 16 KiB from the start of ``/dev/mtd/0``.
This helper recursively checks the first and last 16 KiB of supplied files,
or every 16-KiB-aligned block when ``--all-aligned`` is requested. It
deduplicates identical blocks and reports only successful key recipients. It
never serializes private keys or recovery secrets.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.extract import key_recipient_id  # noqa: E402
from sonos_firmware.legacy import recover_legacy_flash_updater_key  # noqa: E402

PREFIX_BYTES = 0x4000


def candidate_files(paths: list[Path]):
    """Yield unique files of at least 16 KiB in stable path order."""
    seen: set[Path] = set()
    for supplied in paths:
        resolved = supplied.resolve()
        if resolved.is_file():
            candidates = [resolved]
        elif resolved.is_dir():
            candidates = sorted(path for path in resolved.rglob("*") if path.is_file())
        else:
            raise ValueError(f"candidate path does not exist: {supplied}")
        for path in candidates:
            if path in seen or path.stat().st_size < PREFIX_BYTES:
                continue
            seen.add(path)
            yield path


def file_prefixes(path: Path, *, all_aligned: bool = False):
    """Yield candidate 16-KiB blocks from a file."""
    size = path.stat().st_size
    with path.open("rb") as handle:
        if all_aligned:
            for offset in range(0, size - PREFIX_BYTES + 1, PREFIX_BYTES):
                handle.seek(offset)
                yield f"offset-0x{offset:x}", handle.read(PREFIX_BYTES)
            return
        first = handle.read(PREFIX_BYTES)
        yield "first", first
        if size > PREFIX_BYTES:
            handle.seek(-PREFIX_BYTES, 2)
            last = handle.read(PREFIX_BYTES)
            if last != first:
                yield "last", last


def parse_int(value: str) -> int:
    return int(value, 0)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("updater", type=Path, help="plaintext legacy updater ELF")
    parser.add_argument("candidates", nargs="+", type=Path, help="files or directories to scan")
    parser.add_argument("--model", type=parse_int, default=5)
    parser.add_argument("--wrapper-offset", type=parse_int)
    parser.add_argument("--byte-order", choices=("big", "little"), default="big")
    parser.add_argument(
        "--all-aligned",
        action="store_true",
        help="test every 16-KiB-aligned block instead of only the first and last",
    )
    parser.add_argument("--json", action="store_true", help="emit a machine-readable receipt")
    args = parser.parse_args()

    updater = args.updater.read_bytes()
    files_tested = 0
    prefixes_tested = 0
    unique_prefixes: set[str] = set()
    matches = []
    try:
        files = candidate_files(args.candidates)
        for path in files:
            files_tested += 1
            for location, prefix in file_prefixes(path, all_aligned=args.all_aligned):
                prefixes_tested += 1
                digest = hashlib.sha256(prefix).hexdigest()
                if digest in unique_prefixes:
                    continue
                unique_prefixes.add(digest)
                try:
                    recovery = recover_legacy_flash_updater_key(
                        updater,
                        prefix,
                        args.model,
                        wrapper_offset=args.wrapper_offset,
                        byte_order=args.byte_order,
                    )
                except ValueError:
                    continue
                matches.append(
                    {
                        "path": str(path),
                        "location": location,
                        "prefix_sha256": digest,
                        "recipient_id": key_recipient_id(recovery.key),
                    }
                )
    except ValueError as error:
        parser.error(str(error))

    receipt = {
        "updater": str(args.updater.resolve()),
        "updater_sha256": hashlib.sha256(updater).hexdigest(),
        "model": args.model,
        "wrapper_offset": args.wrapper_offset,
        "byte_order": args.byte_order,
        "all_aligned": args.all_aligned,
        "files_tested": files_tested,
        "prefix_positions_tested": prefixes_tested,
        "unique_prefixes_tested": len(unique_prefixes),
        "matches": matches,
    }
    if args.json:
        print(json.dumps(receipt, indent=2))
    else:
        for match in matches:
            print(
                f"match: {match['path']} ({match['location']}, "
                f"sha256={match['prefix_sha256']}, recipient={match['recipient_id']})"
            )
        print(
            f"tested {files_tested} files, {prefixes_tested} prefix positions, "
            f"{len(unique_prefixes)} unique prefixes; matches: {len(matches)}"
        )
    return 0 if matches else 1


if __name__ == "__main__":
    raise SystemExit(main())
