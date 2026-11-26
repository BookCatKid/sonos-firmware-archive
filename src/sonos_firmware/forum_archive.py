"""Mine the anonymous Sonos community forum archive for native update URLs."""

from __future__ import annotations

import hashlib
import html
import ipaddress
import io
import json
import re
import tarfile
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlsplit

from .retry import checkpoint, due, error_checkpoint
from .source_urls import sonos_url
from .discovery import USER_AGENT

LATEST_RELEASE = 'https://api.github.com/repos/BookCatKid/Sonos-forum-crawl/releases/latest'
MAX_STATE_BYTES = 4*1024**3
MAX_JSONL_BYTES = 4*1024**3

_URL = re.compile(r'https?://[^\s"\'<>`\\)\]]+', re.I)
_FIRMWARE_PATH = re.compile(r'^/firmware/[^/]+/[^/]+/')

NATIVE_HOSTS = ('update-firmware.sonos.com', 'update.sonos.com')


def _get(url, *, limit, timeout=60):
    import os
    headers = {'User-Agent': USER_AGENT, 'Accept': 'application/vnd.github+json'}
    token = os.environ.get('GH_TOKEN') or os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer '+token
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=timeout) as response:
        data = response.read(limit+1)
    if len(data) > limit:
        raise ValueError('forum archive response exceeds its byte budget')
    return data


def _release(fetch):
    document = json.loads(fetch(LATEST_RELEASE, limit=2*1024*1024))
    assets = {asset['name']: asset['browser_download_url'] for asset in document.get('assets', [])}
    if 'state.tar.gz' not in assets or 'manifest.json' not in assets:
        raise ValueError('forum archive release lacks state.tar.gz or manifest.json')
    manifest = json.loads(fetch(assets['manifest.json'], limit=1024*1024))
    expected = manifest.get('assets', {}).get('state.tar.gz', {}).get('sha256')
    if not expected:
        raise ValueError('forum archive manifest lacks a state.tar.gz digest')
    return {'tag': document.get('tag_name'), 'url': assets['state.tar.gz'], 'sha256': expected,
            'size': manifest.get('assets', {}).get('state.tar.gz', {}).get('size')}


def _download(release, opener, destination):
    """Stream the snapshot to disk; its digest is checked before any parsing."""
    request = urllib.request.Request(release['url'], headers={'User-Agent': USER_AGENT})
    digest, size = hashlib.sha256(), 0
    with opener(request, timeout=120) as response, destination.open('wb') as output:
        while block := response.read(1024*1024):
            size += len(block)
            if size > MAX_STATE_BYTES:
                raise ValueError('forum archive state snapshot exceeds the byte budget')
            digest.update(block)
            output.write(block)
    if digest.hexdigest() != release['sha256']:
        raise ValueError('forum archive state snapshot does not match its manifest digest')
    return digest.hexdigest()


def _device_local(host):
    """Link-local, private and single-label hosts expose a device's own layout."""
    if not host or host.endswith('.local') or '.' not in host:
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_private or address.is_loopback or address.is_link_local


def lead_urls(text):
    """Native firmware locations plus device-layout paths."""
    roots, installers = set(), set()
    for raw in _URL.findall(html.unescape(text).replace('\\/', '/')):
        url = raw.rstrip('),].};')
        try:
            parts = urlsplit(url)
        except ValueError:
            continue
        host = (parts.hostname or '').lower()
        path = parts.path
        native = host in NATIVE_HOSTS
        secure = 'https://'+host+path
        if sonos_url(secure) and path.lower().endswith(('.exe', '.dmg', '.apk')):
            installers.add(secure)
        directory = _FIRMWARE_PATH.match(path)
        if native and path.endswith('/update.upm'):
            roots.add('https://'+host+path)
        elif directory and (native or _device_local(host)):
            directory_url = directory.group(0)
            for update_host in (NATIVE_HOSTS if not native else (host,)):
                roots.add('https://'+update_host+directory_url+'update.upm')
    return sorted(roots), sorted(installers)


def _mine(stream, leads, budget):
    for line in stream:
        budget['bytes'] += len(line)
        if budget['bytes'] > MAX_JSONL_BYTES:
            raise ValueError('forum archive export exceeds the mining budget')
        if 'sonos.com' not in line and '/firmware/' not in line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        text = row.get('content_html') or ''
        roots, installers = lead_urls(text)
        if not roots and not installers:
            continue
        proof = {'host': row.get('host'), 'topic': row.get('topic_public_id') or row.get('public_id'),
                 'reply': row.get('public_reply_id'), 'replied_at': row.get('replied_at'),
                 'content_sha256': hashlib.sha256(text.encode()).hexdigest()}
        for kind, urls in (('roots', roots), ('installers', installers)):
            for url in urls:
                if proof not in leads[kind].setdefault(url, []):
                    leads[kind][url].append(proof)


def discover_forum_archive(previous, *, fetch=_get, opener=urllib.request.urlopen, success_hours=720):
    """Mine the cumulative forum snapshot; monthly at most, resumable by tag."""
    if previous and not due(previous):
        return previous
    state = dict(previous)
    try:
        release = _release(fetch)
        if release['tag'] and release['tag'] == previous.get('mined_tag'):
            return {**state, **checkpoint(previous, success=True, success_hours=success_hours),
                    'status': 'observed', 'note': 'no newer forum snapshot'}
        if release.get('size') and release['size'] > MAX_STATE_BYTES:
            raise ValueError('forum archive state snapshot exceeds the byte budget')
        leads = {'roots': {}, 'installers': {}}
        budget = {'bytes': 0}
        with tempfile.TemporaryDirectory() as workspace:
            snapshot = Path(workspace)/'state.tar.gz'
            digest = _download(release, opener, snapshot)
            with tarfile.open(snapshot, mode='r:gz') as archive:
                for member in ('replies.jsonl', 'topics.jsonl'):
                    extracted = archive.extractfile(member)
                    if extracted is None:
                        raise ValueError('forum archive state lacks '+member)
                    _mine(io.TextIOWrapper(extracted, encoding='utf-8', errors='replace'), leads, budget)
        return {**state, **checkpoint(previous, success=True, success_hours=success_hours),
                'status': 'observed', 'mined_tag': release['tag'], 'state_sha256': digest,
                'roots': leads['roots'], 'installers': leads['installers'],
                'root_total': len(leads['roots']), 'installer_total': len(leads['installers'])}
    except Exception as error:
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        return {**state, **error_checkpoint(error, previous), 'status': 'failed', 'error': str(error),
                'errors': [{'source': 'forum-archive', 'error': str(error)}]}
