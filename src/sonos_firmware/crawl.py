"""Bounded, evidence-seeded update-list traversal. Never guess campaign codes."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections import deque
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .discovery import Candidate, OFFICIAL_FIRMWARE_HOSTS, USER_AGENT, parse_manifest

HOSTS = OFFICIAL_FIRMWARE_HOSTS
PACKAGE = re.compile(r"^(\d+\.\d+-\d+)-1-(\d+)\.upd$")


def official(url: str) -> bool:
    try:
        p = urlsplit(url)
        port = p.port
    except ValueError:
        return False
    return p.scheme == "https" and p.hostname in HOSTS and port in {None, 443} and not p.username and not p.password and p.path.startswith("/firmware/")


def normalize(url: str) -> str:
    return "https://" + url[7:] if url.startswith("http://") else url


def mirror(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme == 'https' and p.hostname == 'raw.githubusercontent.com' and p.path.startswith('/systemcrash/sonos-firmware/')


def catalog_manifest_url(url: str) -> str:
    return url.replace('https://github.com/systemcrash/sonos-firmware/blob/',
                       'https://raw.githubusercontent.com/systemcrash/sonos-firmware/')


def crawl_manifests(catalog: dict, initial: list[dict], seed_urls: list[str],
                    evidence_dir: Path, max_manifests=256, delay=.25, fetch=None) -> dict:
    """Return raw snapshot receipts, source edges and failures, including limits.

    Only explicit seed/history links or the literal sibling update.upm are visited.
    Official-host redirects are checked before following, never after a disclosure.
    """
    evidence_dir.mkdir(parents=True, exist_ok=True)
    queue = deque()
    seen = set()
    bodies = {normalize(m['url']): m['body'] for m in initial if m.get('body') is not None}
    candidates = {}
    directories = set()
    edges = []
    failures = []
    manifests = []
    blocked = False
    attempts = 0

    def add(url, parent, reason):
        url = normalize(url)
        if official(url) or (reason == 'catalog-manifest' and mirror(url)):
            edges.append({'url': url, 'parent': parent, 'reason': reason})
            if url not in seen:
                seen.add(url)
                queue.append(url)
        else:
            failures.append({'url': url, 'parent': parent, 'error': 'unsupported URL or host', 'kind': 'unsupported'})

    def directory(url, version=None):
        url = normalize(url)
        if not official(url):
            return
        path = urlsplit(url).path
        if not path.endswith(('.upd', '.exe', '.dmg', '.bin')):
            return
        folder = url.rsplit('/', 1)[0]
        # Exact filename build wins over the enclosing campaign build.
        m = PACKAGE.match(path.rsplit('/', 1)[-1])
        if m:
            directories.add((m.group(1), folder))
        elif version and re.fullmatch(r'\d+\.\d+-\d+', version) and path.endswith(('.exe', '.dmg')):
            directories.add((version, folder))
        add(folder + '/update.upm', url, 'sibling-manifest')

    for item in initial:
        add(item['url'], 'current-feed', 'current-manifest')
    for item in catalog.get('source_manifests', []):
        add(catalog_manifest_url(item.get('source_url', '')), 'catalog', 'catalog-manifest')
    for item in catalog.get('packages', []):
        directory(item.get('source_url', ''), item.get('version'))
    for seed in seed_urls:
        url = seed['url'] if isinstance(seed, dict) else seed
        if isinstance(seed, dict) and all(k in seed for k in ('version', 'package_model', 'filename')) and official(normalize(url)):
            c = Candidate(seed['version'], seed['package_model'], normalize(url), 'verified-public-seed', seed['filename'])
            candidates[c.url] = c
        if urlsplit(url).path.endswith('.upm'):
            add(url, 'public-evidence-seed', 'seed-manifest')
        else:
            directory(url)

    class Guard(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            if not official(normalize(newurl)):
                raise ValueError('redirect outside official firmware hosts')
            return super().redirect_request(req, fp, code, msg, headers, normalize(newurl))
    opener = urllib.request.build_opener(Guard())

    def retrieve(url):
        if fetch:
            return fetch(url)
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with opener.open(req, timeout=15) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError('manifest exceeds 2MB limit')
            return body

    while queue and attempts < max_manifests:
        url = queue.popleft()
        attempts += 1
        snapshot = None
        digest = None
        try:
            body = bodies[url] if url in bodies else retrieve(url)
            digest = hashlib.sha256(body).hexdigest()
            snapshot = evidence_dir / (hashlib.sha256(url.encode()).hexdigest()[:16] + '-' + digest[:16] + '.upm')
            snapshot.write_bytes(body)  # retain unsupported/malformed bodies too
            metadata, items = parse_manifest(snapshot, source_url=url)
            manifests.append({'url': url, 'sha256': digest, 'bytes': len(body), 'snapshot': str(snapshot),
                              'metadata': metadata, 'candidate_total': len(items)})
            for c in items:
                if not official(c.url):
                    failures.append({'url': c.url, 'parent': url, 'kind': 'unsupported', 'error': 'non-official artifact URL'})
                    continue
                candidates[c.url] = c
                edges.append({'url': c.url, 'parent': url, 'reason': c.source})
                directory(c.url, c.version)
            for link in metadata.get('manifest_links', []):
                add(link, url, 'manifest-link')
        except Exception as error:
            status = getattr(error, 'code', None)
            failures.append({'url': url, 'attempted': True, 'status': status,
                             'kind': 'not-found' if status == 404 else 'error', 'error': str(error),
                             'snapshot': str(snapshot) if snapshot else None, 'sha256': digest})
            if status in {403, 429}:
                blocked = True
                break
        if url not in bodies and delay:
            time.sleep(delay)
    return {'manifests': manifests, 'candidates': list(candidates.values()),
            'directories': [{'version': v, 'directory': d} for v,d in sorted(directories)],
            'edges': edges, 'failures': failures, 'blocked': blocked,
            'limits_reached': bool(queue), 'pending_manifest_urls': list(queue),
            'scope': 'only evidenced URLs and literal sibling update.upm; no opaque-code guessing'}
