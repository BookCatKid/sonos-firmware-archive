#!/usr/bin/env python3
"""Regenerate the private recovery-vault SHA-256 inventory."""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / "recovery-work"
INVENTORY = VAULT / "inventory.json"
RECIPIENTS = {
    "keys/model8/private.pem": "346ce6e38225ca8177024fbebfaad7043344b3bd",
    "keys/model9/private.pem": "12e82a182af27801eba0ff3c94e8e649ed962dbb",
    "keys/model12/private.pem": "e35f7c21c0ec00a768bfbd364a9a105cf300f023",
    "keys/model16/private.pem": "fd88f2642a9c89a44747c7b4342cdb483fe1d437",
    "keys/model17/private.pem": "f2acc70d8db3ee388c534d99d4bff64d9dab8154",
}


def main() -> int:
    files = []
    for path in sorted(VAULT.rglob("*")):
        if not path.is_file() or path == INVENTORY:
            continue
        relative = str(path.relative_to(VAULT))
        item = {
            "path": relative,
            "bytes": path.stat().st_size,
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        }
        if relative in RECIPIENTS:
            item["recipient_id"] = RECIPIENTS[relative]
        files.append(item)
    document = {
        "schema_version": 1,
        "created": date.today().isoformat(),
        "purpose": "Integrity inventory for the Git-ignored private recovery vault",
        "key_models": [8, 9, 12, 16, 17],
        "files": files,
    }
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    descriptor = os.open(INVENTORY, flags, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        json.dump(document, handle, indent=2)
        handle.write("\n")
    INVENTORY.chmod(0o600)
    print(f"inventoried {len(files)} private recovery-vault files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
