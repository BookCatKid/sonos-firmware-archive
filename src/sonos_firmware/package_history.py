"""Incremental package-manifest history from Git objects, including deletions.

Only public metadata is fetched. Parent snapshots preserve files removed or
moved by a commit; immutable objects are cached outside committed state.
"""
import base64
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode

import yaml

from . import retry
from .storage import atomic_json

REPOSITORY = 'microsoft/winget-pkgs'
VENDOR_PATH = 'manifests/s/Sonos'


def _key(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ManifestHistory:
    def __init__(self, path, fetch, *, observations=None):
        self.path, self.fetch, self.observations = Path(path), fetch, observations
        self.state = json.loads(self.path.read_text()) if self.path.exists() else {}
        if self.state.get('schema_version', 1) != 1:
            raise ValueError('unsupported package history schema')
        self.state.setdefault('schema_version', 1)
        self.state.setdefault('jobs', {})
        self.state.setdefault('paths', {})
        self.state.setdefault('candidates', {})
        self.state['paths'].setdefault(VENDOR_PATH, {})
        revision = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
        if self.state.get('parser_revision') != revision:
            for job in self.state['jobs'].values():
                for field in ('checked_at', 'next_attempt_at', 'status'):
                    job.pop(field, None)
            self.state['parser_revision'] = revision

    def save(self):
        state = self.state
        pending = sum(row.get('status') != 'ok' for row in state['jobs'].values())
        pages = sum(not row.get('complete') or bool(row.get('head_page')) for row in state['paths'].values())
        errors = [{'source': key, 'error': row['error']} for group in ('jobs', 'paths')
                  for key, row in state[group].items() if row.get('error')]
        state.update(pending_total=pending+pages, errors=errors,
                     status='partial' if pending or pages or errors else 'ok')
        atomic_json(self.path, state)

    def get(self, endpoint):
        def complete(document):
            if not isinstance(document, dict):
                return False
            if '/git/trees/' in endpoint:
                return document.get('truncated') is False and isinstance(document.get('tree'), list)
            if '/git/blobs/' in endpoint:
                return isinstance(document.get('content'), str)
            return isinstance(document.get('tree'), dict) and bool(document['tree'].get('sha'))
        cached = self.observations/(_key(endpoint)+'.json') if self.observations else None
        if cached and cached.exists():
            try:
                document = json.loads(cached.read_text())
                if complete(document):
                    return document
            except (OSError, ValueError):
                pass
        document = self.fetch(endpoint)
        if cached and complete(document):
            try:
                atomic_json(cached, document)
            except OSError:
                pass
        return document

    def add(self, role, sha, path='', route=()):
        key = _key([role, sha, path, route])
        self.state['jobs'].setdefault(key, {'role': role, 'sha': sha, 'path': path, 'route': list(route)})

    def page(self, path, record, refresh_hours):
        head = bool(record.get('complete'))
        page = record.get('head_page' if head else 'next_page', 1)
        if page == 1:
            record['anchor'] = record.get('head_sha')
        endpoint = f'repos/{REPOSITORY}/commits?' + urlencode({'path': path, 'per_page': 100, 'page': page})
        rows = self.fetch(endpoint)  # Mutable pages must never use immutable-object cache.
        if not isinstance(rows, list):
            raise ValueError('invalid Git commit page')
        if page == 1 and rows:
            record['new_head_sha'] = rows[0]['sha']
        for commit in rows:
            self.add('route', commit['commit']['tree']['sha'], route=VENDOR_PATH.split('/'))
            for parent in commit.get('parents', []):
                self.add('parent', parent['sha'])
        done = len(rows) < 100 or (head and any(row['sha'] == record.get('anchor') for row in rows))
        if done:
            record.update(complete=True, head_sha=record.get('new_head_sha'))
            record.pop('head_page', None)
            record.pop('next_page', None)
        else:
            record['head_page' if head else 'next_page'] = page+1
        record.update(retry.checkpoint(record, success=True, success_hours=refresh_hours if done else 0))
        record.pop('error', None)

    def blob(self, job, document):
        if document.get('encoding', 'base64') != 'base64':
            raise ValueError('unsupported Git blob encoding')
        data = yaml.load(base64.b64decode(document['content']).decode(), Loader=yaml.BaseLoader)
        if not isinstance(data, dict):
            raise ValueError('invalid installer manifest')
        installers = data.get('Installers', [data] if data.get('InstallerUrl') else [])
        if not isinstance(installers, list):
            raise ValueError('invalid installer records')
        errors = []
        for installer in installers:
            if not isinstance(installer, dict):
                errors.append('invalid installer record')
                continue
            url = installer.get('InstallerUrl', data.get('InstallerUrl'))
            if not isinstance(url, str) or not url:
                errors.append('installer record has no URL')
                continue
            digest = installer.get('InstallerSha256', data.get('InstallerSha256'))
            if digest is not None and (not isinstance(digest, str) or len(digest) != 64 or
                                       any(c not in '0123456789abcdefABCDEF' for c in digest)):
                errors.append('installer record has an invalid SHA-256')
                digest = None
            digest = digest.lower() if digest else None
            version = data.get('PackageVersion', data.get('Version', Path(job['path']).parent.name))
            key = _key([url, digest, version])
            candidate = self.state['candidates'].setdefault(key, {'url': url, 'expected_sha256': digest,
                'version': version, 'platform': 'windows', 'sources': []})
            candidate['advertised_by'] = {'repository': REPOSITORY, 'path': job['path'], 'git_blob': job['sha']}
            source = 'winget-history:'+job['path']+':'+job['sha']
            if source not in candidate['sources']:
                candidate['sources'].append(source)
        if errors:
            raise ValueError('; '.join(errors))

    def work(self, job):
        role, sha = job['role'], job['sha']
        if role == 'parent':
            document = self.get(f'repos/{REPOSITORY}/git/commits/{sha}')
            self.add('route', document['tree']['sha'], route=VENDOR_PATH.split('/'))
            return
        if role == 'blob':
            self.blob(job, self.get(f'repos/{REPOSITORY}/git/blobs/{sha}'))
            return
        document = self.get(f'repos/{REPOSITORY}/git/trees/{sha}')
        if document.get('truncated') is not False or not isinstance(document.get('tree'), list):
            raise ValueError('incomplete Git tree')
        entries = document['tree']
        if role == 'route':
            remaining = job['route']
            if not remaining:
                role = 'scan'
            else:
                matching = [row for row in entries if row.get('path') == remaining[0] and row.get('type') == 'tree']
                for row in matching:
                    self.add('route', row['sha'], '/'.join(filter(None, (job['path'], row['path']))), remaining[1:])
                if not matching and job['path'] == 'manifests':
                    # Before WinGet's alphabetical layout, packages used vendor
                    # directories or combined YAML files directly under manifests.
                    for row in entries:
                        name = row.get('path', '')
                        if name.lower() == 'sonos' or name.lower().startswith('sonos.'):
                            path = 'manifests/'+name
                            self.state['paths'].setdefault(path, {})
                            self.add('scan' if row['type'] == 'tree' else 'blob', row['sha'], path)
                return
        if role == 'scan':
            for row in entries:
                path = '/'.join(filter(None, (job['path'], row['path'])))
                if row['type'] == 'tree':
                    self.add('scan', row['sha'], path)
                elif row['type'] == 'blob' and path.lower().endswith(('.yaml', '.yml')):
                    self.add('blob', row['sha'], path)

    def advance(self, *, limit=64, refresh_hours=6):
        if limit <= 0:
            raise ValueError('metadata work budget must be positive')
        paths = self.state['paths']
        selected = retry.select(list(paths), paths, key=lambda path: path, limit=1,
                                state=self.state, queue='commit-pages')
        used = 0
        for path in selected:
            record = paths[path]
            try:
                self.page(path, record, refresh_hours)
            except Exception as error:
                record.update(retry.checkpoint(record), error=str(error))
            used += 1
            self.save()
        processed = set()
        while used < limit:
            jobs = self.state['jobs']
            pending = [key for key, job in jobs.items() if job.get('status') != 'ok' and key not in processed]
            pending.sort(key=lambda key: (jobs[key]['role'] == 'parent', -len(Path(jobs[key]['path']).parts)))
            selected = retry.select(pending, jobs, key=lambda key: key, limit=1,
                                    state=self.state, queue='git-objects')
            if not selected:
                break
            key = selected[0]
            job = jobs[key]
            try:
                self.work(job)
                job.update(retry.checkpoint(job, success=True), status='ok')
                job.pop('error', None)
            except Exception as error:
                job.update(retry.checkpoint(job), status='pending', error=str(error))
                if type(error).__name__ == 'GitHubSourcePaused':
                    self.save()
                    break
            used += 1
            processed.add(key)
            self.save()
        self.save()
        return list(self.state['candidates'].values())
