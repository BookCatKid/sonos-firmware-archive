#!/usr/bin/env python3
"""Reproduce the public model-26/Tupelo key recovery from an MDP3 field and OTP."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.model26 import recover, verified_bytes, FIELD_SHA256, OTP_SHA256, RECIPIENT_ID

INPUTS = ROOT / "data" / "key-material" / "model26"

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--field", type=Path, default=INPUTS / "mdp3-model-key.bin")
    parser.add_argument("--otp", type=Path, default=INPUTS / "otp.bin")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        recipient = recover(args.field, args.otp, args.output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"recovery failed: {error}\n")
    print(json.dumps({"model": 26, "recipient_id": recipient, "output": str(args.output)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
