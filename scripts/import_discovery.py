#!/usr/bin/env python3
"""Merge a discovery receipt into the catalog and generate UPD manifests."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.firmware_archive import import_receipt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("discovery", type=Path)
    parser.add_argument("receipt", type=Path)
    parser.add_argument("artifacts", type=Path)
    args = parser.parse_args()

    imported = import_receipt(ROOT, json.loads(args.discovery.read_text()),
                              json.loads(args.receipt.read_text()), args.artifacts)
    print(f'registered {len(imported)} verified firmware captures')
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
