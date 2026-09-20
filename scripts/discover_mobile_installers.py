#!/usr/bin/env python3
"""Discover official Sonos-hosted Android/Fire OS APKs via redirects and Wayback."""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

CDX = "https://web.archive.org/cdx/search/cdx"
REDIRECT = "https://www.sonos.com/redir/controller_software_android2"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_latest_redirect() -> dict:
    opener = urllib.request.build_opener(NoRedirect)
    current = REDIRECT
    live_status = None
    for phase in ("live", "wayback"):
        if phase == "wayback":
            current = f"https://web.archive.org/web/2id_/{REDIRECT}"
        for _ in range(12):
            try:
                opener.open(urllib.request.Request(current, method="HEAD", headers={
                    "User-Agent": "Mozilla/5.0 SonosArchiveMonitor/1",
                    "Referer": "https://support.sonos.com/en-us/downloads",
                }), timeout=30)
                break
            except urllib.error.HTTPError as error:
                if phase == "live" and live_status is None:
                    live_status = error.code
                location = error.headers.get("Location")
                if not location:
                    break
                embedded = re.search(r"/web/\d+id_/(https?://.+)$", location)
                original = embedded.group(1) if embedded else location
                if re.search(r"https?://update-(?:software|beta)\.sonos\.com/.+\.apk$", original):
                    return {"live_http_status": live_status, "location": original,
                            "location_source": phase}
                current = location
            except Exception as error:
                if phase == "live":
                    return {"live_http_status": None, "location": None,
                            "location_source": None, "error": str(error)}
                break
    return {"live_http_status": live_status, "location": None, "location_source": None}


def probe(item: dict) -> dict:
    request = urllib.request.Request(item["url"], method="HEAD",
                                     headers={"User-Agent": "sonos-firmware-archive/1"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return {**item, "http_status": response.status,
                    "content_length": int(response.headers.get("Content-Length", 0)) or None,
                    "etag": response.headers.get("ETag")}
    except urllib.error.HTTPError as error:
        return {**item, "http_status": error.code, "content_length": None, "etag": None}
    except Exception as error:
        return {**item, "http_status": None, "content_length": None,
                "etag": None, "probe_error": str(error)}


def family(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    if name.startswith("SonosAndroidController"):
        return "legacy"
    return "s1" if re.search(r"Sonos_57\.", name) else "s2"


def version(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    match = re.search(r"(?:Sonos_|SonosAndroidController)([^.]*(?:\.[^.]*)*)\.apk$", name)
    return match.group(1) if match else "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    query = urllib.parse.urlencode({
        "url": "update-software.sonos.com/software/", "matchType": "prefix",
        "output": "json", "fl": "timestamp,original,statuscode,mimetype,digest,length",
        "filter": ["statuscode:200", r"original:.*\.apk$"],
        "collapse": "urlkey", "limit": "5000",
    }, doseq=True)
    cdx_url = f"{CDX}?{query}"
    rows = json.loads(urllib.request.urlopen(cdx_url, timeout=90).read())
    candidates = {}
    for timestamp, original, _status, _mime, digest, length in rows[1:]:
        url = original.replace("http://", "https://", 1)
        candidates[url] = {
            "family": family(url), "platform": "fireos-android-apk",
            "version": version(url), "url": url,
            "wayback_timestamp": timestamp, "wayback_digest": digest,
            "wayback_length": int(length), "sources": ["wayback-cdx"],
        }
    direct = resolve_latest_redirect()
    if direct.get("location"):
        url = direct["location"]
        item = candidates.setdefault(url, {
            "family": family(url), "platform": "fireos-android-apk",
            "version": version(url), "url": url, "sources": [],
        })
        item["sources"].append("official-current-fireos-redirect")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(probe, candidates.values()))
    results.sort(key=lambda x: (x["family"], x["version"], x["url"]))
    document = {
        "schema_version": 1, "checked": datetime.now(timezone.utc).isoformat(),
        "official_redirect": {"url": REDIRECT, **direct},
        "wayback": {"url": cdx_url, "row_total": max(0, len(rows) - 1)},
        "candidate_total": len(results),
        "available_live_total": sum(x.get("http_status") == 200 for x in results),
        "candidates": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    print(f"candidates={len(results)} live={document['available_live_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
