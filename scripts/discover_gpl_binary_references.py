#!/usr/bin/env python3
"""Recover GPL index URLs referenced by preserved Sonos application binaries.

Interpret APK/IPA/ZIP central directories and scan decompressed DEX, ELF,
Mach-O, and executable/text resources without executing target code. Report
only exact versioned GPL path evidence; absence is not proof of completeness.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sonos_firmware.gpl_binary_evidence import scan_binary
from sonos_firmware.gpl_archive import atomic_json, load_json, timestamp

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "data/gpl/live/binary-evidence.json"


def update(output: Path, scans: list[dict]) -> dict:
    record = load_json(output, {"schema_version": 1, "scans": {}, "version_evidence": {}})
    if record.get("schema_version") != 1:
        raise ValueError("unsupported binary evidence manifest")
    for scan in scans:
        record["scans"][scan["sha256"]] = scan
    found = {}
    for scan in record["scans"].values():
        for version, references in scan["version_evidence"].items():
            for item in references:
                label = f"binary:{scan['filename']}:{item.get('entry') or '<raw>'}:{item['offset']}:{item['format']}"
                found.setdefault(version, set()).add(label)
    record["version_evidence"] = {version: sorted(sources) for version, sources in
                                  sorted(found.items(), key=lambda x: tuple(map(int, x[0].split('.'))))}
    record["updated"] = timestamp()
    record["limitations"] = (
        "Only visible literal paths in parsed local executable/text resources are detected; "
        "constructed, compressed-within-entry, obfuscated and encrypted URLs require deeper binary reversal."
    )
    atomic_json(output, record)
    return record


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", action="append", type=Path, required=True, help="APK, IPA, ZIP, ELF or Mach-O path")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-bytes", type=int, default=768 * 1024 * 1024)
    args = parser.parse_args()
    try:
        scans = []
        for path in args.input:
            report = scan_binary(path, max_bytes=args.max_bytes)
            report["checked_at"] = timestamp()
            scans.append(report)
            print(f"{report['filename']}: {report['format']} "
                  f"members={report['entries_scanned']} bytes={report['bytes_scanned']} "
                  f"complete={report['complete']} versions={list(report['version_evidence'])}", flush=True)
        record = update(args.output, scans)
        print(json.dumps({"saved_scans": len(record["scans"]),
                          "versions": sorted(record["version_evidence"])}, indent=2))
        return 0
    except (OSError, ValueError, RuntimeError) as error:
        print(f"GPL binary scanner: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
