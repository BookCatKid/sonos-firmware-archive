#!/usr/bin/env python3
"""Enumerate and archive every anonymously available APKPure Sonos version."""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ("com.sonos.acr2", "com.sonos.acr")


def discover_versions(apkeep: str, package: str) -> list[str]:
    with tempfile.TemporaryDirectory(prefix="sonos-apkpure-list-") as temporary:
        result = subprocess.run(
            [apkeep, "-a", package, "-d", "apk-pure", "--list-versions", temporary],
            check=True, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
    match = re.search(r"\|\s*(.+)", result.stdout)
    if not match:
        raise RuntimeError(f"could not parse version list for {package}: {result.stdout}")
    return [item.strip() for item in match.group(1).split(",") if item.strip()]


def write_ledger(path: Path, attempts: list[dict]) -> None:
    successful = sum(item["status"] == "archived" for item in attempts)
    failed = sum(item["status"] == "failed" for item in attempts)
    document = {
        "schema_version": 1,
        "updated": datetime.now(timezone.utc).isoformat(),
        "scope": "anonymous-apkpure-history-sweep",
        "attempt_total": len(attempts),
        "archived_total": successful,
        "failed_total": failed,
        "attempts": attempts,
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2) + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apkeep", default="apkeep")
    parser.add_argument("--package", choices=PACKAGES, action="append")
    parser.add_argument("--receipt", type=Path,
                        default=ROOT / "data/apps/android-store-archive.json")
    parser.add_argument("--ledger", type=Path,
                        default=ROOT / "data/apps/android-store-history-sweep.json")
    args = parser.parse_args()
    packages = args.package or list(PACKAGES)
    receipt = json.loads(args.receipt.read_text())
    archived = {
        (item["package"], item.get("source_version_selector"))
        for item in receipt["artifacts"]
        if item.get("source_kind") == "apkpure-recovery"
    }
    attempts = (json.loads(args.ledger.read_text()).get("attempts", [])
                if args.ledger.exists() else [])
    attempted = {(x["package"], x["version_selector"]): x for x in attempts}

    for package in packages:
        versions = discover_versions(args.apkeep, package)
        print(f"{package}: discovered {len(versions)} selectors", flush=True)
        for selector in versions:
            key = (package, selector)
            if key in archived:
                print(f"already archived: {package}@{selector}", flush=True)
                continue
            command = [
                sys.executable, str(ROOT / "scripts/archive_android_store_apps.py"),
                "--source", "apkpure-recovery", "--package", package,
                "--version-selector", selector, "--apkeep", args.apkeep,
                "--receipt", str(args.receipt),
            ]
            completed = subprocess.run(
                command, cwd=ROOT, text=True,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            )
            record = {
                "package": package,
                "version_selector": selector,
                "checked": datetime.now(timezone.utc).isoformat(),
                "status": "archived" if completed.returncode == 0 else "failed",
            }
            if completed.returncode:
                record["error"] = completed.stdout[-4000:]
                print(f"FAILED {package}@{selector}", flush=True)
            else:
                archived.add(key)
                print(completed.stdout.strip(), flush=True)
            if key in attempted:
                attempts[attempts.index(attempted[key])] = record
            else:
                attempts.append(record)
            attempted[key] = record
            write_ledger(args.ledger, attempts)
    return 1 if any(item["status"] == "failed" for item in attempts) else 0


if __name__ == "__main__":
    raise SystemExit(main())
