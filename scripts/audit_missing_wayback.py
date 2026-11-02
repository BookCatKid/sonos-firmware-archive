#!/usr/bin/env python3
"""Check Wayback CDX for every exact package currently marked missing-cdn."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import urllib.error
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path


USER_AGENT = "sonos-firmware-archive/0.1 (+preservation research)"
CDX_URL = "https://web.archive.org/cdx/search/cdx"


def query_prefix(prefix: str, timeout: float = 45) -> dict:
    params = {
        "url": prefix,
        "matchType": "prefix",
        "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest,length",
        "filter": "statuscode:200",
        "collapse": "urlkey",
        "limit": "10000",
    }
    url = f"{CDX_URL}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read())
        headings, *rows = payload if payload else [params["fl"].split(",")]
        return {
            "prefix": prefix,
            "captures": [dict(zip(headings, row)) for row in rows],
            "error": None,
        }
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        return {"prefix": prefix, "captures": [], "error": str(error)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, default=Path("data/catalog.json"))
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    args = parser.parse_args()

    catalog = json.loads(args.catalog.read_text(encoding="utf-8"))
    missing = [
        package
        for package in catalog["packages"]
        if package["artifact_status"] == "missing-cdn"
    ]
    by_prefix: dict[str, list[dict]] = defaultdict(list)
    for package in missing:
        by_prefix[package["source_url"].rsplit("/", 1)[0] + "/"].append(package)

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        prefix_results = list(pool.map(query_prefix, sorted(by_prefix)))

    captures_by_url = {
        capture["original"].replace("http://", "https://", 1): capture
        for result in prefix_results
        for capture in result["captures"]
    }
    candidates = []
    for package in missing:
        capture = captures_by_url.get(package["source_url"])
        candidates.append(
            {
                "id": package["id"],
                "source_url": package["source_url"],
                "wayback_capture": capture,
            }
        )

    receipt = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "method": "Wayback CDX metadata-only prefix queries; successful status 200 rows",
        "candidate_total": len(candidates),
        "prefix_total": len(prefix_results),
        "exact_capture_total": sum(item["wayback_capture"] is not None for item in candidates),
        "query_errors": [
            {"prefix": result["prefix"], "error": result["error"]}
            for result in prefix_results
            if result["error"]
        ],
        "candidates": candidates,
        "prefix_results": prefix_results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(receipt, indent=2) + "\n", encoding="utf-8")
    print(
        f"checked {len(candidates)} candidates across {len(prefix_results)} prefixes; "
        f"found {receipt['exact_capture_total']} exact captures; "
        f"{len(receipt['query_errors'])} query errors"
    )
    return 1 if receipt["query_errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
