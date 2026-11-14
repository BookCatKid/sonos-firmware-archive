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

from sonos_firmware.releases import receipt_matches_repository
from sonos_firmware.storage import atomic_json

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


def write_ledger(path: Path, attempts: list[dict], pending_selectors: list[dict]) -> None:
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
        "pending_selectors": pending_selectors,
    }
    atomic_json(path, document)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apkeep", default="apkeep")
    parser.add_argument('--repo', default='BookCatKid/sonos-firmware-archive')
    parser.add_argument('--limit', type=int, default=24, help='maximum acquisition attempts per worker run')
    parser.add_argument("--package", choices=PACKAGES, action="append")
    parser.add_argument("--receipt", type=Path,
                        default=ROOT / "data/apps/android-store-archive.json")
    parser.add_argument("--ledger", type=Path,
                        default=ROOT / "data/apps/android-store-history-sweep.json")
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error('--limit must be positive')
    packages = args.package or list(PACKAGES)
    receipt = json.loads(args.receipt.read_text()) if args.receipt.exists() else {'artifacts': []}
    archived = {
        (item["package"], selector)
        for item in receipt["artifacts"]
        if item.get("source_kind") == "apkpure-recovery" and receipt_matches_repository(item, args.repo)
        for selector in {item.get('source_version_selector'), *item.get('source_version_selectors', [])}
    }
    state = json.loads(args.ledger.read_text()) if args.ledger.exists() else {}
    attempts = state.get('attempts', [])
    attempted = {(x["package"], x["version_selector"]): x for x in attempts}

    queue = {(row['package'], row['version_selector']): row for row in state.get('pending_selectors', [])}
    discovery_errors = []
    for package in packages:
        try:
            versions = discover_versions(args.apkeep, package)
        except Exception as error:
            discovery_errors.append({'package': package, 'error': str(error)})
            continue
        print(f"{package}: discovered {len(versions)} selectors", flush=True)
        for rank, selector in enumerate(versions):
            key = (package, selector)
            if key in archived:
                print(f"already archived: {package}@{selector}", flush=True)
                continue
            queue.setdefault(key, {'package': package, 'version_selector': selector, 'rank': rank})
    for attempt in attempts:
        key = (attempt['package'], attempt['version_selector'])
        if key[0] in packages and key not in archived:
            queue.setdefault(key, {'package': key[0], 'version_selector': key[1], 'rank': len(queue)})
    pending = [(row['package'], row['version_selector'], row.get('rank', rank))
               for rank, row in enumerate(queue.values()) if row['package'] in packages
               and (row['package'], row['version_selector']) not in archived]
    def save():
        write_ledger(args.ledger, attempts, [row for key, row in queue.items() if key not in archived])
    # Save every observation before the bounded acquisition loop. Even a lead
    # never attempted in this worker must survive a later listing outage.
    save()
    # Untried selectors first, then oldest attempts. Rank interleaves packages
    # so a large history or a persistent failure cannot monopolize the worker.
    pending.sort(key=lambda item: (attempted.get(item[:2], {}).get('checked', ''), item[2], item[0]))
    for package, selector, _ in pending[:args.limit]:
        key = (package, selector)
        command = [
            sys.executable, str(ROOT / "scripts/archive_android_store_apps.py"),
            "--source", "apkpure-recovery", "--package", package,
            "--version-selector", selector, "--apkeep", args.apkeep,
            "--receipt", str(args.receipt),
            '--repo', args.repo,
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
        save()
    save()
    document = json.loads(args.ledger.read_text())
    document.update(discovery_errors=discovery_errors,
                    pending_total=sum((p, s) not in archived for p, s, _ in pending))
    document['status'] = 'partial' if discovery_errors or document['pending_total'] else 'ok'
    atomic_json(args.ledger, document)
    return 1 if document['status'] == 'partial' else 0


if __name__ == "__main__":
    raise SystemExit(main())
