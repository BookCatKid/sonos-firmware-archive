#!/usr/bin/env python3
"""Discover historical Sonos Windows/macOS installers from public indexes.

The script reads the current and historical WinGet/Homebrew manifests, derives
same-release cross-platform Sonos CDN URLs, probes availability, and writes a
machine-readable candidate list. It downloads no installer bodies.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
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
import yaml
from sonos_firmware.storage import atomic_json
from sonos_firmware.installers import family as installer_family, version as installer_version, observed_candidates, installer_location, manifest_candidates
from sonos_firmware.source_urls import open_advertised, sonos_url, advertised_url, curl_source_options
from sonos_firmware.archive_replay import canonical_source
from sonos_firmware.package_history import ManifestHistory

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


GH_API_ATTEMPTS = 4
GH_SOURCE_PAUSE = None


class GitHubSourcePaused(RuntimeError):
    pass


def gh_output(cmd: list[str]) -> str:
    global GH_SOURCE_PAUSE
    if GH_SOURCE_PAUSE:
        raise GH_SOURCE_PAUSE
    error = None
    for attempt in range(GH_API_ATTEMPTS):
        try:
            return subprocess.check_output(cmd, text=True, stderr=subprocess.PIPE)
        except subprocess.CalledProcessError as caught:
            error = caught
            status = re.search(r'\bHTTP (\d{3})\b', caught.stderr or '')
            if status and int(status[1]) in {401, 403, 429}:
                GH_SOURCE_PAUSE = GitHubSourcePaused(f'GitHub metadata source paused after HTTP {status[1]}')
                raise GH_SOURCE_PAUSE from caught
            if status and int(status[1]) in {404, 422}:
                raise RuntimeError((caught.stderr or str(caught)).strip()) from caught
            if attempt < GH_API_ATTEMPTS - 1:
                time.sleep(min(2 ** attempt, 30))
    raise RuntimeError((error.stderr or str(error)).strip()) from error


def gh_json(endpoint: str) -> object:
    return json.loads(gh_output(["gh", "api", endpoint]))


def gh_lines(endpoint: str, jq: str) -> list[str]:
    return gh_output(
        ["gh", "api", "--paginate", endpoint, "--jq", jq]
    ).splitlines()


def add_candidate(target: dict, *, family: str, platform: str, version: str,
                  url: str, sha256: str | None, source: str, advertised_by=None) -> None:
    original_url = url
    try:
        url = canonical_source(url, lambda u: advertised_url(u, advertised_by))
    except ValueError:
        pass  # Retain unsupported leads for the probe's explicit error report.
    # A WinGet package spans the pre-split and S1/S2 eras, so its package name
    # alone is not a reliable family label for historical filenames.
    filename = urllib.parse.urlsplit(url).path.rsplit('/', 1)[-1]
    if filename.startswith("SonosDesktopController") or filename.startswith("Sonos_"):
        family = family_for_wayback_url(url)
    digest = sha256.lower() if sha256 else None
    key = (family, platform, url, digest)
    item = target.setdefault(key, {
        "family": family, "platform": platform, "version": version,
        "url": url, "expected_sha256": digest,
        "sources": [],
    })
    if source not in item["sources"]:
        item["sources"].append(source)
    if original_url not in item.setdefault('advertised_urls', []):
        item['advertised_urls'].append(original_url)
    if advertised_by:
        item['advertised_by'] = advertised_by
    if digest:
        item['acquisition_id'] = 'package-metadata:'+hashlib.sha256(
            json.dumps([url, digest]).encode()).hexdigest()


def gh_tree(repository, revision):
    result = gh_json(f'repos/{repository}/git/trees/{urllib.parse.quote(revision, safe="")}')
    if not isinstance(result, dict) or result.get('truncated') is not False or not isinstance(result.get('tree'), list):
        raise ValueError(f'incomplete Git tree: {repository}@{revision}')
    return result['tree']


def gh_directory(repository, path):
    branch = gh_json(f'repos/{repository}')['default_branch']
    entries = gh_tree(repository, branch)
    for part in path.split('/'):
        node = next((row for row in entries if row.get('type') == 'tree' and row.get('path') == part), None)
        if node is None:
            raise ValueError(f'Git directory not found: {repository}/{path}')
        entries = gh_tree(repository, node['sha'])
    return entries


def winget_candidates(target: dict) -> list[dict]:
    repository = 'microsoft/winget-pkgs'
    packages = gh_directory(repository, 'manifests/s/Sonos')
    errors = []
    for package in packages:
        if package.get('type') != 'tree':
            continue
        family = 's1' if package['path'].lower().startswith('s1') else 'unknown'
        try:
            versions = gh_tree(repository, package['sha'])
        except GitHubSourcePaused as error:
            errors.append({'source': f'winget:{package["path"]}', 'error': str(error)})
            return errors
        except Exception as error:
            errors.append({'source': f'winget:{package["path"]}', 'error': str(error)})
            continue
        for version in versions:
            if version.get('type') != 'tree':
                continue
            source = f'winget:{package["path"]}/{version["path"]}'
            try:
                files = gh_tree(repository, version['sha'])
            except GitHubSourcePaused as error:
                errors.append({'source': source, 'error': str(error)})
                return errors
            except Exception as error:
                errors.append({'source': source, 'error': str(error)})
                continue
            for manifest in files:
                if manifest.get('type') != 'blob' or not manifest['path'].endswith('.installer.yaml'):
                    continue
                try:
                    payload = gh_json(f'repos/{repository}/git/blobs/{manifest["sha"]}')
                    # BaseLoader keeps identifiers and digests as strings and
                    # never constructs objects from YAML tags.
                    document = yaml.load(base64.b64decode(payload['content']).decode(), Loader=yaml.BaseLoader)
                    installers = document.get('Installers', [document])
                    if not isinstance(installers, list) or not installers:
                        raise ValueError('installer manifest has no installer records')
                    for installer in installers:
                        try:
                            url = installer.get('InstallerUrl', document.get('InstallerUrl'))
                            digest = installer.get('InstallerSha256', document.get('InstallerSha256'))
                            if not isinstance(url, str) or not url:
                                raise ValueError('installer record has no URL')
                            if digest is not None and (not isinstance(digest, str) or not re.fullmatch(r'[0-9a-fA-F]{64}', digest)):
                                errors.append({'source': source, 'error': 'installer record has an invalid SHA-256', 'url': url})
                                digest = None
                            add_candidate(target, family=family, platform='windows', version=version['path'],
                                          url=url, sha256=digest, source=source+':'+manifest['sha'],
                                          advertised_by={'repository': repository, 'git_blob': manifest['sha'],
                                              'path': f'manifests/s/Sonos/{package["path"]}/{version["path"]}/{manifest["path"]}'})
                        except Exception as error:
                            errors.append({'source': source, 'error': str(error)})
                except GitHubSourcePaused as error:
                    errors.append({'source': source, 'error': str(error)})
                    return errors
                except Exception as error:
                    errors.append({'source': source, 'error': str(error)})
    return errors


def winget_history(target, path, observations, limit, refresh_hours):
    history = ManifestHistory(path, gh_json, observations=observations)
    for item in history.advance(limit=limit, refresh_hours=refresh_hours):
        for source in item['sources']:
            add_candidate(target, family='unknown', platform='windows', version=item['version'],
                          url=item['url'], sha256=item['expected_sha256'], source=source,
                          advertised_by=item.get('advertised_by'))
    return {key: history.state[key] for key in ('status', 'pending_total', 'errors')}


def homebrew_candidates(target: dict) -> list[dict]:
    errors = []
    for path, family in HOMEBREW_PATHS.items():
        try:
            commits = gh_lines(
                f"repos/Homebrew/homebrew-cask/commits?path={path}&per_page=100", ".[].sha"
            )
        except GitHubSourcePaused as error:
            errors.append({'source': f'homebrew:{path}', 'error': str(error)})
            return errors
        except Exception as error:
            errors.append({'source': f'homebrew:{path}', 'error': str(error)})
            continue
        for sha in commits:
            raw_url = f"https://raw.githubusercontent.com/Homebrew/homebrew-cask/{sha}/{path}"
            try:
                with urllib.request.urlopen(raw_url, timeout=20) as response:
                    text = response.read().decode()
            except (urllib.error.HTTPError, urllib.error.URLError, OSError, UnicodeError) as error:
                if getattr(error, 'code', None) != 404:
                    errors.append({'source': raw_url, 'error': str(error)})
                if isinstance(error, urllib.error.HTTPError):
                    error.close()
                if getattr(error, 'code', None) in {401, 403, 429}:
                    return errors
                continue
            try:
                version_match = re.search(r'''^\s*version\s+(['"])([^'"\n]+)\1''', text, re.M)
                version_text = version_match[2] if version_match else 'latest' if re.search(r'^\s*version\s+:latest\b', text, re.M) else None
                url_match = re.search(r'''^\s*url\s+(['"])([^'"\n]+)\1''', text, re.M)
                if version_text is None or url_match is None:
                    raise ValueError('unsupported Homebrew version or URL declaration')
                parts = version_text.split(',')
                if url_match[1] == "'" and '#{' in url_match[2]:
                    raise ValueError('single-quoted Homebrew URL cannot interpolate a version')
                url = url_match[2].replace('#{version.csv.first}', parts[0])
                if len(parts) > 1:
                    url = url.replace('#{version.csv.second}', parts[1])
                url = url.replace('#{version.no_dots}', version_text.replace('.', '')).replace('#{version}', version_text)
                if '#{' in url:
                    raise ValueError('unsupported Homebrew URL interpolation')
                hash_match = re.search(r'''^\s*sha256\s+(['"])([0-9a-fA-F]{64})\1''', text, re.M)
                add_candidate(target, family=family, platform='macos', version=parts[0],
                              url=url, sha256=hash_match[2] if hash_match else None,
                              source=f'homebrew-history:{sha}:{path}',
                              advertised_by={'repository': 'Homebrew/homebrew-cask', 'path': path, 'git_commit': sha})
            except Exception as error:
                errors.append({'source': raw_url, 'error': str(error)})
    return errors


def family_for_wayback_url(url: str) -> str:
    return installer_family(url, "windows")


def version_for_url(url: str) -> str:
    return installer_version(url, "windows")


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def check_direct_redirects(*, include_history: bool = True) -> list[dict]:
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
                current = canonical_source(current, lambda u: advertised_url(u, url))
                session.curl_options.update(curl_source_options(current, url))
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
                if installer_location(next_url, ('exe', 'dmg'), advertised_by=url):
                    location = installer_location(next_url, ('exe', 'dmg'), advertised_by=url)
                    source = "live-redirect"
                    break
                current = next_url
        except Exception as error:
            results.append({"name": name, "url": url, "http_status": None,
                            "location": None, "error": str(error)})
            continue
        if not location and include_history:
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
                    if installer_location(original, ('exe', 'dmg'), advertised_by=url):
                        location = installer_location(original, ('exe', 'dmg'), advertised_by=url)
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
    if not sonos_url(url):
        return None
    parsed = urllib.parse.urlsplit(url)
    protocol = re.fullmatch(r'(.*?/)(\d+\.\d+-\d+)-1-(\d+)\.(exe|dmg)', parsed.path, re.I)
    if protocol:
        folder, build, model, suffix = protocol.groups()
        if candidate['platform'] == 'windows' and (model, suffix.lower()) == ('3', 'exe'):
            platform, model, suffix = 'macos', '4', 'dmg'
        elif candidate['platform'] == 'macos' and (model, suffix.lower()) == ('4', 'dmg'):
            platform, model, suffix = 'windows', '3', 'exe'
        else:
            return None
        path = folder+build+'-1-'+model+'.'+suffix
        return platform, parsed._replace(path=path).geturl()
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
        with open_advertised(request, advertised_by=item.get('advertised_by'), timeout=30) as response:
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
    parser.add_argument("--output", type=Path, required=True, metavar="FILE.json",
                        help="write the discovery report to this JSON file; parent directories are created")
    parser.add_argument('--archive-root', type=Path, default=Path(__file__).resolve().parents[1],
                        help="archive checkout whose own manifest observations supply optional installer leads")
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--current-only", action="store_true")
    parser.add_argument('--history-state', type=Path)
    parser.add_argument('--history-limit', type=int, default=64)
    parser.add_argument('--observations', type=Path)
    parser.add_argument('--refresh-hours', type=float, default=6)
    args = parser.parse_args()
    candidates: dict = {}
    errors = []
    wayback = {"provider": "shared-installer-history"}
    package_history = {'status': 'skipped', 'pending_total': 0, 'errors': []}
    if not args.current_only:
        native = manifest_candidates(args.archive_root, 'desktop')
        errors.extend(native['errors'])
        for item in native['candidates']:
            for source in item['sources']:
                add_candidate(candidates, family=item['family'], platform=item['platform'], version=item['version'],
                              url=item['url'], sha256=None, source=source, advertised_by=item.get('advertised_by'))
        try:
            package_history = winget_history(candidates, args.history_state or args.output.parent/'desktop-package-history.json',
                                             args.observations, args.history_limit, args.refresh_hours)
            errors.extend(package_history['errors'])
        except Exception as error:
            errors.append({'source': 'winget-history', 'error': str(error)})
        for item in observed_candidates(args.output.parent/'desktop-history-index.json', 'desktop'):
            add_candidate(candidates, family=item['family'], platform=item['platform'], version=item['version'],
                          url=item['url'], sha256=None, source='saved-installer-index')
        for name, source in [('winget', winget_candidates), ('homebrew', homebrew_candidates)]:
            try:
                discovered_errors = source(candidates)
                if isinstance(discovered_errors, list):
                    errors.extend(discovered_errors)
            except Exception as error:
                errors.append({'source': name, 'error': str(error)})
    direct_redirects = check_direct_redirects(include_history=not args.current_only)
    for redirect in direct_redirects:
        url = redirect.get("location")
        if not url:
            continue
        family, platform = redirect["name"].split("-", 1)
        add_candidate(
            candidates, family=family, platform=platform,
            version=version_for_url(url), url=url, sha256=None,
            source=f"official-direct:{redirect['name']}:{redirect['location_source']}",
            advertised_by=redirect["url"],
        )
    current_urls = {row['location'] for row in direct_redirects if row.get('location_source') == 'live-redirect'}
    for item in list(candidates.values()):
        other = counterpart(item)
        if other:
            platform, url = other
            add_candidate(candidates, family=item["family"], platform=platform,
                          version=item["version"], url=url, sha256=None,
                          source="derived-cross-platform")
    for item in candidates.values():
        item['current'] = item['url'] in current_urls
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        results = list(pool.map(probe, candidates.values()))
    results.sort(key=lambda x: (x["family"], x["platform"], x["version"], x["url"]))
    errors.extend({'source': x['url'], 'error': x.get('probe_error') or str(x.get('http_status'))}
                  for x in results if x.get('probe_error') or x.get('http_status') in (403, 429)
                  or (x.get('http_status') or 0) >= 500)
    errors.extend({'source': x.get('url'), 'error': 'current redirect unresolved; historical fallback is not a live observation'}
                  for x in direct_redirects if not x.get('location') or x.get('location_source') != 'live-redirect')
    document = {
        'status': 'partial' if errors or package_history['status'] == 'partial' else 'ok', 'errors': errors,
        'package_history': package_history,
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "sources": ["official-current-redirects"] if args.current_only else ["microsoft/winget-pkgs", "Homebrew/homebrew-cask", "Sonos CDN HEAD"],
        "current_only": args.current_only,
        "direct_redirects": direct_redirects,
        "wayback": wayback,
        "candidate_total": len(results),
        "available_total": sum(x["http_status"] == 200 for x in results),
        "candidates": results,
    }
    atomic_json(args.output, document)
    print(f"candidates={len(results)} available={document['available_total']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
