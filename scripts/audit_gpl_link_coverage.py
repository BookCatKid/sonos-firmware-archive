#!/usr/bin/env python3
"""Reconcile every advertised GPL index link against its acquisition receipt.

A verified index is the archive's own claim that these sources exist. Each link
must therefore end in exactly one accounted state: published with a release
receipt, or recorded with the terminal reason it could not be published
(`invalid-artifact`, `http-error`, `network-error`, ...). A link with no receipt
at all is *unattempted* -- it is silently unaccounted for, will be re-downloaded
forever without leaving a record, and is invisible to both the publication audit
(it never reached GitHub) and the compressed-stream audit (which only revalidates
published receipts).

This audit is offline: it reads the catalog and receipts only, so it can run in
the test suite and on a worker without acquiring anything.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from sonos_firmware.gpl_archive import source_receipt_keys, valid_version

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA = ROOT / "data/gpl/live"

# States that prove the archive looked at a link and reached a durable verdict.
TERMINAL_STATES = frozenset({
    "published", "verified", "invalid-artifact", "http-error", "network-error",
    "incomplete-transfer", "unexpected-content-type", "insufficient-space",
    "publish-conflict",
})


def classify(link: dict, receipt: dict | None) -> tuple[str, str | None]:
    """Return (state, detail) for one advertised link.

    `unattempted` is the only state that requires work: no receipt means the
    link was neither published nor rejected, so the archive cannot explain it.
    """
    if not receipt:
        return "unattempted", None
    if receipt.get("published"):
        return "published", receipt.get("release_asset")
    state = receipt.get("state")
    if state in TERMINAL_STATES:
        return f"recorded:{state}", receipt.get("verification") or receipt.get("error")
    return f"recorded:{state}", receipt.get("verification") or receipt.get("error")


def coverage(catalog: dict, receipts: dict) -> dict:
    """Per-version link coverage with an actionable unattempted list."""
    releases = catalog.get("releases") or {}
    artifacts = receipts.get("artifacts") or {}
    totals: dict[str, int] = {}
    rows = []
    unattempted = []
    for version in sorted(releases, key=lambda value: tuple(map(int, value.split(".")))):
        record = releases[version]
        if record.get("state") != "verified":
            continue
        valid_version(version)
        prior = artifacts.get(version) or {}
        keys = source_receipt_keys(record["links"], prior)
        counts: dict[str, int] = {}
        for link in record["links"]:
            state, detail = classify(link, prior.get(keys[link["url"]]))
            counts[state] = counts.get(state, 0) + 1
            totals[state] = totals.get(state, 0) + 1
            if state == "unattempted":
                unattempted.append({"version": version, "filename": link["filename"],
                                    "url": link["url"], "origin": link.get("origin"),
                                    "kind": link.get("kind")})
        rows.append({"version": version, "links": len(record["links"]), "states": counts})
    return {
        "schema_version": 1,
        "versions": len(rows),
        "link_total": sum(totals.values()),
        "state_totals": totals,
        "unattempted_total": len(unattempted),
        "unattempted": unattempted,
        "versions_detail": rows,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=DEFAULT_DATA,
                        help="directory holding catalog.json and artifacts.json")
    parser.add_argument("--output", type=Path, help="write the coverage report as JSON")
    parser.add_argument("--allow-unattempted", action="store_true",
                        help="report unattempted links without failing the job")
    args = parser.parse_args()

    catalog = json.loads((args.data / "catalog.json").read_text())
    receipts = json.loads((args.data / "artifacts.json").read_text())
    report = coverage(catalog, receipts)
    if args.output:
        from sonos_firmware.storage import atomic_json
        atomic_json(args.output, report)
    states = ", ".join(f"{state}={count}" for state, count in sorted(report["state_totals"].items()))
    print(f"versions={report['versions']} links={report['link_total']} "
          f"unattempted={report['unattempted_total']} ({states})")
    for row in report["unattempted"]:
        print(f"  unattempted {row['version']}/{row['filename']} ({row['origin']})")
    if report["unattempted_total"] and not args.allow_unattempted:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
