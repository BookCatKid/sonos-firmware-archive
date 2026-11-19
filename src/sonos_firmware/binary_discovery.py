"""Feed verified archived binaries back into independent GPL discovery."""
from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import gpl_binary_evidence
from .releases import download_verified
from .retry import checkpoint, select
from .storage import atomic_json, file_sha256


def _load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def preserved_sources(root, repository, errors=None):
    """Normalize the archive's own publication receipts, not external URL lists."""
    sources = {}
    errors = errors if errors is not None else []

    def add(row, provenance, inherited_tag=None):
        digest, size, name = row.get('sha256'), row.get('bytes'), row.get('release_asset')
        tag = row.get('release_tag') or inherited_tag
        if (not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)
            or not isinstance(size, int) or isinstance(size, bool) or size <= 0 or not tag or not name):
            return
        if not isinstance(name, str) or Path(name).name != name or name in {'.', '..'}:
            errors.append({'metadata': provenance, 'error': 'preserved release asset must be a basename'})
            return
        if row.get('repository') and row['repository'] != repository:
            return
        url = row.get('release_url')
        if url and not url.startswith(f'https://github.com/{repository}/releases/download/'):
            return
        publication = {'release_tag': tag, 'release_asset': name, 'sha256': digest, 'bytes': size,
                       'metadata': provenance}
        group = sources.setdefault(digest, {'sha256': digest, 'sources': []})
        if publication not in group['sources']:
            group['sources'].append(publication)

    paths = set((root/'data/apps').glob('*archive.json')) | {root/'data/catalog.json'}
    if (root/'config/targets.json').exists():
        from .platform.registry import targets
        for target in targets(root).values():
            for relative in target.metadata:
                location = root/relative
                paths.update(location.rglob('*.json') if location.is_dir() else [location])

    def rows(document, key):
        value = document.get(key, [])
        return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []

    for path in sorted(paths):
        if not path.exists() or path.suffix != '.json':
            continue
        provenance = str(path.relative_to(root))
        try:
            document = _load(path, {})
        except (ValueError, OSError) as error:
            errors.append({'metadata': provenance, 'error': str(error)})
            continue
        if not isinstance(document, dict):
            continue
        for row in rows(document, 'artifacts'):
            add(row, provenance)
            for component in rows(row, 'components'):
                if component.get('verified') is not False:
                    add(component, provenance, row.get('release_tag'))
            if isinstance(row.get('complete_wrapper'), dict):
                add(row['complete_wrapper'], provenance, row.get('release_tag'))
        for deployment in rows(document, 'deployments'):
            for resource in rows(deployment, 'resources'):
                add(resource, provenance, deployment.get('release_tag'))
        for package in rows(document, 'packages'):
            if package.get('artifact_status') == 'preserved':
                inventory = package.get('inventory', {}).get('publications', {}).get('bundle', {})
                if inventory.get('status') == 'verified':
                    add(inventory, provenance)
        for row in rows(document, 'raw_images'):
            add(row, provenance)
        for package in rows(document, 'packages'):
            if package.get('artifact_status') == 'preserved':
                add(package, provenance)
    return list(sources.values())


def discover_preserved(root, work, repository, *, limit=4, max_bytes=768*1024*1024,
                       max_source_bytes=1024*1024*1024, max_entry_bytes=300*1024*1024,
                       acquire=download_verified, scan=None):
    if any(not isinstance(value, int) or isinstance(value, bool) or value <= 0
           for value in (limit, max_bytes, max_source_bytes, max_entry_bytes)):
        raise ValueError('binary discovery limits must be positive integers')
    path = root/'data/gpl/live/binary-evidence.json'
    document = _load(path, {'schema_version': 1, 'scans': {}, 'version_evidence': {}, 'repositories': {}})
    if document.get('schema_version') != 1:
        raise ValueError('unsupported binary discovery checkpoint schema')
    records = document.setdefault('repositories', {}).setdefault(repository, {})
    errors = []
    sources = preserved_sources(root, repository, errors)
    digest = hashlib.sha256(Path(__file__).read_bytes() + Path(gpl_binary_evidence.__file__).read_bytes() +
                            Path(gpl_binary_evidence.firmware_inventory.__file__).read_bytes()).hexdigest()
    settings = {'decoder': digest, 'max_bytes': max_bytes, 'max_source_bytes': max_source_bytes,
                'max_entry_bytes': max_entry_bytes}
    revision = hashlib.sha256(json.dumps(settings, sort_keys=True).encode()).hexdigest()
    document.setdefault('revisions', {})[revision] = settings
    eligible = {key: row for key, row in records.items() if row.get('revision') == revision}
    for key, row in list(eligible.items()):
        if row.get('status') == 'scanned':
            eligible[key] = {**row, 'next_attempt_at': '9999-01-01T00:00:00+00:00'}
    for candidate in sources:
        candidate['source_revision'] = hashlib.sha256(json.dumps(candidate['sources'], sort_keys=True).encode()).hexdigest()
        previous = eligible.get(candidate['sha256'], {})
        if previous.get('status') == 'failed' and previous.get('source_revision') != candidate['source_revision']:
            eligible.pop(candidate['sha256'], None)
    queue = select(sources, eligible, key=lambda row: row['sha256'], limit=limit,
                   state=document, queue=repository)
    work.mkdir(parents=True, exist_ok=True)
    attempted = 0

    def save():
        found = {}
        for capture in document['scans'].values():
            for version, refs in capture['version_evidence'].items():
                for ref in refs:
                    label = f"binary:{capture['filename']}:{ref.get('entry') or '<raw>'}:{ref['offset']}:{ref['format']}"
                    found.setdefault(version, set()).add(label)
        document['version_evidence'] = {key: sorted(value) for key, value in sorted(found.items())}
        pending = sum(records.get(row['sha256'], {}).get('status') != 'scanned' or
                      records.get(row['sha256'], {}).get('revision') != revision for row in sources)
        failures = [{'sha256': row['sha256'], 'error': records[row['sha256']].get('error')}
                    for row in sources if records.get(row['sha256'], {}).get('status') == 'failed']
        document.update(updated=datetime.now(timezone.utc).isoformat(), repository=repository,
                        attempted_this_run=attempted, pending_total=pending, errors=errors + failures,
                        status='partial' if pending or errors or failures else 'ok')
        atomic_json(path, document)

    for candidate in queue:
        identity = candidate['sha256']
        attempted += 1
        next_source = records.get(identity, {}).get('next_source', 0)
        try:
            locations = [row for row in candidate['sources'] if row['bytes'] <= max_source_bytes]
            if not locations:
                raise ValueError('preserved binary exceeds the configured acquisition byte limit')
            start = next_source % len(locations)
            locations = locations[start:] + locations[:start]
            with tempfile.TemporaryDirectory(dir=work, prefix='binary-') as temporary:
                last_error = None
                for source in locations[:4]:
                    next_source = (next_source + 1) % len(locations)
                    payload = Path(temporary)/source['release_asset']
                    try:
                        acquire(source, payload, repository)
                        if payload.stat().st_size != source['bytes'] or file_sha256(payload) != identity:
                            raise ValueError('binary discovery source does not match its preservation receipt')
                        break
                    except Exception as error:
                        payload.unlink(missing_ok=True)
                        last_error = error
                else:
                    raise last_error
                named_archive = next((row['release_asset'] for row in candidate['sources']
                    if row['release_asset'].lower().endswith(gpl_binary_evidence.TAR_SUFFIXES)), payload.name)
                inspection = payload.parent/named_archive
                if inspection != payload:
                    inspection.hardlink_to(payload)
                result = (scan or gpl_binary_evidence.scan_binary)(inspection, max_bytes=max_bytes, max_entry_bytes=max_entry_bytes)
            previous = document['scans'].get(identity, {})
            evidence = previous.get('version_evidence', {})
            for version, refs in result['version_evidence'].items():
                retained = evidence.setdefault(version, [])
                retained.extend(ref for ref in refs if ref not in retained)
            result.update(sources=candidate['sources'], verified_source=source, revision=revision, version_evidence=evidence,
                          checked_at=datetime.now(timezone.utc).isoformat())
            if evidence or not result['complete']:
                document['scans'][identity] = result
            else:
                document['scans'].pop(identity, None)
            records[identity] = {'status': 'scanned' if result['complete'] else 'partial', 'revision': revision,
                                 'bytes_scanned': result.get('bytes_scanned', 0)}
            if not result['complete']:
                records[identity].update(checkpoint(eligible.get(identity)))
        except Exception as error:
            records[identity] = {**checkpoint(records.get(identity)), 'status': 'failed',
                                 'revision': revision, 'source_revision': candidate['source_revision'],
                                 'next_source': next_source, 'error': str(error)}
        save()
    save()
    return document
