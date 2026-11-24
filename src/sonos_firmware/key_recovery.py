"""Recover exact recipient keys from verified, independently cataloged updaters."""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
import selectors
import subprocess
import tempfile
import time
import urllib.error
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.exceptions import UnsupportedAlgorithm

from . import cramfs
from .legacy import discover_legacy_updater_key
from .mdp import write_private_key_exclusive
from .storage import file_sha256, atomic_json
from .retry import checkpoint, select, error_checkpoint, RequestBackoff
from .upd import parse_file
from .releases import download_verified
from .extract import key_recipient_id

MAX_SOURCE_BYTES = 128 * 1024 * 1024
MAX_UPDATER_BYTES = 8 * 1024 * 1024
RECOVERY_REVISION = 2


def embedded_private_key(data, expected_recipient, *, max_bytes=MAX_UPDATER_BYTES):
    """Return an exact RSA recipient match and public location evidence, or None.

    Scan PEM and length-framed DER PKCS#1/PKCS#8 private keys without executing
    updater code. Public keys and encrypted/invalid candidates are not keys.
    """
    if len(data) > max_bytes:
        raise ValueError('updater exceeds the recovery size limit')
    if not re.fullmatch('[0-9a-f]{40}', expected_recipient):
        raise ValueError('expected recipient must be a SHA-1 fingerprint')
    def accept(body, offset, encoding):
        try:
            loader = serialization.load_pem_private_key if encoding == 'pem' else serialization.load_der_private_key
            key = loader(body, password=None)
            if isinstance(key, rsa.RSAPrivateKey) and key_recipient_id(key) == expected_recipient:
                return key, {'method': 'embedded-updater-private-key', 'key_encoding': encoding,
                             'key_offset': offset, 'key_bytes': len(body)}
        except (ValueError, TypeError, UnsupportedAlgorithm):
            pass
        return None
    pem = re.compile(rb'-----BEGIN (?P<label>(?:RSA )?PRIVATE KEY)-----\r?\n'
                     rb'[A-Za-z0-9+/=\r\n]+-----END (?P=label)-----(?:\r?\n)?')
    for match in pem.finditer(data):
        result = accept(match[0], match.start(), 'pem')
        if result:
            return result
    # Both unencrypted DER private-key forms start with a sequence and version
    # integer. Frame the whole sequence before passing it to the crypto parser.
    prefix = re.compile(rb'\x30(?:[\x00-\x7f]|\x81.|\x82..|\x83...|\x84....)\x02\x01[\x00\x01]', re.DOTALL)
    for match in prefix.finditer(data):
        start = match.start()
        first = data[start+1]
        count = first & 0x7f if first & 0x80 else 0
        size = int.from_bytes(data[start+2:start+2+count], 'big') if count else first
        end = start+2+count+size
        if end <= len(data):
            result = accept(data[start:end], start, 'der')
            if result:
                return result
    return None


def select_updater_sources(root, catalog, model):
    """Select plaintext or previously decrypted rootfs with source provenance."""
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
        receipt_path = root / 'data/raw' / (identity + '.json')
        try:
            receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
        except (OSError, ValueError):
            receipt = {}
        if (not isinstance(receipt, dict) or not isinstance(receipt.get('components'), list) or
                any(not isinstance(component, dict) for component in receipt['components'])):
            receipt = {}
        for section in inventory.get('sections', []):
            if section.get('section_type') != 4:
                continue
            references = []
            encrypted = bool(section.get('encrypted'))
            for raw in catalog.get('raw_images', []):
                if raw.get('package_id') != identity:
                    continue
                if encrypted:
                    # Ciphertext section hashes cannot identify plaintext. A
                    # completed extraction receipt must bind it to this exact
                    # source before it can become another recovery source.
                    fields = ('package_id', 'section_index', 'section_type', 'bytes', 'sha256',
                              'source_encrypted', 'recipient_id')
                    if (receipt.get('source_sha256') != package['sha256'] or
                            raw.get('section_index') != section['index'] or raw.get('section_type') != 4 or
                            raw.get('source_encrypted') is not True or not section.get('recipient_id') or
                            raw.get('recipient_id') != section['recipient_id'] or
                            not any(all(proof.get(field) == raw.get(field) for field in fields)
                                    for proof in receipt.get('components', []))):
                        continue
                elif (raw.get('sha256') != section['sha256'] or raw.get('bytes') != section['payload_length'] or
                      not (raw.get('section_index') == section['index'] or
                           raw.get('section_index') is None and raw.get('kind') == 'rootfs')):
                    continue
                references.append(('raw', raw))
            if not encrypted:
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
                if encrypted:
                    source.update(source_sha256=package['sha256'], section_encrypted=True,
                                  source_recipient_id=section['recipient_id'])
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
    backoff = RequestBackoff(state, repository)
    def identity(source):
        return hashlib.sha256(json.dumps({'source': source, 'recovery_revision': RECOVERY_REVISION},
                                         sort_keys=True).encode()).hexdigest()
    successful_sources = {json.dumps(row['proof'].get('source'), sort_keys=True)
        for row in records.values() if row.get('status') == 'recovered' and isinstance(row.get('proof'), dict)}
    # Keys are ephemeral: replay a successful, unchanged source in each worker.
    # Failed candidates back off while new sources continue through the queue.
    preferred = [row for row in sources if json.dumps(row, sort_keys=True) in successful_sources][:max_sources]
    other = [row for row in sources if row not in preferred]
    queue = preferred + select(other, records, key=identity, limit=max_sources - len(preferred),
                               state=state, queue=f'{repository}/{expected_recipient}')
    if backoff.paused('key-source-downloads'):
        queue = []
    assessment = {'method': 'catalog-updater', 'methods': ['embedded-updater-private-key', 'legacy-updater'],
                  'recovery_revision': RECOVERY_REVISION, 'model': model,
                  'checked_at': datetime.now(timezone.utc).isoformat(),
                  'eligible_sources': len(sources), 'selected_sources': len(queue),
                  'attempted_sources': 0, 'pending_sources': len(sources),
                  'status': 'in-progress' if queue else 'deferred' if sources else 'no-eligible-source'}
    state.setdefault('assessments', {}).setdefault(repository, {})[expected_recipient] = assessment
    # A method with no eligible source is evidence too. Persist it without
    # imposing a recipient-wide deadline: newly discovered inputs run at once.
    atomic_json(state_path, state)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    failures = []
    for source in queue:
        assessment['attempted_sources'] += 1
        atomic_json(state_path, state)
        try:
            with tempfile.TemporaryDirectory(dir=output.parent, prefix='.recovery-') as temporary:
                body = Path(temporary) / 'capture'
                read_artifact(source, body, repository)
                backoff.clear('key-source-downloads')
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
                embedded = embedded_private_key(updater, expected_recipient)
                if embedded is not None:
                    key, details = embedded
                else:
                    recovered = discover_legacy_updater_key(updater, model, expected_recipient)
                    key = recovered.key
                    details = {'method': 'legacy-updater', 'wrapper_offset': recovered.wrapper_offset,
                               'byte_order': recovered.byte_order}
                write_private_key_exclusive(output, key)
                proof = {**details, 'model': model, 'recipient_id': expected_recipient,
                        'repository': repository,
                        'source': source, 'updater_sha256': hashlib.sha256(updater).hexdigest()}
                records[identity(source)] = {**checkpoint(success=True, success_hours=168),
                                              'status': 'recovered', 'proof': proof}
                assessment.update(status='recovered', pending_sources=0)
                atomic_json(state_path, state)
                return proof
        except Exception as error:
            # Filesystem/updater content and crypto exceptions may contain
            # sensitive details. Only public source identity and type survive.
            failures.append({'package_id': source['package_id'], 'kind': source['kind'],
                             'error_type': type(error).__name__})
            records[identity(source)] = {**error_checkpoint(error, records.get(identity(source))), 'status': 'failed',
                                         'source': source, 'error_type': type(error).__name__}
            backoff.failed('key-source-downloads', error)
            if isinstance(error, urllib.error.HTTPError):
                error.close()
            atomic_json(state_path, state)
            if backoff.paused('key-source-downloads'):
                break
    if failures:
        assessment.update(status='attempts-failed')
        atomic_json(state_path, state)
    raise ValueError(f'no matching updater key in {len(failures)} attempted sources; '
                     f'{max(0, len(sources) - len(failures))} sources deferred')
