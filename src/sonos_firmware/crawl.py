"""Bounded, evidence-seeded update-list traversal. Never guess campaign codes."""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.error
import urllib.request
from collections import deque
from dataclasses import replace
from pathlib import Path
from urllib.parse import urljoin, urlsplit, urlunsplit

from .discovery import Candidate, USER_AGENT, parse_manifest, official_firmware_url as official
from .source_urls import advertised_url, open_advertised, sonos_url
from .source_links import discover_urls, distribution_urls, PAYLOAD_SUFFIXES

PACKAGE = re.compile(r"^(\d+\.\d+-\d+)-1-(\d+)\.upd$", re.I)
DOCUMENT_SUFFIXES = ('.html', '.htm', '.js', '.mjs', '.json', '.php', '.asp', '.aspx', '.jsp', '.cgi')
STATIC_SUFFIXES = ('.css', '.png', '.jpg', '.jpeg', '.gif', '.svg', '.ico', '.woff', '.woff2', '.ttf')


def landing_url(url):
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return False
    return path.endswith(DOCUMENT_SUFFIXES) or '.' not in path.rsplit('/', 1)[-1]


def normalize(url: str) -> str:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return url
    return parsed._replace(scheme='https' if parsed.scheme == 'http' else parsed.scheme, fragment='').geturl()


def mirror(url: str) -> bool:
    p = urlsplit(url)
    return p.scheme == 'https' and p.hostname == 'raw.githubusercontent.com' and p.path.startswith('/systemcrash/sonos-firmware/')


def catalog_manifest_url(url: str) -> str:
    return url.replace('https://github.com/systemcrash/sonos-firmware/blob/',
                       'https://raw.githubusercontent.com/systemcrash/sonos-firmware/')



def rejected_url_kind(url: str) -> str:
    """Classify a rejected non-official URL.

    Rejected manifest, download and landing-page URLs remain coverage gaps.
    Other link types can be outside this traversal's scope, such as static
    images that contain no executable or document links.
    """
    try:
        path = urlsplit(url).path.lower()
    except ValueError:
        return 'unsupported'
    if path.endswith(('.upm', *PAYLOAD_SUFFIXES)) or landing_url(url):
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
    manifests, documents = [], []
    blocked = bool(paused_hosts)
    attempts = 0
    blocked_hosts = set(paused_hosts)
    deferred = []
    authorities = {}

    def authority(url):
        return url if sonos_url(url) else authorities.get(url)

    def add(url, parent, reason, advertised_by=None):
        url = normalize(url)
        witness = advertised_by or authority(parent)
        if advertised_url(url, witness) or (reason == 'catalog-manifest' and mirror(url)):
            if witness:
                authorities[url] = witness
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
        if not path.lower().endswith(PAYLOAD_SUFFIXES):
            return
        parsed = urlsplit(url)
        folder = parsed._replace(path=path.rsplit('/', 1)[0], query='', fragment='').geturl()
        # Exact filename build wins over the enclosing campaign build.
        m = PACKAGE.match(path.rsplit('/', 1)[-1])
        if m:
            directories.add((m.group(1), folder))
        elif version and re.fullmatch(r'\d+\.\d+-\d+', version) and path.endswith(('.exe', '.dmg')):
            directories.add((version, folder))
        add(folder + '/update.upm', url, 'sibling-manifest')

    def artifact(url, parent, reason):
        url = normalize(url)
        if not advertised_url(url, authority(parent)):
            failures.append({'url': url, 'parent': parent, 'kind': 'unsupported',
                             'error': 'download link outside Sonos source domains'})
            return
        filename = urlsplit(url).path.rsplit('/', 1)[-1]
        match = PACKAGE.fullmatch(filename)
        candidate = Candidate(match[1] if match else 'unknown', int(match[2]) if match else None,
                              url, reason, filename, authority(parent))
        candidates.setdefault(url, candidate)
        edges.append({'url': url, 'parent': parent, 'reason': reason})
        directory(url, candidate.version)

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
        add(item['url'], 'current-feed', 'current-manifest', item.get('advertised_by'))
    for item in (catalog.get('source_manifests', []) if include_history else []):
        add(catalog_manifest_url(item.get('source_url', '')), 'catalog', 'catalog-manifest', item.get('advertised_by'))
    for item in (catalog.get('packages', []) if include_history else []):
        directory(item.get('source_url', ''), item.get('version'))
    for seed in (seed_urls if include_history else []):
        url = seed['url'] if isinstance(seed, dict) else seed
        if isinstance(seed, dict) and all(k in seed for k in ('version', 'package_model', 'filename')) and official(normalize(url)):
            c = Candidate(seed['version'], seed['package_model'], normalize(url), 'verified-public-seed', seed['filename'])
            candidates[c.url] = c
        if urlsplit(url).path.lower().endswith('.upm') or landing_url(url):
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
        incoming = (opener.open(req, timeout=15) if mirror(url) else
                    open_advertised(req, advertised_by=authority(url), timeout=15))
        with incoming as response:
            if landing_url(url) and urlsplit(response.url).path.lower().endswith(PAYLOAD_SUFFIXES):
                return None, response.url, None
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
            if (location or resolved != url) and not advertised_url(canonical, authority(url)):
                raise ValueError('manifest location outside official firmware hosts')
            if canonical != url:
                if landing_url(url) and urlsplit(canonical).path.lower().endswith(PAYLOAD_SUFFIXES):
                    artifact(canonical, url, 'landing-page-redirect')
                    continue
                bodies.setdefault(canonical, body)
                add(canonical, url, 'content-location' if location else 'redirect')
            digest = hashlib.sha256(body).hexdigest()
            page = landing_url(canonical) and not re.search(rb'<update_manifest(?:\s|>)', body)
            suffix = (next((ext for ext in ('.js', '.mjs', '.json') if urlsplit(canonical).path.lower().endswith(ext)), '.html')
                      if page else '.upm')
            snapshot = evidence_dir / (hashlib.sha256(url.encode()).hexdigest()[:16] + '-' + digest[:16] + suffix)
            snapshot.write_bytes(body)  # retain unsupported/malformed bodies too
            if page:
                advertised = discover_urls(canonical, body)
                links = sorted(distribution_urls(canonical, body))
                documents.append({'url': canonical, 'request_url': url, 'advertised_by': authority(url), 'sha256': digest, 'bytes': len(body),
                                  'snapshot': str(snapshot), 'document_type': 'landing-page',
                                  'metadata': {'format': suffix.removeprefix('.'), 'links': links, 'warnings': [],
                                               'out_of_scope_link_total': len(advertised-set(links)),
                                               'link_scope': 'software-distribution'}})
                actionable = 0
                for link in links:
                    try:
                        path = urlsplit(link).path.lower()
                    except ValueError:
                        failures.append({'url': link, 'parent': canonical, 'kind': 'unsupported',
                                         'error': 'landing page advertises a malformed URL'})
                        continue
                    if path.endswith(PAYLOAD_SUFFIXES):
                        artifact(link, canonical, 'landing-page-link')
                        actionable += 1
                    elif path.endswith('.upm') or landing_url(link):
                        add(link, canonical, 'landing-page-link')
                        actionable += 1
                    elif not path.endswith(STATIC_SUFFIXES):
                        failures.append({'url': link, 'parent': canonical, 'kind': 'unsupported',
                                         'error': 'landing page advertises an unsupported link format'})
                if not actionable:
                    failures.append({'url': canonical, 'parent': url, 'kind': 'unsupported',
                                     'error': 'landing page has no traversable download or document links'})
                continue
            metadata, items = parse_manifest(snapshot, source_url=canonical)
            metadata['document_links'] = sorted({normalize(c.url) for c in items if landing_url(c.url)})
            manifests.append({'url': canonical, 'request_url': url, 'advertised_by': authority(url), 'sha256': digest, 'bytes': len(body), 'snapshot': str(snapshot),
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
                if landing_url(c.url):
                    add(c.url, url, 'manifest-landing-page')
                    continue
                if not advertised_url(c.url, authority(canonical)):
                    failures.append({'url': c.url, 'parent': url, 'kind': rejected_url_kind(c.url), 'error': 'non-official artifact URL'})
                    continue
                candidates[c.url] = replace(c, advertised_by=authority(canonical))
                edges.append({'url': c.url, 'parent': url, 'reason': c.source})
                directory(c.url, c.version)
            for link in (metadata.get('manifest_links', []) if include_history else []):
                add(link, url, 'manifest-link')
            for link in metadata.get('landing_links', []):
                add(link, url, 'manifest-store-link')
        except Exception as error:
            status = getattr(error, 'code', None)
            failures.append({'url': url, 'attempted': True, 'status': status,
                             'kind': 'not-found' if status == 404 else 'error', 'error': str(error),
                             'snapshot': str(snapshot) if snapshot else None, 'sha256': digest})
            if status in {403, 429}:
                blocked = True
                blocked_hosts.add(urlsplit(url).hostname)
        finally:
            if url not in bodies and delay:
                time.sleep(delay)
    return {'manifests': manifests, 'documents': documents, 'candidates': list(candidates.values()),
            'directories': [{'version': v, 'directory': d} for v,d in sorted(directories)],
            'edges': edges, 'failures': failures, 'blocked': blocked,
            'limits_reached': bool(queue), 'pending_manifest_urls': list(queue) + deferred,
            'blocked_hosts': sorted(blocked_hosts), 'advertised_sources': authorities,
            'scope': 'manifest and landing-page links, base URLs, content locations, package siblings and channel counterparts; no opaque-code guessing'}


def discovery_incomplete(graph: dict, manifests: list, challenged: bool) -> bool:
    """Fail closed on real discovery gaps; ignore out-of-scope policy rejections.

    Incomplete when probing was challenged, the crawl was blocked or capped,
    a fetch/parse failed (anything but a clean 404), an in-scope artifact or
    manifest URL sat on an unrecognized host ('unsupported'), or a manifest
    carried an unrecognized-structure warning. Landing pages and unsupported
    download locations remain gaps; unrelated static resources can be excluded.
    """
    if challenged or graph.get("blocked") or graph.get("limits_reached"):
        return True
    if any(f["kind"] not in ("not-found", "out-of-scope") for f in graph["failures"]):
        return True
    return any(
        any("unrecognized" in w or "needs manifest source" in w for w in m["metadata"].get("warnings", []))
        for m in manifests
    )
