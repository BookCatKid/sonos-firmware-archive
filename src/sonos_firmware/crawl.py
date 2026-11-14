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
from urllib.parse import urljoin, urlsplit, urlunsplit

from .discovery import Candidate, USER_AGENT, parse_manifest, official_firmware_url as official

PACKAGE = re.compile(r"^(\d+\.\d+-\d+)-1-(\d+)\.upd$")


def normalize(url: str) -> str:
    return "https://" + url[7:] if url.startswith("http://") else url


def mirror(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme == 'https' and p.hostname == 'raw.githubusercontent.com' and p.path.startswith('/systemcrash/sonos-firmware/')


def catalog_manifest_url(url: str) -> str:
    return url.replace('https://github.com/systemcrash/sonos-firmware/blob/',
                       'https://raw.githubusercontent.com/systemcrash/sonos-firmware/')



def rejected_url_kind(url: str) -> str:
    """Classify a rejected non-official URL.

    A URL that is not manifest- or package-shaped (e.g. a beta-app HTML
    page) can never be archivable firmware: rejecting it is out-of-scope
    policy, not a discovery gap. An artifact- or manifest-shaped URL on an
    unrecognized host stays fail-closed 'unsupported': it may be host
    discovery the archive must not silently drop.
    """
    path = urlsplit(url).path.lower()
    if path.endswith(('.upm', '.upd', '.exe', '.dmg', '.bin')):
        return 'unsupported'
    return 'out-of-scope'

def crawl_manifests(catalog: dict, initial: list[dict], seed_urls: list[str],
                    evidence_dir: Path, max_manifests=256, delay=.25, fetch=None, include_history=True,
                    explore_channels=False, deadline=None, paused_hosts=()) -> dict:
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
    blocked = bool(paused_hosts)
    attempts = 0
    blocked_hosts = set(paused_hosts)
    deferred = []

    def add(url, parent, reason):
        url = normalize(url)
        if official(url) or (reason == 'catalog-manifest' and mirror(url)):
            edges.append({'url': url, 'parent': parent, 'reason': reason})
            if url not in seen:
                seen.add(url)
                queue.append(url)
        else:
            failures.append({'url': url, 'parent': parent, 'error': 'unsupported URL or host',
                             'kind': rejected_url_kind(url)})

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

    def channels(url):
        if not explore_channels or not official(url):
            return
        p = urlsplit(url)
        parts = p.path.split('/')
        for index, part in enumerate(parts):
            if part in {'Prod', 'Dev'}:
                swapped = list(parts)
                swapped[index] = 'Dev' if part == 'Prod' else 'Prod'
                add(urlunsplit((p.scheme, p.netloc, '/'.join(swapped), p.query, '')), url, 'channel-counterpart')
                break

    for item in initial:
        add(item['url'], 'current-feed', 'current-manifest')
    for item in (catalog.get('source_manifests', []) if include_history else []):
        add(catalog_manifest_url(item.get('source_url', '')), 'catalog', 'catalog-manifest')
    for item in (catalog.get('packages', []) if include_history else []):
        directory(item.get('source_url', ''), item.get('version'))
    for seed in (seed_urls if include_history else []):
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
            result = fetch(url)
            return (result, url, None) if isinstance(result, bytes) else result
        req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
        with opener.open(req, timeout=15) as response:
            body = response.read(2_000_001)
            if len(body) > 2_000_000:
                raise ValueError('manifest exceeds 2MB limit')
            return body, response.url, response.headers.get('Content-Location')

    while queue and attempts < max_manifests:
        if deadline is not None and time.monotonic() >= deadline:
            break
        url = queue.popleft()
        if urlsplit(url).hostname in blocked_hosts:
            deferred.append(url)
            continue
        attempts += 1
        snapshot = None
        digest = None
        try:
            body, resolved, location = (bodies[url], url, None) if url in bodies else retrieve(url)
            canonical = normalize(urljoin(resolved, location)) if location else resolved
            if (location or resolved != url) and not official(canonical):
                raise ValueError('manifest location outside official firmware hosts')
            if canonical != url:
                bodies.setdefault(canonical, body)
                add(canonical, url, 'content-location' if location else 'redirect')
            digest = hashlib.sha256(body).hexdigest()
            snapshot = evidence_dir / (hashlib.sha256(url.encode()).hexdigest()[:16] + '-' + digest[:16] + '.upm')
            snapshot.write_bytes(body)  # retain unsupported/malformed bodies too
            metadata, items = parse_manifest(snapshot, source_url=canonical)
            manifests.append({'url': canonical, 'request_url': url, 'sha256': digest, 'bytes': len(body), 'snapshot': str(snapshot),
                              'metadata': metadata, 'candidate_total': len(items)})
            channels(canonical)
            base = metadata.get('base_url')
            if base:
                path = urlsplit(base).path
                if '^' in path:
                    sibling = base.split('^', 1)[0].rstrip('/') + '/update.upm'
                    add(sibling, canonical, 'manifest-base')
                elif path.endswith('.upm'):
                    add(base, canonical, 'manifest-base')
            for c in items:
                if not official(c.url):
                    failures.append({'url': c.url, 'parent': url, 'kind': rejected_url_kind(c.url), 'error': 'non-official artifact URL'})
                    continue
                candidates[c.url] = c
                edges.append({'url': c.url, 'parent': url, 'reason': c.source})
                directory(c.url, c.version)
            for link in (metadata.get('manifest_links', []) if include_history else []):
                add(link, url, 'manifest-link')
        except Exception as error:
            status = getattr(error, 'code', None)
            failures.append({'url': url, 'attempted': True, 'status': status,
                             'kind': 'not-found' if status == 404 else 'error', 'error': str(error),
                             'snapshot': str(snapshot) if snapshot else None, 'sha256': digest})
            if status in {403, 429}:
                blocked = True
                blocked_hosts.add(urlsplit(url).hostname)
        if url not in bodies and delay:
            time.sleep(delay)
    return {'manifests': manifests, 'candidates': list(candidates.values()),
            'directories': [{'version': v, 'directory': d} for v,d in sorted(directories)],
            'edges': edges, 'failures': failures, 'blocked': blocked,
            'limits_reached': bool(queue), 'pending_manifest_urls': list(queue) + deferred,
            'blocked_hosts': sorted(blocked_hosts),
            'scope': 'manifest links, base URLs, content locations, package siblings and channel counterparts; no opaque-code guessing'}


def discovery_incomplete(graph: dict, manifests: list, challenged: bool) -> bool:
    """Fail closed on real discovery gaps; ignore out-of-scope policy rejections.

    Incomplete when probing was challenged, the crawl was blocked or capped,
    a fetch/parse failed (anything but a clean 404), an in-scope artifact or
    manifest URL sat on an unrecognized host ('unsupported'), or a manifest
    carried an unrecognized-structure warning. 'out-of-scope' entries are
    deterministic rejections of links that are not manifests or packages at
    all (e.g. beta-app HTML pages) and can never hide archivable firmware.
    """
    if challenged or graph.get("blocked") or graph.get("limits_reached"):
        return True
    if any(f["kind"] not in ("not-found", "out-of-scope") for f in graph["failures"]):
        return True
    return any(
        any("unrecognized" in w or "needs manifest source" in w for w in m["metadata"].get("warnings", []))
        for m in manifests
    )
