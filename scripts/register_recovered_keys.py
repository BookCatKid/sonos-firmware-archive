#!/usr/bin/env python3
"""Move exact recipient groups from missing to recovered in the key ledger."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recipient", action="append", required=True)
    parser.add_argument("--family", action="append", required=True)
    parser.add_argument("--model", action="append", type=int, required=True)
    args = parser.parse_args()
    if not (len(args.recipient) == len(args.family) == len(args.model)):
        raise ValueError("recipient, family, and model counts must match")

    path = ROOT / "data/key-recovery-ledger.json"
    ledger = json.loads(path.read_text(encoding="utf-8"))
    missing = {item["recipient_id"]: item for item in ledger["missing_keys"]}
    for recipient, family, model in zip(args.recipient, args.family, args.model):
        group = missing.pop(recipient, None)
        if group is None:
            raise ValueError(f"missing recipient group not found: {recipient}")
        models = {item["package_model"] for item in group["packages"]}
        if models != {model}:
            raise ValueError(f"recipient {recipient} has unexpected models {sorted(models)}")
        ledger["recovered_keys"][recipient] = {
            "coverage_packages": group["packages"],
            "key_family": family,
            "package_model": model,
        }
    ledger["missing_keys"] = [
        item for item in ledger["missing_keys"] if item["recipient_id"] in missing
    ]
    path.write_text(json.dumps(ledger, indent=2) + "\n", encoding="utf-8")
    print(f"registered {len(args.recipient)} recovered recipients; {len(missing)} remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
