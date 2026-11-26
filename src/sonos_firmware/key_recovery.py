"""Recover exact recipient keys from verified, independently cataloged components."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
import subprocess
import tarfile
import tempfile
import urllib.error
from datetime import datetime, timezone
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.exceptions import UnsupportedAlgorithm

from .deadline import winding_down
from . import cramfs
from .legacy import discover_legacy_updater_key
from .mdp import write_private_key_exclusive
from .storage import file_sha256, atomic_json
from .retry import checkpoint, select, error_checkpoint, RequestBackoff
from .upd import parse_file
from .releases import download_verified
from .extract import key_recipient_id

RECOVERY_REVISION = 4


def embedded_private_key(data, expected_recipient):
    """Return an exact RSA recipient match and public location evidence, or None.

    Scan PEM and length-framed DER PKCS#1/PKCS#8 private keys without executing
    updater code. Public keys and encrypted/invalid candidates are not keys.
    """
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
    return _select_sources(root, catalog, model, rootfs_only=True)


def _source_receipt(reference):
    fields = ('sha256', 'bytes', 'release_tag', 'release_asset')
    if not isinstance(reference, dict) or not all(reference.get(key) for key in fields):
        return None
    digest, size = reference['sha256'], reference['bytes']
    if (not isinstance(digest, str) or not re.fullmatch('[0-9a-f]{64}', digest) or
            not isinstance(size, int) or isinstance(size, bool) or size <= 0):
        return None
    return {key: reference[key] for key in fields}


def inventory_private_key(path, expected_recipient, source_sha256):
    """Inspect every decoded regular member of a verified inventory bundle."""
    found, manifest_seen = None, False
    with tarfile.open(path, 'r|xz') as archive:
        for member in archive:
            if not member.isfile():
                continue
            if member.size < 0:
                raise ValueError('negative inventory member size')
            with archive.extractfile(member) as stream:
                data = stream.read()
            if len(data) != member.size:
                raise ValueError('truncated inventory member')
            if member.name == 'inventory.json':
                manifest = json.loads(data)
                if manifest_seen or not isinstance(manifest, dict) or manifest.get('sha256') != source_sha256:
                    raise ValueError('inventory manifest does not match its parent source')
                manifest_seen = True
            elif found is None:
                candidate = embedded_private_key(data, expected_recipient)
                if candidate is not None:
                    key, details = candidate
                    found = key, {**details, 'method': 'embedded-inventory-private-key',
                                  'member': member.name, 'member_sha256': hashlib.sha256(data).hexdigest()}
    if not manifest_seen:
        raise ValueError('inventory manifest is missing')
    return found


def _select_sources(root, catalog, model, *, rootfs_only=False):
    """Bind plaintext components to preserved parents and extraction receipts."""
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
        prior = package.get('inventory', {})
        if not rootfs_only and isinstance(prior, dict):
            revision = prior.get('revision', {})
            publications = prior.get('publications', {})
            bundle = publications.get('bundle', {}) if isinstance(publications, dict) else {}
            source = _source_receipt(bundle)
            if (source and bundle.get('status') == 'verified' and
                    prior.get('source_sha256') == package['sha256'] and isinstance(revision, dict) and
                    revision.get('source_sha256') == package['sha256']):
                rows.append({**source, 'kind': 'inventory-bundle', 'package_id': identity, 'model': model,
                             'source_sha256': package['sha256'], 'repository': bundle.get('repository'),
                             })
        receipt_path = root / 'data/raw' / (identity + '.json')
        try:
            receipt = json.loads(receipt_path.read_text()) if receipt_path.is_file() else {}
        except (OSError, ValueError):
            receipt = {}
        if (not isinstance(receipt, dict) or not isinstance(receipt.get('components'), list) or
                any(not isinstance(component, dict) for component in receipt['components'])):
            receipt = {}
        for section in inventory.get('sections', []):
            section_type = section.get('section_type')
            if rootfs_only and section_type != 4:
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
                            raw.get('section_index') != section['index'] or raw.get('section_type') != section_type or
                            raw.get('source_encrypted') is not True or not section.get('recipient_id') or
                            raw.get('recipient_id') != section['recipient_id'] or
                            not any(all(proof.get(field) == raw.get(field) for field in fields)
                                    for proof in receipt.get('components', []))):
                        continue
                elif (raw.get('sha256') != section['sha256'] or raw.get('bytes') != section['payload_length'] or
                      raw.get('section_type', section_type) != section_type or
                      not (raw.get('section_index') == section['index'] or
                           section_type == 4 and raw.get('section_index') is None and raw.get('kind') == 'rootfs')):
                    continue
                references.append(('raw', raw))
            if not encrypted:
                references.append(('upd', package))
            for kind, reference in references:
                source = _source_receipt(reference)
                if source is None:
                    continue
                source.update(kind=kind, package_id=identity, model=model, section_index=section['index'],
                              section_sha256=section['sha256'], section_bytes=section['payload_length'])
                if section_type != 4:
                    source.update(kind=kind+'-section', section_type=section_type)
                if encrypted:
                    source.update(source_sha256=package['sha256'], section_encrypted=True,
                                  source_recipient_id=section['recipient_id'])
                key = (kind, source['release_tag'], source['release_asset'], section['index'])
                if key not in seen:
                    seen.add(key)
                    rows.append(source)
    return sorted(rows, key=lambda row: (0 if row['kind'] == 'inventory-bundle' else
                                        1 if row['kind'].startswith('raw') else 2,
                                        row['package_id'], row.get('section_index', -1)))


def decoder_output(command):
    """Read a decoder to EOF, checking its exit status."""
    return subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                          check=True).stdout


def read_updater_member(path):
    data = path.read_bytes()
    if data[:4] in {b'hsqs', b'sqsh'}:
        return decoder_output(['unsquashfs', '-cat', str(path), 'bin/upgrade'])
    order, offset = cramfs.detect_filesystem(data)
    inode = cramfs.find_inode(data, 'bin/upgrade', order, offset + cramfs.ROOT_INODE_OFFSET)
    return cramfs.read_file(data, inode, order)


def fetch_release_source(source, destination, repository):
    download_verified(source, destination, repository)


def _source_key(body, source, model, expected_recipient, read_member):
    if source['kind'] == 'inventory-bundle':
        result = inventory_private_key(body, expected_recipient, source['source_sha256'])
        if result is None:
            raise ValueError('no matching private key in decoded inventory')
        return result
    component_path = body
    if source['kind'] in {'upd', 'upd-section'}:
        section = next((row for row in parse_file(body) if row.index == source['section_index']), None)
        if (section is None or section.section_type != source.get('section_type', 4) or section.encrypted or
                section.sha256 != source['section_sha256'] or section.payload_length != source['section_bytes']):
            raise ValueError('plaintext key source section does not match its inventory')
        component_path = body.with_name('section')
        with body.open('rb') as stream:
            stream.seek(section.offset+16)
            component_path.write_bytes(stream.read(section.payload_length))
    component = 'section_type' in source
    data = component_path.read_bytes() if component else read_member(component_path)
    embedded = embedded_private_key(data, expected_recipient)
    if embedded is not None:
        key, details = embedded
        if component:
            details = {**details, 'method': 'embedded-component-private-key'}
    elif component:
        raise ValueError('no matching private key in component')
    else:
        recovered = discover_legacy_updater_key(data, model, expected_recipient)
        key = recovered.key
        details = {'method': 'legacy-updater', 'wrapper_offset': recovered.wrapper_offset,
                   'byte_order': recovered.byte_order}
    return key, {**details, 'component_sha256' if component else 'updater_sha256': hashlib.sha256(data).hexdigest()}


def recover_catalog_key(root, catalog, model, expected_recipient, output, repository, *,
                        read_artifact=None, read_member=None):
    """Try verified sources and write only an exact matching key.

    Return public provenance; derivation material and keys never enter reports.
    The callbacks are internal seams for offline fixtures and decoder isolation.
    """
    read_artifact = read_artifact or fetch_release_source
    read_member = read_member or read_updater_member
    sources = [row for row in _select_sources(root, catalog, model)
               if row['kind'] != 'inventory-bundle' or row['repository'] == repository]
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
    preferred = [row for row in sources if json.dumps(row, sort_keys=True) in successful_sources]
    other = [row for row in sources if row not in preferred]
    queue = preferred + select(other, records, key=identity)
    if backoff.paused('key-source-downloads'):
        queue = []
    assessment = {'method': 'catalog-components', 'methods': ['embedded-updater-private-key', 'legacy-updater',
                                                       'embedded-component-private-key', 'embedded-inventory-private-key'],
                  'recovery_revision': RECOVERY_REVISION, 'model': model,
                  'checked_at': datetime.now(timezone.utc).isoformat(),
                  'eligible_sources': len(sources), 'selected_sources': len(queue),
                  'attempted_sources': 0, 'pending_sources': len(sources),
                  'status': 'in-progress' if queue else 'deferred' if sources else 'no-eligible-source'}
    state.setdefault('assessments', {}).setdefault(repository, {})[expected_recipient] = assessment
    # Persist the assessment even when no source is currently eligible.
    atomic_json(state_path, state)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    failures = []
    for source in queue:
        if winding_down():
            break
        assessment['attempted_sources'] += 1
        atomic_json(state_path, state)
        try:
            with tempfile.TemporaryDirectory(dir=output.parent, prefix='.recovery-') as temporary:
                body = Path(temporary) / 'capture'
                read_artifact(source, body, repository)
                backoff.clear('key-source-downloads')
                if body.stat().st_size != source['bytes'] or file_sha256(body) != source['sha256']:
                    raise ValueError('key source does not match its publication receipt')
                key, details = _source_key(body, source, model, expected_recipient, read_member)
                write_private_key_exclusive(output, key)
                proof = {**details, 'model': model, 'recipient_id': expected_recipient,
                         'repository': repository, 'source': source}
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
    raise ValueError(f'no matching component key in {len(failures)} attempted sources; '
                     f'{max(0, len(sources) - len(failures))} sources deferred')
