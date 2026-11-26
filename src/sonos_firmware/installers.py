"""Shared current and historical acquisition of public Sonos installers."""
import json
import re
import shutil
import hashlib
import plistlib
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import zipfile
from functools import partial
from pathlib import Path
from xml.parsers.expat import ExpatError
from urllib.parse import urlsplit

from .app_archive import AppSource, preserve
from .archive_index import discover_archives as discover_sources, paused_index_hosts
from .archive_replay import canonical_source, open_capture, capture_host
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
        host = capture_host(replay) if replay is not None else urlsplit(url).hostname
        if host in blocked:
            last_error = ValueError('installer source host is paused for this run')
            continue
        try:
            response = (open_capture(url, replay, allowed=allowed, timeout=900) if replay is not None else
                        open_advertised(urllib.request.Request(canonical_source(url, allowed),
                            headers={'User-Agent': 'sonos-firmware-archive/1'}),
                            advertised_by=candidate.get('advertised_by'), timeout=900))
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


def _archive_output(command, destination, *, limit, seconds=600, allow_partial=False):
    """Read an archive through stdout; never execute or extract its member paths.

    A disk image can contain members the decoder refuses while still serving the
    one requested here, in which case it reports failure after writing complete
    output. Callers that parse the result pass allow_partial, so the parse itself
    is what proves the output usable.
    """
    with destination.open('wb') as stream, subprocess.Popen(
            command, stdout=stream, stderr=subprocess.DEVNULL) as process:
        deadline = time.monotonic()+seconds
        try:
            while process.poll() is None:
                if destination.stat().st_size > limit or time.monotonic() >= deadline:
                    raise ValueError('installer metadata decoder exceeded its resource budget')
                time.sleep(.05)
            size = destination.stat().st_size
            failed = process.returncode and not (allow_partial and size)
            if failed or size > limit:
                raise ValueError('installer metadata decoder failed or exceeded its output budget')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()


def _complete_member(output, expected):
    """Return a member's bytes only when the listing proves them complete."""
    if not output.exists() or not isinstance(expected, str) or not expected.isdigit():
        return None
    if output.stat().st_size != int(expected):
        return None
    return output.read_bytes()


def _inspect_desktop(candidate, path):
    """Observe the actual app version needed to discover native manifest URLs.

    Algorithm provenance: ReverseTelnet's manifest_files_provenance_and_research.md.
    Inputs are our captured installer bytes, never a foreign manifest collection.
    """
    numeric = lambda value: isinstance(value, str) and re.fullmatch(r'\d+(?:[.]\d+)+', value)

    def parse(data, member):
        info = plistlib.loads(data)
        if not str(info.get('CFBundleIdentifier', '')).lower().startswith('com.sonos'):
            raise ValueError('installer plist is not a Sonos application bundle')
        identifier = info.get('CFBundleIdentifier')
        metadata = {'bundle_id': identifier, 'member': member,
                    'bundle_version': info.get('CFBundleVersion') if numeric(info.get('CFBundleVersion')) else None,
                    'plist_sha256': hashlib.sha256(data).hexdigest()}
        # The short version is the release marketing version used by campaign
        # URLs, and only a controller bundle states it. A nested helper states
        # its own version, which must not become a release version.
        if numeric(info.get('CFBundleShortVersionString')) and str(identifier).lower().startswith('com.sonos.maccontroller'):
            metadata['app_version'] = info['CFBundleShortVersionString']
        if not metadata.get('app_version') and not metadata.get('bundle_version'):
            raise ValueError('installer plist has no Sonos application version')
        return {'desktop_metadata': metadata}
    with tempfile.TemporaryDirectory(dir=path.parent, prefix='desktop-metadata-') as temporary:
        work = Path(temporary)
        if path.suffix.lower() != '.exe' and zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as bundle:
                members = sorted((m for m in bundle.infolist() if
                    m.filename.endswith('.app/Contents/Info.plist')), key=lambda m: m.filename.count('/'))
                build_only = None
                for member in members:
                    if member.file_size > 1_000_000:
                        raise ValueError('installer plist exceeds its metadata budget')
                    try:
                        parsed = parse(bundle.read(member), member.filename)
                    except (ValueError, plistlib.InvalidFileException, ExpatError):
                        continue
                    if parsed['desktop_metadata'].get('app_version'):
                        return parsed
                    build_only = build_only or parsed
                if build_only:
                    return build_only
            raise ValueError('installer archive has no Sonos Info.plist')
        tool = shutil.which('7zz') or shutil.which('7z')
        if not tool:
            raise ValueError('7zip is required to read installer metadata')
        executable = path.suffix.lower() == '.exe'
        archive_type = ['-tPE'] if executable else []
        # A recursive wildcard listing keeps the output small even when the
        # decoder expands filesystem images nested inside the container.
        selection = '*.app/Contents/Info.plist' if not executable else None
        listing = work/'listing.txt'
        _archive_output([tool, 'l', '-slt', *(('-r',) if selection else ()), *archive_type, '--', str(path),
                         *([selection] if selection else [])], listing, limit=4*1024*1024,
                        allow_partial=True)
        entries = []
        for block in listing.read_text(errors='replace').split('\n\n'):
            entries.append(dict(line.split(' = ', 1) for line in block.splitlines() if ' = ' in line))

        def member_bytes(member, name, limit=1_000_000):
            """Read one member; a failed or short extraction is not usable."""
            output = work/name
            try:
                _archive_output([tool, 'x', '-so', *archive_type, '--', str(path), member['Path']], output,
                                limit=limit, allow_partial=True)
            except ValueError:
                return None
            return _complete_member(output, member.get('Size', ''))
        if executable:
            resources = [e for e in entries if re.search(r'(?i)(?:^|/)VERSION(?:[.]txt|/|$)', e.get('Path', ''))]
            for member in resources:
                data = member_bytes(member, 'version-resource')
                if data is None:
                    continue
                for text in (data.decode('utf-16le', errors='ignore'), data.decode('utf-8', errors='ignore')):
                    pattern = r'ProductVersion[\s\x00\",:=]+([0-9]+(?:[.,][ ]*[0-9]+)+)'
                    # Prefer the application's StringFileInfo value over
                    # 7zip's uppercase PRODUCTVERSION fixed-header summary.
                    version = re.search(pattern, text) or re.search(pattern, text, re.I)
                    if version and 'sonos' in text.lower():
                        return {'desktop_metadata': {'installer_version': re.sub(r',\s*', '.', version[1]),
                                'member': member['Path'],
                                'version_resource_sha256': hashlib.sha256(data).hexdigest()}}
            raise ValueError('installer has no readable Sonos ProductVersion resource')
        plists = sorted((e for e in entries if e.get('Path', '').endswith('.app/Contents/Info.plist')),
                        key=lambda e: e['Path'].count('/'))
        build_only = None
        for member in plists:
            data = member_bytes(member, 'Info.plist')
            if data is None:
                continue
            try:
                parsed = parse(data, member['Path'])
            except (ValueError, plistlib.InvalidFileException, ExpatError):
                continue
            if parsed['desktop_metadata'].get('app_version'):
                return parsed
            build_only = build_only or parsed
        if build_only:
            return build_only
        raise ValueError('installer disk image has no readable application plist')


def inspect_desktop(candidate, path):
    # Metadata discovery must never prevent preservation of the original bytes.
    try:
        return _inspect_desktop(candidate, path)
    except (OSError, ValueError, subprocess.SubprocessError, plistlib.InvalidFileException, ExpatError) as error:
        return {'discovery_errors': [{'phase': 'desktop-metadata', 'error': str(error)}]}


def desktop_inspection_priority(record):
    builds = [tuple(map(int, match.groups())) for url in record.get('source_urls') or []
              if isinstance(url, str) and (match := re.search(r'/software/[^/]+/Sonos_(\d+)[.](\d+)-(\d+)[.](?:dmg|exe)$', urlsplit(url).path))]
    # Inspect URLs that can unlock new native campaign paths before classic
    # filenames with no campaign token. All captures remain in the fair queue.
    return (not bool(builds), tuple(-n for n in max(builds)) if builds else ())


def installer_pivot_contexts(record):
    verification = record.get('verification')
    metadata = verification.get('desktop_metadata') if isinstance(verification, dict) else {}
    if not isinstance(metadata, dict):
        metadata = {}
    def usable(value):
        return isinstance(value, str) and re.fullmatch(r'\d+(?:[.]\d+)+', value)

    # A campaign installer URL carries the build and token that identify its
    # release family; the application version is an additional observation,
    # not a requirement.
    app = metadata.get('app_version') if usable(metadata.get('app_version')) else None
    result = []
    for url in record.get('source_urls') or []:
        if not sonos_url(url):
            continue
        match = re.fullmatch(r'/software/([A-Za-z0-9_-]+)/Sonos_(\d+[.]\d+-\d+)[.](?:dmg|exe)', urlsplit(url).path, re.I)
        if match:
            versions = [None]
            if app is not None:
                versions = [app]
                parts = app.split('.')
                while len(parts) > 2 and parts[-1] == '0':
                    parts.pop()
                    versions.append('.'.join(parts))
            for version in versions:
                result.append({'build': match[2], 'app_version': version, 'metadata_app_version': app,
                               'token': match[1], 'source_url': url, 'source_sha256': record.get('sha256'),
                               'plist_sha256': metadata.get('plist_sha256'),
                               'version_resource_sha256': metadata.get('version_resource_sha256')})
    return result


def pivotable(record):
    """Only campaign installers can yield a native manifest URL."""
    urls = [*(record.get('source_urls') or []), record.get('source_url'), record.get('url')]
    return any(isinstance(url, str) and re.fullmatch(
        r'/software/[A-Za-z0-9_-]+/Sonos_\d+[.]\d+-\d+[.](?:dmg|exe)', urlsplit(url).path, re.I)
        for url in urls if url)


def source(*, refresh_hours=168, current_refresh_hours=6, default_platform='', paused_hosts=()):
    from .apk import inspect_official, inspection_revision
    mobile = default_platform == 'fireos-android-apk'
    return AppSource(partial(acquire, blocked=set(paused_hosts), inspection_required=mobile), release_for,
                     'Exact public Sonos installer captures with verified hashes and source provenance.',
                     default_platform=default_platform, refresh_hours=refresh_hours, current_refresh_hours=current_refresh_hours,
                     inspect=inspect_official if mobile else inspect_desktop,
                     inspection_revision=inspection_revision() if mobile else 'desktop-metadata-5',
                     inspect_if=None if mobile else pivotable,
                     inspection_blocks_publication=mobile,
                     inspection_priority=None if mobile else desktop_inspection_priority)


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
    native = manifest_candidates(root, kind)
    seeds = [row['url'] for row in native['candidates']]
    discovery = root/'data/apps'/f'{kind}-discovery.json'
    if discovery.exists():
        try:
            rows = json.loads(discovery.read_text()).get('candidates', [])
            if not isinstance(rows, list):
                raise ValueError('invalid installer discovery candidates')
            seeds.extend(row.get('url') for row in rows if isinstance(row, dict))
        except (OSError, ValueError, AttributeError) as error:
            native['errors'].append({'source': str(discovery), 'error': str(error)})
    index = discover_sources(previous, pages=pages, extensions=extensions, allowed=official_source,
                             seed_urls=seeds)
    atomic_json(path, index)
    candidates, errors = [], [*native['errors'], *index['errors']]
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
                'acquisition_id': capture.get('provider', 'wayback')+':'+identity, 'archive_capture': capture,
                **({'wayback_timestamp': capture['timestamp'], 'wayback_digest': capture.get('digest')}
                   if capture.get('provider', 'wayback') == 'wayback' else {})})
    discovery = work/'discovery.json'
    atomic_json(discovery, {'status': index['status'], 'errors': errors, 'candidates': candidates})
    paused = paused_index_hosts(index)
    result = preserve(source(default_platform='fireos-android-apk' if kind == 'mobile' else '',
                             refresh_hours=refresh_hours, current_refresh_hours=current_refresh_hours),
                      discovery, root/'data/apps'/f'{kind}-archive.json', repository,
                      root=root, limit=limit, paused_hosts=paused, discovery_reference=path)
    return {'status': result['status'], 'artifact_total': result['artifact_total'],
            'attempted': result['attempted_this_run'], 'errors': result['errors'],
            'pending': result['pending_total'], 'index_status': index['status']}
