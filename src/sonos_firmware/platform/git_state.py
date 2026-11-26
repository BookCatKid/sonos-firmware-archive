"""Commit only validated metadata owned by an archive target."""
import json
import os
import re
import subprocess
from pathlib import Path

from ..catalog import validate_catalog
from .runner import state_path


def repository_name(root, environment=None):
    environment = os.environ if environment is None else environment
    if environment.get('GITHUB_REPOSITORY'):
        name = environment['GITHUB_REPOSITORY']
    else:
        remote = subprocess.check_output(['git', 'remote', 'get-url', 'origin'], cwd=root, text=True).strip()
        match = re.fullmatch(r'(?:https://github\.com/|git@github\.com:)([^/]+/[^/]+?)(?:\.git)?', remote)
        if not match:
            raise ValueError('specify a GitHub repository as owner/name')
        name = match[1]
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+', name):
        raise ValueError('repository must be owner/name')
    return name


def validate_metadata(root, target):
    for relative in target.metadata:
        path = root / relative
        paths = path.rglob('*.json') if path.is_dir() else [path] if path.exists() else []
        for document in paths:
            json.loads(document.read_text())
    if (root / 'data/catalog.json').exists():
        errors = validate_catalog(root)
        if errors:
            raise ValueError('catalog validation failed: ' + '; '.join(errors[:5]))


def commit_metadata(root: Path, target, environment=None):
    environment = os.environ if environment is None else environment
    if environment.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('automatic metadata commits run only in CI')
    validate_metadata(root, target)
    paths = [path for path in target.metadata if (root / path).exists() or
             subprocess.check_output(['git', 'ls-files', '--', path], cwd=root)]
    state = state_path(root, target)
    if state.exists():
        paths.append(str(state.relative_to(root)))
    if not paths:
        return False
    def git(*args, **kwargs):
        return subprocess.run(['git', *args], cwd=root, check=True, **kwargs)
    staged = subprocess.check_output(['git', 'diff', '--cached', '--name-only'], cwd=root, text=True).strip()
    if staged:
        raise ValueError('refusing to include pre-staged changes in an automated metadata commit')
    git('add', '--', *paths)
    if subprocess.run(['git', 'diff', '--cached', '--quiet'], cwd=root).returncode == 0:
        return False
    git('config', 'user.name', 'github-actions[bot]')
    git('config', 'user.email', '41898282+github-actions[bot]@users.noreply.github.com')
    git('commit', '-m', f'Update {target.name} archive state')
    # Preserve a reviewable checkpoint even if rebase or publication fails.
    if environment.get('RUNNER_TEMP'):
        checkpoint = Path(environment['RUNNER_TEMP']) / 'metadata-checkpoint.patch'
        with checkpoint.open('wb') as stream:
            git('format-patch', '-1', 'HEAD', '--stdout', stdout=stream)
    # Independent targets can publish disjoint metadata between pull and push.
    # Rebase and revalidate after each rejected push rather than losing progress.
    for _ in range(3):
        git('pull', '--rebase', 'origin', 'main')
        validate_metadata(root, target)
        if subprocess.run(['git', 'push', 'origin', 'HEAD:main'], cwd=root).returncode == 0:
            return True
    raise RuntimeError('metadata push failed after three validated rebase attempts')
