#!/usr/bin/env python3
"""Discover historical Sonos Windows/macOS installers from public indexes.

The script reads the current and historical WinGet/Homebrew manifests, derives
same-release cross-platform Sonos CDN URLs, probes availability, and writes a
machine-readable candidate list. It downloads no installer bodies.
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures
import json
import os
import re
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from curl_cffi import requests as tls_requests

WINGET_PACKAGES = {"Controller": "s2", "S1Controller": "s1"}
HOMEBREW_PATHS = {
    "Casks/s/sonos.rb": "s2",
    "Casks/sonos.rb": "s2",
    "Casks/s/sonos-s1-controller.rb": "s1",
}
DIRECT_ENDPOINTS = {
    "s2-windows": "https://www.sonos.com/redir/controller_software_pc2",
    "s2-macos": "https://www.sonos.com/redir/controller_software_mac2",
    "s1-windows": "https://www.sonos.com/redir/controller_software_pc",
    "s1-macos": "https://www.sonos.com/redir/controller_software_mac",
}
WAYBACK_CDX = "https://web.archive.org/cdx/search/cdx"


def gh_json(endpoint: str) -> object:
    return json.loads(subprocess.check_output(["gh", "api", endpoint], text=True))


def gh_lines(endpoint: str, jq: str) -> list[str]:
    return subprocess.check_output(
        ["gh", "api", "--paginate", endpoint, "--jq", jq], text=True
    ).splitlines()


def add_candidate(target: dict, *, family: str, platform: str, version: str,
                  url: str, sha256: str | None, source: str) -> None:
    # A WinGet package spans the pre-split and S1/S2 eras, so its package name
    # alone is not a reliable family label for historical filenames.
    filename = url.rsplit("/", 1)[-1]
    if filename.startswith("SonosDesktopController") or filename.startswith("Sonos_"):
        family = family_for_wayback_url(url)
    key = (family, platform, url)
    item = target.setdefault(key, {
        "family": family, "platform": platform, "version": version,
        "url": url, "expected_sha256": sha256.lower() if sha256 else None,
        "sources": [],
    })
    if source not in item["sources"]:
        item["sources"].append(source)
    if sha256:
        digest = sha256.lower()
        if item["expected_sha256"] not in (None, digest):
            raise ValueError(f"conflicting hashes for {url}")
        item["expected_sha256"] = digest


def winget_candidates(target: dict) -> None:
    base = "repos/microsoft/winget-pkgs/contents/manifests/s/Sonos"
    for package, family in WINGET_PACKAGES.items():
        versions = gh_json(f"{base}/{package}?ref=master")
        for version in versions:
            files = gh_json(version["url"].split("api.github.com/")[-1])
            manifests = [f for f in files if f["name"].endswith(".installer.yaml")]
            for manifest in manifests:
                payload = gh_json(manifest["url"].split("api.github.com/")[-1])
                text = base64.b64decode(payload["content"]).decode()
                urls = re.findall(r"^\s*InstallerUrl:\s*(\S+)", text, re.M)
                hashes = re.findall(r"^\s*InstallerSha256:\s*([0-9A-Fa-f]{64})", text, re.M)
                for url, digest in zip(urls, hashes):
                    add_candidate(target, family=family, platform="windows",
                                  version=version["name"], url=url, sha256=digest,
                                  source="winget-current")


def homebrew_candidates(target: dict) -> None:
    for path, family in HOMEBREW_PATHS.items():
        commits = gh_lines(
            f"repos/Homebrew/homebrew-cask/commits?path={path}&per_page=100", ".[].sha"
        )
        for sha in commits:
            raw_url = f"https://raw.githubusercontent.com/Homebrew/homebrew-cask/{sha}/{path}"
            try:
                text = urllib.request.urlopen(raw_url, timeout=20).read().decode()
            except urllib.error.HTTPError:
                continue
            version_match = re.search(r'^\s*version "([^"]+)"', text, re.M)
            hash_match = re.search(r'^\s*sha256 "([0-9a-f]{64})"', text, re.M)
            if not version_match or not hash_match:
                continue
            parts = version_match.group(1).split(",")
            version = parts[0]
            if len(parts) == 2:
                url = f"https://update-software.sonos.com/software/{parts[1]}/Sonos_{version}.dmg"
            else:
                url_match = re.search(r'^\s*url "([^"]+)"', text, re.M)
                if not url_match:
                    continue
                url = url_match.group(1)
            add_candidate(target, family=family, platform="macos", version=version,
                          url=url, sha256=hash_match.group(1),
                          source=f"homebrew-history:{sha[:12]}")


def family_for_wayback_url(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    if re.search(r"Sonos_57\.", name):
        return "s1"
    if name.startswith("Sonos_"):
        return "s2"
    match = re.search(r"SonosDesktopController([^.]*)", name)
    token = match.group(1).lower() if match else ""
    if token.startswith("112"):
        return "s1"
    if token.startswith(("12", "13")):
        return "s2"
    return "legacy"


def version_for_url(url: str) -> str:
    name = url.rsplit("/", 1)[-1]
    match = re.search(r"Sonos_([^.]*(?:\.[^.]*)*)\.(?:exe|dmg)$", name, re.I)
    if match:
        return match.group(1)
    match = re.search(r"SonosDesktopController([^.]*)\.(?:exe|dmg)$", name, re.I)
    return f"classic-{match.group(1)}" if match else "unknown"


def wayback_candidates(target: dict) -> dict:
    query = urllib.parse.urlencode({
        "url": "update-software.sonos.com/software/",
        "matchType": "prefix", "output": "json",
        "fl": "timestamp,original,statuscode,mimetype,digest,length",
        "filter": ["statuscode:200", r"original:.*\.(exe|dmg)$"],
        "collapse": "urlkey", "limit": "5000",
    }, doseq=True)
    url = f"{WAYBACK_CDX}?{query}"
    error = None
    for attempt in range(3):
        try:
            rows = json.loads(urllib.request.urlopen(url, timeout=90).read())
            break
        except Exception as caught:
            error = caught
            if attempt < 2:
                time.sleep(2 ** attempt)
    else:
        raise RuntimeError(f"Wayback CDX query failed after 3 attempts: {error}")
    for timestamp, original, _status, _mime, digest, length in rows[1:]:
        live_url = original.replace("http://", "https://", 1)
        platform = "windows" if live_url.lower().endswith(".exe") else "macos"
        add_candidate(
            target, family=family_for_wayback_url(live_url), platform=platform,
            version=version_for_url(live_url), url=live_url, sha256=None,
            source=f"wayback-cdx:{timestamp}:{digest}:{length}",
        )
    return {"status": "ok", "row_total": max(0, len(rows) - 1), "url": url}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_direct_redirects() -> list[dict]:
    opener = urllib.request.build_opener(NoRedirect)
    results = []
    for name, url in DIRECT_ENDPOINTS.items():
        location = None
        source = None
        try:
            session = tls_requests.Session(impersonate="chrome")
            current = url
            status = None
            for _ in range(10):
                response = session.get(
                    current, allow_redirects=False, timeout=30,
                    headers={"Referer": "https://support.sonos.com/en-us/downloads"},
                )
                if status is None:
                    status = response.status_code
                next_url = response.headers.get("location")
                if not next_url:
                    break
                next_url = urllib.parse.urljoin(current, next_url)
                if re.search(r"https?://update(?:-software|-beta)?\.sonos\.com/.+\.(?:exe|dmg)$", next_url):
                    location = next_url
                    source = "live-redirect"
                    break
                current = next_url
        except Exception as error:
            results.append({"name": name, "url": url, "http_status": None,
                            "location": None, "error": str(error)})
            continue
        if not location:
            current = f"https://web.archive.org/web/2id_/{url}"
            for _ in range(10):
                try:
                    opener.open(urllib.request.Request(current, method="HEAD"), timeout=30)
                    break
                except urllib.error.HTTPError as error:
                    next_url = error.headers.get("Location")
                    if not next_url:
                        break
                    embedded = re.search(r"/web/\d+id_/(https?://.+)$", next_url)
                    original = embedded.group(1) if embedded else next_url
                    if re.search(r"https?://update-(?:software|beta)\.sonos\.com/.+\.(?:exe|dmg)$", original):
                        location = original
                        source = "wayback-latest-redirect"
                        break
                    current = next_url
                except Exception:
                    break
        results.append({"name": name, "url": url, "http_status": status,
                        "location": location, "location_source": source})
    return results


def counterpart(candidate: dict) -> tuple[str, str] | None:
    url = candidate["url"]
    if candidate["platform"] == "windows":
        changed = url.replace("/software/pc/dcr/", "/software/mac/mdcr/")
        if changed.endswith(".exe"):
            return "macos", changed[:-4] + ".dmg"
    elif candidate["platform"] == "macos":
        changed = url.replace("/software/mac/mdcr/", "/software/pc/dcr/")
        if changed.endswith(".dmg"):
            return "windows", changed[:-4] + ".exe"
    return None


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


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    args = parser.parse_args()
    candidates: dict = {}
    winget_candidates(candidates)
    homebrew_candidates(candidates)
    wayback = wayback_candidates(candidates)
    direct_redirects = check_direct_redirects()
    for redirect in direct_redirects:
        url = redirect.get("location")
        if not url:
            continue
        family, platform = redirect["name"].split("-", 1)
        add_candidate(
            candidates, family=family, platform=platform,
            version=version_for_url(url), url=url, sha256=None,
            source=f"official-direct:{redirect['name']}:{redirect['location_source']}",
        )
    for item in list(candidates.values()):
        other = counterpart(item)
        if other:
            platform, url = other
            add_candidate(candidates, family=item["family"], platform=platform,
                          version=item["version"], url=url, sha256=None,
                          source="derived-cross-platform")
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(probe, candidates.values()))
    results.sort(key=lambda x: (x["family"], x["platform"], x["version"], x["url"]))
    document = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "sources": ["microsoft/winget-pkgs", "Homebrew/homebrew-cask", "Sonos CDN HEAD"],
        "direct_redirects": direct_redirects,
        "wayback": wayback,
        "candidate_total": len(results),
        "available_total": sum(x["http_status"] == 200 for x in results),
        "candidates": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(document, indent=2) + "\n")
    print(f"candidates={len(results)} available={document['available_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
