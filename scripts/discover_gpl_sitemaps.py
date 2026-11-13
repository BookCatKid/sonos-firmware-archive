#!/usr/bin/env python3
"""Discover GPL index leads from public Sonos XML sitemaps, without Wayback.

The job inspects only independently published Sonos sitemap URLs. Each page is
fetched once per checkpoint unless explicitly rechecked. A matched version is
only a candidate; `archive_gpl_direct.py discover` validates the actual index.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from pathlib import Path

from sonos_firmware.gpl_archive import (
    USER_AGENT, VERSION_IN_URL, atomic_json, get_page, load_json, pause,
    sha256_bytes, timestamp, valid_version,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "data/gpl/live/site-evidence.json"
SEEDS = (
    "https://www.sonos.com/sitemap.xml",
    "https://www.sonos.com/blog/sitemap-index.xml",
    "https://www.sonos.com/sonos-pro/sitemap-index.xml",
    "https://support.sonos.com/sitemap-index.xml",
)
HOSTS = {"www.sonos.com", "support.sonos.com"}
MAX_XML_BYTES = 2 * 1024 * 1024
MAX_CHILD_SITEMAPS = 1500
SITEMAP_SUFFIXES = ("/sitemap.xml", "/sitemap-index.xml", "/sitemap_index.xml")


def allowed_sitemap(url: str) -> bool:
    parts = urllib.parse.urlsplit(url)
    return (parts.scheme == "https" and parts.hostname in HOSTS
            and (parts.path.endswith(SITEMAP_SUFFIXES))
            and not parts.username and not parts.password and not parts.query and not parts.fragment)


def parse_xml_sitemap(body: bytes) -> tuple[str, list[str]]:
    if len(body) > MAX_XML_BYTES:
        raise ValueError("sitemap too large")
    try:
        doc = ET.fromstring(body)
    except ET.ParseError as error:
        raise ValueError(f"malformed sitemap XML: {error}") from error
    element_name = doc.tag.rsplit("}", 1)[-1]
    if element_name not in ("sitemapindex", "urlset"):
        raise ValueError(f"unexpected sitemap root: {element_name}")
    elements = []
    for loc in doc.iter():
        if loc.tag.rsplit("}", 1)[-1] == "loc" and loc.text:
            elements.append(loc.text.strip())
    return element_name, elements


def extract_leads(urls: list[str]) -> tuple[dict[str, list[str]], list[str]]:
    versions: dict[str, set[str]] = {}
    related = set()
    for url in urls:
        if not url.startswith("https://"):
            continue
        host = urllib.parse.urlsplit(url).hostname
        if host not in HOSTS:
            continue
        if any(word in urllib.parse.urlsplit(url).path.lower() for word in ("gpl", "lgpl", "opensource", "open-source", "licenses")):
            related.add(url)
        for match in VERSION_IN_URL.finditer(url):
            version = match.group(1)
            try:
                valid_version(version)
            except ValueError:
                continue
            versions.setdefault(version, set()).add(url)
    return {v: sorted(links) for v, links in versions.items()}, sorted(related)


def scan(args: argparse.Namespace) -> int:
    state = load_json(args.output, {"schema_version": 1, "sitemaps": {}, "version_evidence": {}, "related_pages": []})
    if state.get("schema_version") != 1:
        raise ValueError("unsupported sitemap checkpoint format")
    seeds = args.seed or SEEDS
    queue = sorted(set(seeds))
    processed = set()
    requests = 0
    leads = {version: set(urls) for version, urls in state.get("version_evidence", {}).items()}
    related = set(state.get("related_pages", []))
    while queue and requests < args.limit:
        url = queue.pop(0)
        if not allowed_sitemap(url):
            print(f"skip out-of-scope sitemap: {url}")
            continue
        if url in processed:
            continue
        processed.add(url)
        prior = state["sitemaps"].get(url)
        if prior and not args.recheck:
            queue.extend(prior.get("sitemap_children", []))
            continue
        result = {"checked_at": timestamp(), "url": url, "state": "inconclusive"}
        try:
            response = get_page(url, max_bytes=MAX_XML_BYTES)
            result["http_status"] = response.status
            if response.status == 200:
                kind, urls = parse_xml_sitemap(response.data)
                result.update(state="verified-xml", sha256=sha256_bytes(response.data),
                              url_count=len(urls), sitemap_kind=kind)
                if kind == "sitemapindex":
                    children = sorted({item for item in urls if allowed_sitemap(item)})
                    if len(children) > MAX_CHILD_SITEMAPS:
                        raise ValueError("sitemap lists too many children")
                    result["sitemap_children"] = children
                    queue.extend(children)
                else:
                    found, page_leads = extract_leads(urls)
                    for version, links in found.items():
                        leads.setdefault(version, set()).update(links)
                    related.update(page_leads)
                    result["gpl_versions"] = sorted(found)
                    result["gpl_related_urls"] = len(page_leads)
            elif response.status == 429:
                result["state"] = "rate-limited"
            elif response.status == 403:
                result["state"] = "access-denied"
            else:
                result["state"] = "http-error"
        except (OSError, ValueError) as error:
            result["state"] = "error"
            result["reason"] = str(error)
        state["sitemaps"][url] = result
        state["version_evidence"] = {version: sorted(pages) for version, pages in sorted(leads.items())}
        state["related_pages"] = sorted(related)
        state["updated"] = timestamp()
        atomic_json(args.output, state)
        requests += 1
        print(f"sitemap {requests}/{args.limit}: {url} {result['state']} links={result.get('url_count', 0)} GPL_versions={result.get('gpl_versions', [])}", flush=True)
        if result["state"] in {"rate-limited", "error"}:
            break
        pause(args.delay)
    print(f"processed_requests={requests} checkpointed_sitemaps={len(state['sitemaps'])} versions={sorted(leads)} related={len(related)}", flush=True)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--seed", action="append")
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument("--delay", type=float, default=2.5)
    args = parser.parse_args()
    if args.limit <= 0:
        parser.error("--limit must be positive")
    try:
        return scan(args)
    except (OSError, ValueError) as error:
        print(f"sitemap discovery failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
