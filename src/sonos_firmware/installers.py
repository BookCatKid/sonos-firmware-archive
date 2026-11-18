"""Shared current and historical acquisition of public Sonos installers."""
import json
import re
import shutil
import urllib.error
import urllib.request
import zipfile
from functools import partial
from pathlib import Path
from urllib.parse import urlsplit

from .app_archive import AppSource, preserve
from .archive_index import discover_sources
from .archive_replay import canonical_source, open_capture
from .retry import due
from .storage import atomic_json
from .source_urls import sonos_url, open_advertised, advertised_url

def official_source(url):
    return sonos_url(url) and urlsplit(url).path.lower().endswith(('.exe', '.dmg', '.apk'))


def installer_location(url, extensions, *, advertised_by=None):
    """Recognize an advertised installer without a distribution-host list."""
    try:
        url = canonical_source(url, lambda u: advertised_url(u, advertised_by))
    except ValueError:
        return None
    return url if urlsplit(url).path.lower().endswith(tuple('.'+ext for ext in extensions)) else None


def family(url, platform):
    name = urlsplit(url).path.rsplit('/', 1)[-1]
    protocol = re.fullmatch(r'(\d+\.\d+-\d+)-1-\d+\.(?:exe|dmg)', name, re.I)
    if protocol:
        return 's1' if protocol[1].startswith('57.') else 'unknown'
    if re.search(r'Sonos_57\.', name):
        return 's1'
    if name.startswith('Sonos_'):
        return 's2'
    if platform == 'fireos-android-apk':
        return 'legacy' if name.startswith('SonosAndroidController') else 's2'
    match = re.search(r'SonosDesktopController([^.]*)', name)
    if match is None:
        return 'unknown'
    token = match[1].lower()
    return 's1' if token.startswith('112') else 's2' if token.startswith(('12', '13')) else 'legacy'


def version(url, platform):
    name = urlsplit(url).path.rsplit('/', 1)[-1]
    protocol = re.fullmatch(r'(\d+\.\d+-\d+)-1-\d+\.(?:exe|dmg)', name, re.I)
    if protocol:
        return protocol[1]
    match = re.fullmatch(r'Sonos_(.+)\.(?:exe|dmg|apk)', name, re.I)
    if match:
        return match[1]
    match = re.fullmatch(r'SonosDesktopController([^.]*)\.(?:exe|dmg)', name, re.I)
    if match:
        return 'classic-'+match[1]
    match = re.fullmatch(r'SonosAndroidController(.+)\.apk', name, re.I)
    return match[1] if match else 'unknown'


def release_for(candidate):
    family_name, platform = candidate['family'], candidate['platform']
    if platform == 'fireos-android-apk':
        return 'apps-fireos-'+family_name, f'Sonos {family_name} official Android installers'
    return 'apps-'+family_name+'-'+platform, f'Sonos {family_name} {platform} installers'


def acquire(candidate, destination, *, blocked=None, inspection_required=False):
    if inspection_required and not all(shutil.which(tool) for tool in ('java', 'javac')):
        raise ValueError('APKInspectionUnavailable: Java and javac are required before downloading APKs')
    blocked = set() if blocked is None else blocked
    capture = candidate.get('archive_capture')
    sources = [(candidate['url'], capture)] if capture is not None else [(candidate['url'], None)]
    # Older saved URL observations may offer a replay as a fallback. Explicit
    # timestamped acquisitions above always request that capture directly.
    if capture is None:
        timestamp = candidate.get('wayback_timestamp')
        if not timestamp:
            timestamp = next((row.split(':', 2)[1] for row in candidate.get('sources', [])
                              if row.startswith('wayback-cdx:')), None)
        if timestamp:
            sources.append((candidate['url'], {'timestamp': timestamp}))
    allowed = lambda url: advertised_url(url, candidate.get('advertised_by'))
    last_error = None
    for url, replay in sources:
        host = 'web.archive.org' if replay is not None else urlsplit(url).hostname
        if host in blocked:
            last_error = ValueError('installer source host is paused for this run')
            continue
        try:
            response = (open_capture(url, replay, allowed=allowed, timeout=180) if replay is not None else
                        open_advertised(urllib.request.Request(canonical_source(url, allowed),
                            headers={'User-Agent': 'sonos-firmware-archive/1'}),
                            advertised_by=candidate.get('advertised_by'), timeout=180))
            with response, destination.open('wb') as output:
                if getattr(response, 'status', 200) != 200:
                    raise ValueError('installer source did not return a complete HTTP 200 response')
                if 'html' in response.headers.get('Content-Type', '').lower():
                    raise ValueError('installer source returned an HTML page')
                size = 0
                while block := response.read(1024*1024):
                    size += output.write(block)
                declared = response.headers.get('Content-Length')
                if declared is not None and int(declared) != size:
                    raise ValueError('installer response has a truncated Content-Length')
                fetched = response.geturl() if hasattr(response, 'geturl') else url
            if candidate['platform'] == 'fireos-android-apk' and not zipfile.is_zipfile(destination):
                raise ValueError('downloaded installer is not a ZIP/APK')
            return fetched
        except Exception as error:
            destination.unlink(missing_ok=True)
            last_error = error
            if getattr(error, 'pause_replay', False) or getattr(error, 'code', None) in {403, 429}:
                blocked.add(host)
                error.pause_replay, error.pause_host = True, host
            if isinstance(error, urllib.error.HTTPError):
                error.close()
    raise last_error


def source(*, refresh_hours=168, current_refresh_hours=6, default_platform='', paused_hosts=()):
    from .apk import inspect_official, inspection_revision
    mobile = default_platform == 'fireos-android-apk'
    return AppSource(partial(acquire, blocked=set(paused_hosts), inspection_required=mobile), release_for,
                     'Exact public Sonos installer captures with verified hashes and source provenance.',
                     default_platform=default_platform, refresh_hours=refresh_hours, current_refresh_hours=current_refresh_hours,
                     inspect=inspect_official if mobile else None,
                     inspection_revision=inspection_revision() if mobile else None)


def _candidate(url, kind, *, advertised_by=None, source='public-archive-index'):
    url = canonical_source(url, lambda value: advertised_url(value, advertised_by))
    suffix = urlsplit(url).path.rsplit('.', 1)[-1].lower()
    if suffix not in (('exe', 'dmg') if kind == 'desktop' else ('apk',)):
        return None
    platform = 'fireos-android-apk' if suffix == 'apk' else 'windows' if suffix == 'exe' else 'macos'
    result = {'url': url, 'family': family(url, platform), 'platform': platform,
              'version': version(url, platform), 'sources': [source]}
    if advertised_by:
        result['advertised_by'] = advertised_by
    return result


def manifest_candidates(root, kind):
    """Reuse this checkout's protocol observations; neither input is required."""
    if kind not in {'desktop', 'mobile'}:
        raise ValueError('installer kind must be desktop or mobile')
    root = Path(root)
    candidates, errors = {}, []
    for relative, field in (('data/catalog.json', 'packages'),
                            ('data/discovery/firmware-frontier.json', 'candidates')):
        path = root/relative
        if not path.exists():
            continue
        try:
            document = json.loads(path.read_text())
            rows = document.get(field, [])
            if not isinstance(rows, list):
                raise ValueError('invalid native manifest observations')
        except (OSError, ValueError, AttributeError) as error:
            errors.append({'source': relative, 'error': str(error)})
            continue
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get('source_urls', []), list):
                errors.append({'source': relative, 'error': 'invalid native artifact observation'})
                continue
            urls = [row.get('source_url') or row.get('url'), *row.get('source_urls', [])]
            for url in urls:
                try:
                    if url is not None and not isinstance(url, str):
                        errors.append({'source': relative, 'error': 'invalid native installer URL alias'})
                        continue
                    if not url:
                        continue
                    suffix = urlsplit(url).path.rsplit('.', 1)[-1].lower()
                    if suffix not in (('exe', 'dmg') if kind == 'desktop' else ('apk',)):
                        continue
                    witness = row.get('advertised_by') or row.get('advertised_sources', {}).get(url)
                    candidate = _candidate(url, kind, advertised_by=witness,
                                           source='firmware-observation:'+relative)
                    if candidate['version'] == 'unknown' and isinstance(row.get('version'), str):
                        candidate['version'] = row['version']
                    if candidate['family'] == 'unknown' and isinstance(row.get('family'), str) and row['family'] in {'s1', 's2', 'legacy'}:
                        candidate['family'] = row['family']
                    prior = candidates.setdefault(candidate['url'], candidate)
                    for field in ('family', 'version'):
                        if prior.get(field) in {None, '', 'unknown'} and candidate.get(field) not in {None, '', 'unknown'}:
                            prior[field] = candidate[field]
                    prior['sources'] = sorted(set(prior['sources']) | set(candidate['sources']))
                    if candidate.get('advertised_by'):
                        prior['advertised_by'] = candidate['advertised_by']
                except (ValueError, TypeError, AttributeError) as error:
                    errors.append({'source': relative, 'error': str(error)})
    return {'candidates': list(candidates.values()), 'errors': errors}


def observed_candidates(path, kind):
    """Indexed URLs remain independent live leads, including sibling inference."""
    index = json.loads(path.read_text()) if path.exists() else {}
    candidates = []
    for url in index.get('artifacts', {}):
        try:
            candidate = _candidate(url, kind)
            if candidate:
                candidates.append(candidate)
        except ValueError:
            continue
    return candidates


def archive_history(root, work, repository, kind, *, limit=8, pages=1, refresh_hours=168, current_refresh_hours=6):
    if kind not in {'desktop', 'mobile'}:
        raise ValueError('installer history kind must be desktop or mobile')
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0 for value in (limit, pages)):
        raise ValueError('installer history limits must be positive integers')
    path = root/'data/apps'/f'{kind}-history-index.json'
    previous = json.loads(path.read_text()) if path.exists() else {}
    extensions = ('exe', 'dmg') if kind == 'desktop' else ('apk',)
    index = discover_sources(previous, pages=pages, extensions=extensions, allowed=official_source)
    atomic_json(path, index)
    candidates, errors = [], list(index['errors'])
    for url, row in index['artifacts'].items():
        try:
            candidate = _candidate(url, kind)
        except ValueError as error:
            errors.append({'url': url, 'error': str(error)})
            continue
        if not candidate:
            continue
        for identity, capture in row.get('captures', {}).items():
            candidates.append({**candidate,
                'acquisition_id': 'wayback:'+identity, 'archive_capture': capture,
                'wayback_timestamp': capture['timestamp'], 'wayback_digest': capture.get('digest')})
    discovery = work/'discovery.json'
    atomic_json(discovery, {'status': index['status'], 'errors': errors, 'candidates': candidates})
    paused = any(row.get('http_status') in {403, 429} and not due(row) for row in index['errors'])
    result = preserve(source(default_platform='fireos-android-apk' if kind == 'mobile' else '',
                             refresh_hours=refresh_hours, current_refresh_hours=current_refresh_hours),
                      discovery, root/'data/apps'/f'{kind}-archive.json', repository,
                      root=root, limit=limit, paused_hosts={'web.archive.org'} if paused else ())
    return {'status': result['status'], 'artifact_total': result['artifact_total'],
            'attempted': result['attempted_this_run'], 'errors': result['errors'],
            'pending': result['pending_total'], 'index_status': index['status']}
