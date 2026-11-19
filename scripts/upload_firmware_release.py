#!/usr/bin/env python3
"""Resumably upload a firmware discovery receipt to an existing Release."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.firmware_archive import upload_receipt  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    args = parser.parse_args()

    upload_receipt(args.receipt, args.directory, args.tag, args.repo)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
