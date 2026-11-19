#!/usr/bin/env python3
"""Discover official Sonos-hosted Android/Fire OS APKs via redirects and Wayback."""

from __future__ import annotations

import argparse
import concurrent.futures
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests as tls_requests
from sonos_firmware.storage import atomic_json
from sonos_firmware.installers import family as installer_family, version as installer_version, observed_candidates, installer_location, manifest_candidates
from sonos_firmware.source_urls import open_advertised, sonos_url, advertised_url, curl_source_options
from sonos_firmware.archive_replay import canonical_source

REDIRECT = "https://www.sonos.com/redir/controller_software_android2"


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def resolve_latest_redirect(*, include_history: bool = True) -> dict:
    opener = urllib.request.build_opener(NoRedirect)
    live_status = None
    try:
        session = tls_requests.Session(impersonate="chrome")
        current = REDIRECT
        for _ in range(12):
            current = canonical_source(current, lambda u: advertised_url(u, REDIRECT))
            session.curl_options.update(curl_source_options(current, REDIRECT))
            response = session.get(
                current, allow_redirects=False, timeout=30,
                headers={"Referer": "https://support.sonos.com/en-us/downloads"},
            )
            if live_status is None:
                live_status = response.status_code
            location = response.headers.get("location")
            if not location:
                break
            current = urllib.parse.urljoin(current, location)
            if installer_location(current, ('apk',), advertised_by=REDIRECT):
                return {"live_http_status": live_status, "location": installer_location(current, ('apk',), advertised_by=REDIRECT),
                        "location_source": "live"}
    except Exception as error:
        live_error = str(error)
    else:
        live_error = None

    if not include_history:
        result = {"live_http_status": live_status, "location": None, "location_source": None}
        if live_error:
            result["error"] = live_error
        return result

    current = f"https://web.archive.org/web/2id_/{REDIRECT}"
    for _ in range(12):
        try:
            opener.open(urllib.request.Request(current, method="HEAD"), timeout=30)
            break
        except urllib.error.HTTPError as error:
            location = error.headers.get("Location")
            if not location:
                break
            embedded = re.search(r"/web/\d+id_/(https?://.+)$", location)
            original = embedded.group(1) if embedded else location
            if installer_location(original, ('apk',), advertised_by=REDIRECT):
                return {"live_http_status": live_status, "location": installer_location(original, ('apk',), advertised_by=REDIRECT),
                        "location_source": "wayback"}
            current = location
        except Exception:
            break
    result = {"live_http_status": live_status, "location": None, "location_source": None}
    if live_error:
        result["error"] = live_error
    return result


def probe(item: dict) -> dict:
    request = urllib.request.Request(item["url"], method="HEAD",
                                     headers={"User-Agent": "sonos-firmware-archive/1"})
    try:
        with open_advertised(request, advertised_by=item.get('advertised_by'), timeout=30) as response:
            return {**item, "http_status": response.status,
                    "content_length": int(response.headers.get("Content-Length", 0)) or None,
                    "etag": response.headers.get("ETag")}
    except urllib.error.HTTPError as error:
        return {**item, "http_status": error.code, "content_length": None, "etag": None}
    except Exception as error:
        return {**item, "http_status": None, "content_length": None,
                "etag": None, "probe_error": str(error)}


def family(url: str) -> str:
    return installer_family(url, "fireos-android-apk")


def version(url: str) -> str:
    return installer_version(url, "fireos-android-apk")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True, metavar='FILE.json',
                        help='write the discovery report to this JSON file; parent directories are created')
    parser.add_argument('--archive-root', type=Path, default=Path(__file__).resolve().parents[1],
                        help="archive checkout whose own manifest observations supply optional installer leads")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--current-only", action="store_true")
    args = parser.parse_args()
    errors = []
    candidates = {} if args.current_only else {row['url']: row for row in
        observed_candidates(args.output.parent/'mobile-history-index.json', 'mobile')}
    if not args.current_only:
        native = manifest_candidates(args.archive_root, 'mobile')
        errors.extend(native['errors'])
        for item in native['candidates']:
            previous = candidates.setdefault(item['url'], item)
            previous['sources'] = sorted(set(previous['sources']) | set(item['sources']))
            if item.get('advertised_by'):
                previous['advertised_by'] = item['advertised_by']
    direct = resolve_latest_redirect(include_history=not args.current_only)
    if direct.get("location"):
        url = direct["location"]
        item = candidates.setdefault(url, {
            "family": family(url), "platform": "fireos-android-apk",
            "version": version(url), "url": url, "sources": [],
        })
        item["advertised_by"] = REDIRECT
        item["sources"].append("official-current-fireos-redirect" if direct.get('location_source') == 'live' else 'historical-fireos-redirect')
    for item in candidates.values():
        item['current'] = item['url'] == direct.get('location') and direct.get('location_source') == 'live'
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(probe, candidates.values()))
    results.sort(key=lambda x: (x["family"], x["version"], x["url"]))
    errors.extend({'source': x['url'], 'error': x.get('probe_error') or str(x.get('http_status'))}
                  for x in results if x.get('probe_error') or x.get('http_status') in (403, 429)
                  or (x.get('http_status') or 0) >= 500)
    if not direct.get('location') or direct.get('location_source') != 'live':
        errors.append({'source': REDIRECT, 'error': 'current redirect unresolved; historical fallback is not a live observation'})
    document = {
        'status': 'partial' if errors else 'ok', 'errors': errors,
        "schema_version": 1, "checked": datetime.now(timezone.utc).isoformat(),
        "current_only": args.current_only,
        "official_redirect": {"url": REDIRECT, **direct},
        "wayback": {"provider": "shared-installer-history"},
        "candidate_total": len(results),
        "available_live_total": sum(x.get("http_status") == 200 for x in results),
        "candidates": results,
    }
    atomic_json(args.output, document)
    print(f"candidates={len(results)} live={document['available_live_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
