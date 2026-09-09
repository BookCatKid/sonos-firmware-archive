#!/usr/bin/env python3
"""Verify hashes, permissions, fingerprints, and ignore status of the private vault."""

from __future__ import annotations

import hashlib
import json
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
VAULT = ROOT / "recovery-work"
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.mdp import recipient_id_for_key_file  # noqa: E402


def main() -> int:
    inventory_path = VAULT / "inventory.json"
    inventory = json.loads(inventory_path.read_text(encoding="utf-8"))
    errors: list[str] = []
    if stat.S_IMODE(inventory_path.stat().st_mode) & 0o077:
        errors.append("permissions too broad: inventory.json")
    if subprocess.run(
        ["git", "check-ignore", "--quiet", str(inventory_path.relative_to(ROOT))],
        cwd=ROOT,
    ).returncode != 0:
        errors.append("not gitignored: inventory.json")
    inventoried = {item["path"] for item in inventory["files"]}
    present = {
        str(path.relative_to(VAULT))
        for path in VAULT.rglob("*")
        if path.is_file() and path.name != "inventory.json"
    }
    for relative in sorted(inventoried - present):
        errors.append(f"inventoried file is missing: {relative}")
    for relative in sorted(present - inventoried):
        errors.append(f"unlisted vault file: {relative}")
    for directory in [VAULT, *(path for path in VAULT.rglob("*") if path.is_dir())]:
        if stat.S_IMODE(directory.stat().st_mode) & 0o077:
            errors.append(f"directory permissions too broad: {directory.relative_to(ROOT)}")
    for item in inventory["files"]:
        path = VAULT / item["path"]
        if not path.is_file():
            errors.append(f"missing: {item['path']}")
            continue
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != item["sha256"]:
            errors.append(f"hash mismatch: {item['path']}")
        if stat.S_IMODE(path.stat().st_mode) & 0o077:
            errors.append(f"permissions too broad: {item['path']}")
        if "recipient_id" in item:
            try:
                actual = recipient_id_for_key_file(path)
            except ValueError as error:
                errors.append(f"invalid private key {item['path']}: {error}")
            else:
                if actual != item["recipient_id"]:
                    errors.append(f"recipient mismatch: {item['path']}")
        ignored = subprocess.run(
            ["git", "check-ignore", "--quiet", str(path.relative_to(ROOT))],
            cwd=ROOT,
        ).returncode == 0
        if not ignored:
            errors.append(f"not gitignored: {item['path']}")
    if errors:
        print("\n".join(f"error: {error}" for error in errors))
        return 1
    print(f"verified {len(inventory['files'])} private recovery-vault files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
