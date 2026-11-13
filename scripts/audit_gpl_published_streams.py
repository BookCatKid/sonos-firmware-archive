#!/usr/bin/env python3
"""Revalidate previously published GPL archives against original source bytes.

A historic tar member listing does not necessarily validate an XZ/gzip/bzip2
footer. This job reacquires each unique published source from the recorded
upstream URL, validates the *entire* compressed stream, and compares SHA-256
with the immutable GitHub publication receipt. It never replaces release assets.
"""

from __future__ import annotations

import argparse
import json
import urllib.error
import sys
from pathlib import Path

from sonos_firmware.gpl_archive import (
    atomic_json, download_artifact, load_json, pause, timestamp, valid_version,
)

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/gpl/live"


def strong_proofs(receipts: dict) -> dict[str, dict]:
    """Anchor every reusable digest in an actually completed stream audit.

    A bare `state: verified` field is not enough: publication checksums,
    legacy tar-member checks, and unanchored recursive proofs are excluded.
    """
    strong = {}
    for version, entries in receipts.get("artifacts", {}).items():
        for name, row in entries.items():
            stream = row.get("stream_integrity", {})
            if (row.get("kind") == "source" and row.get("published") and
                stream.get("state") == "verified" and
                stream.get("method") == "tar-and-compression-integrity" and
                stream.get("expected_sha256") == row.get("sha256") and
                stream.get("observed_sha256") == row.get("sha256") and
                stream.get("observed_bytes") == row.get("bytes") and
                isinstance(stream.get("members"), int) and stream["members"] > 0):
                strong.setdefault(row["sha256"], {
                    "version": version, "filename": name,
                    "checked_at": stream["checked_at"],
                })
    return strong


def pending(receipts: dict, *, versions: set[str] | None, retry_failed: bool) -> list[tuple[str, str, dict]]:
    result = []
    anchored = strong_proofs(receipts)
    for version, entries in sorted(receipts.get("artifacts", {}).items(), key=lambda kv: tuple(map(int, kv[0].split('.')))):
        if versions is not None and version not in versions:
            continue
        for name, item in sorted(entries.items()):
            if item.get("kind") != "source" or not item.get("published"):
                continue
            prior = item.get("stream_integrity", {})
            if (prior.get("state") == "verified" and prior.get("expected_sha256") == item.get("sha256")
                    and item.get("sha256") in anchored):
                continue
            if prior and not retry_failed:
                state = prior.get("state")
                if state in {"content-mismatch", "invalid-source", "invalid-artifact", "unexpected-content-type"}:
                    continue
                if state == "http-error" and prior.get("error", {}).get("http_status") in {403, 404}:
                    continue
            result.append((version, name, item))
    return result


def run(args: argparse.Namespace, *, download=download_artifact, wait=pause) -> dict:
    if args.limit < 1:
        raise ValueError("audit --limit must be positive")
    for version in args.version:
        valid_version(version)
    records = load_json(args.output / "artifacts.json", {"schema_version": 1, "artifacts": {}})
    # Limit by *published source records*, including zero-byte/network-free dedup
    # records, to keep metadata commits bounded and deterministic.
    queue = pending(records, versions=set(args.version) if args.version else None,
                    retry_failed=args.retry_failed)
    proofs = strong_proofs(records)
    stats = {"selected": min(len(queue), args.limit), "downloaded": 0, "verified": 0,
             "reused": 0, "mismatched": 0, "failed": 0, "bytes_downloaded": 0,
             "remaining_before": len(queue)}
    for version, name, item in queue[:args.limit]:
        valid_version(version)
        digest = item["sha256"]
        if digest in proofs:
            proof = proofs[digest]
            item["stream_integrity"] = {
                "state": "verified", "expected_sha256": digest,
                "checked_at": timestamp(),
                "method": "sha256-equality-with-fully-verified-original-source",
                "proof": proof,
            }
            stats["reused"] += 1
            stats["verified"] += 1
        else:
            link = {"filename": name, "kind": "source", "origin": item.get("origin") or
                    ("sonos" if "sonos.com" in (item.get("fetch_url") or "") else "upstream"),
                    "url": item.get("source_url") or item.get("fetch_url"),
                    "fetch_url": item.get("fetch_url")}
            try:
                outcome = download(link, args.store, min_free_bytes=args.reserve_bytes)
            except (OSError, TimeoutError, urllib.error.URLError) as error:
                outcome = {"state": "network-error", "filename": name,
                           "error_type": type(error).__name__}
            stats["downloaded"] += 1
            if outcome["state"] == "verified":
                stats["bytes_downloaded"] += outcome["bytes"]
                digest_match = outcome["sha256"] == digest and outcome["bytes"] == item["bytes"]
                status = "verified" if digest_match else "content-mismatch"
                item["stream_integrity"] = {
                    "state": status, "expected_sha256": digest,
                    "observed_sha256": outcome["sha256"],
                    "observed_bytes": outcome["bytes"],
                    "checked_at": timestamp(),
                    "method": outcome["verification"], "members": outcome.get("members", 0),
                }
                # Delete ONLY this tool's content-addressed re-download, not a
                # published release or the user's original historic source.
                blob = args.store / "sha256" / outcome["sha256"][:2] / outcome["sha256"]
                if blob.is_file():
                    blob.unlink()
                if digest_match:
                    proofs[digest] = {"version": version, "filename": name,
                                      "checked_at": item["stream_integrity"]["checked_at"]}
                    stats["verified"] += 1
                else:
                    stats["mismatched"] += 1
            else:
                item["stream_integrity"] = {"state": outcome["state"],
                    "expected_sha256": digest, "checked_at": timestamp(),
                    "method": "original-source-reacquisition", "error": {
                        k: v for k, v in outcome.items() if k in
                        {"http_status", "content_type", "verification", "partial_bytes", "need_bytes", "error_type"}}}
                stats["failed"] += 1
        records["updated"] = timestamp()
        atomic_json(args.output / "artifacts.json", records)
        print(f"{version}/{name}: {item['stream_integrity']['state']} "
              f"{item['stream_integrity']['method']}", flush=True)
        if item["stream_integrity"]["state"] in {"insufficient-space", "network-error", "rate-limited"} or (
            item["stream_integrity"]["state"] == "http-error" and item["stream_integrity"].get("error", {}).get("http_status") in {429, 500, 502, 503, 504}
        ):
            print("stopping this batch after transient/storage failure", flush=True)
            break
        if stats["downloaded"] and item["stream_integrity"]["method"] != "sha256-equality-with-fully-verified-original-source":
            wait(args.delay)
    stats["remaining_after"] = len(pending(records, versions=set(args.version) if args.version else None,
                                         retry_failed=args.retry_failed))
    print(json.dumps(stats, indent=2), flush=True)
    return stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DATA)
    parser.add_argument("--store", type=Path, default=ROOT / "artifacts/gpl-stream-audit")
    parser.add_argument("--version", action="append", default=[])
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--reserve-bytes", type=int, default=2_000_000_000)
    parser.add_argument("--delay", type=float, default=2.5)
    parser.add_argument("--retry-failed", action="store_true")
    args = parser.parse_args()
    try:
        run(args)
        return 0
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        print(f"GPL stream audit: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
