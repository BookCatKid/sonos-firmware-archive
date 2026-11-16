"""Reproduce legacy keys from verified, independently cataloged updater sources."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import selectors
import subprocess
import tempfile
import time

from . import cramfs
from .legacy import discover_legacy_updater_key
from .mdp import write_private_key_exclusive
from .storage import file_sha256, atomic_json
from .retry import checkpoint, select
from .upd import parse_file
from .releases import download_verified

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_UPDATER_BYTES = 8 * 1024 * 1024


def select_updater_sources(root, catalog, model):
    """Select by plaintext section identity, never a release/version inventory."""
    rows = []
    seen = set()
    for package in catalog.get('packages', []):
        identity = package['id']
        if (package.get('package_model') != model or package.get('artifact_status') != 'preserved'
            or package.get('artifact_type') != 'upd' or Path(identity).name != identity or identity in {'.', '..'}):
            continue
        manifest = root / 'data/upd' / (identity + '.json')
        if not manifest.exists():
            continue
        inventory = json.loads(manifest.read_text())
        if inventory.get('sha256') != package.get('sha256'):
            continue
        for section in inventory.get('sections', []):
            if section.get('section_type') != 4 or section.get('encrypted'):
                continue
            references = [('raw', raw) for raw in catalog.get('raw_images', [])
                          if raw.get('package_id') == identity and raw.get('sha256') == section['sha256']
                          and raw.get('bytes') == section['payload_length'] and
                          (raw.get('section_index') == section['index'] or
                           raw.get('section_index') is None and raw.get('kind') == 'rootfs')]
            references.append(('upd', package))
            for kind, reference in references:
                if not all(reference.get(key) for key in ('sha256', 'bytes', 'release_tag', 'release_asset')):
                    continue
                digest = reference['sha256']
                if (not isinstance(digest, str) or len(digest) != 64 or
                    any(c not in '0123456789abcdef' for c in digest) or
                    not 0 < reference['bytes'] <= MAX_SOURCE_BYTES):
                    continue
                source = {key: reference[key] for key in ('sha256', 'bytes', 'release_tag', 'release_asset')}
                source.update(kind=kind, package_id=identity, model=model, section_index=section['index'],
                              section_sha256=section['sha256'], section_bytes=section['payload_length'])
                key = (kind, source['release_tag'], source['release_asset'], section['index'])
                if key not in seen:
                    seen.add(key)
                    rows.append(source)
    return sorted(rows, key=lambda row: (row['kind'] != 'raw', row['package_id'], row['section_index']))


def bounded_output(command, *, max_bytes, timeout=60):
    """Read a decoder's pipe with independent wall-time and output limits."""
    output = bytearray()
    deadline = time.monotonic() + timeout
    with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL) as process:
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError('filesystem member reader timed out')
                    if not selector.select(min(remaining, 1)):
                        continue
                    block = os.read(process.stdout.fileno(), min(65536, max_bytes + 1 - len(output)))
                    if not block:
                        break
                    output.extend(block)
                    if len(output) > max_bytes:
                        raise ValueError('filesystem member exceeds the size limit')
            if process.wait(timeout=max(0.001, deadline - time.monotonic())):
                raise ValueError('filesystem member reader failed')
        finally:
            if process.poll() is None:
                process.kill()
                process.wait()
    return bytes(output)


def read_updater_member(path):
    data = path.read_bytes()
    if data[:4] in {b'hsqs', b'sqsh'}:
        return bounded_output(['unsquashfs', '-cat', str(path), 'bin/upgrade'], max_bytes=MAX_UPDATER_BYTES)
    order, offset = cramfs.detect_filesystem(data)
    inode = cramfs.find_inode(data, 'bin/upgrade', order, offset + cramfs.ROOT_INODE_OFFSET)
    return cramfs.read_file(data, inode, order, max_bytes=MAX_UPDATER_BYTES)


def fetch_release_source(source, destination, repository):
    download_verified(source, destination, repository)


def recover_catalog_key(root, catalog, model, expected_recipient, output, repository, *,
                        read_artifact=None, read_member=None, max_sources=8):
    """Try bounded verified sources and write only an exact matching key.

    Return public provenance; derivation material and keys never enter reports.
    The callbacks are internal seams for offline fixtures and decoder isolation.
    """
    read_artifact = read_artifact or fetch_release_source
    read_member = read_member or read_updater_member
    if max_sources <= 0:
        raise ValueError('max_sources must be positive')
    sources = select_updater_sources(root, catalog, model)
    state_path = root / 'data/discovery/key-recovery.json'
    state = json.loads(state_path.read_text()) if state_path.exists() else {'schema_version': 1, 'repositories': {}}
    records = state['repositories'].setdefault(repository, {}).setdefault(expected_recipient, {})
    def identity(source):
        return hashlib.sha256(json.dumps(source, sort_keys=True).encode()).hexdigest()
    # Keys are ephemeral: replay a successful, unchanged source in each worker.
    # Failed candidates back off while new sources continue through the queue.
    preferred = [row for row in sources if records.get(identity(row), {}).get('status') == 'recovered'][:max_sources]
    other = [row for row in sources if row not in preferred]
    queue = preferred + select(other, records, key=identity, limit=max_sources - len(preferred),
                               state=state, queue=f'{repository}/{expected_recipient}')
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    failures = []
    for source in queue:
        try:
            with tempfile.TemporaryDirectory(dir=output.parent, prefix='.recovery-') as temporary:
                body = Path(temporary) / 'capture'
                read_artifact(source, body, repository)
                if body.stat().st_size != source['bytes'] or file_sha256(body) != source['sha256']:
                    raise ValueError('key source does not match its publication receipt')
                rootfs = body
                if source['kind'] == 'upd':
                    section = next((row for row in parse_file(body) if row.index == source['section_index']), None)
                    if (section is None or section.section_type != 4 or section.encrypted or
                        section.sha256 != source['section_sha256'] or section.payload_length != source['section_bytes']):
                        raise ValueError('plaintext key source section does not match its inventory')
                    rootfs = Path(temporary) / 'rootfs'
                    with body.open('rb') as stream:
                        stream.seek(section.offset + 16)
                        rootfs.write_bytes(stream.read(section.payload_length))
                updater = read_member(rootfs)
                recovered = discover_legacy_updater_key(updater, model, expected_recipient)
                write_private_key_exclusive(output, recovered.key)
                proof = {'method': 'legacy-updater', 'model': model, 'recipient_id': expected_recipient,
                        'repository': repository,
                        'source': source, 'updater_sha256': hashlib.sha256(updater).hexdigest(),
                        'wrapper_offset': recovered.wrapper_offset, 'byte_order': recovered.byte_order}
                records[identity(source)] = {**checkpoint(success=True, success_hours=168),
                                              'status': 'recovered', 'proof': proof}
                atomic_json(state_path, state)
                return proof
        except Exception as error:
            # Filesystem/updater content and crypto exceptions may contain
            # sensitive details. Only public source identity and type survive.
            failures.append({'package_id': source['package_id'], 'kind': source['kind'],
                             'error_type': type(error).__name__})
            records[identity(source)] = {**checkpoint(records.get(identity(source))), 'status': 'failed',
                                         'source': source, 'error_type': type(error).__name__}
            atomic_json(state_path, state)
    raise ValueError(f'no matching legacy key in {len(failures)} attempted sources; '
                     f'{max(0, len(sources) - len(failures))} sources deferred')
