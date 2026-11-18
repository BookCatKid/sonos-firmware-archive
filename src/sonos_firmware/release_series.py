"""Publish immutable captures across releases without a total asset ceiling."""
import re
import subprocess
import urllib.error
from pathlib import Path
from urllib.parse import quote

from . import releases
from .storage import file_sha256


def matching_tags(repository, prefix):
    """Enumerate the series by tag prefix, without scanning unrelated assets."""
    output = subprocess.check_output([
        'gh', 'api', '--paginate',
        f'repos/{repository}/git/matching-refs/tags/{quote(prefix, safe="")}',
        '--jq', '.[].ref',
    ], text=True)
    return [line.removeprefix('refs/tags/') for line in output.splitlines()]


class ReleaseSeries:
    """Return verified receipts carrying the actual partition used for a capture.

    Inventories are cached for one worker, not trusted across worker restarts.
    Construction performs no network requests, so known receipts need no series
    discovery. Unavailable inventories remain errors rather than empty releases.
    """

    def __init__(self, repository, base_tag, title, notes):
        self.repository, self.base_tag = repository, base_tag
        self.title, self.notes = title, notes
        self._tags = None
        self._assets = {}
        self._ids = {}
        self._unavailable = {}

    @property
    def errors(self):
        return [{'release_tag': tag, 'error': str(error)} for tag, error in self._unavailable.items()]

    def _ordinal(self, tag):
        if tag == self.base_tag:
            return 1
        match = re.fullmatch(re.escape(self.base_tag)+r'-part-([2-9]|[1-9][0-9]+)', tag)
        return int(match[1]) if match else 0

    def _inventory(self, tag, *, refresh=False):
        if not refresh and tag in self._unavailable:
            raise self._unavailable[tag]
        if refresh or tag not in self._assets:
            try:
                self._assets[tag] = releases.remote_assets(self.repository, tag)
            except urllib.error.HTTPError as error:
                if error.code != 404:
                    raise
                error.close()
                self._assets[tag] = {}
        return self._assets[tag]

    def _proof(self, tag, path, capture, remote):
        remote = releases.verified_asset(self.repository, tag, path.name, capture['bytes'],
                                        capture['sha256'], inventory={path.name: remote})
        if remote is None:
            raise RuntimeError(f'GitHub asset missing after upload: {tag}/{path.name}')
        return releases.publication_receipt(self.repository, tag, path.name, capture, remote)

    def reconcile(self, name, capture):
        """Find a verified capture without requiring cached source bytes."""
        path = Path(name)
        if path.name != name or name in {'', '.', '..'}:
            raise ValueError('release asset name must be a basename')
        if self._tags is None:
            self._tags = sorted({self.base_tag, *(tag for tag in matching_tags(
                self.repository, self.base_tag) if self._ordinal(tag))}, key=self._ordinal)
        # Search every partition before choosing free space. A lost response may
        # have stored this name in a later partition even if an earlier has room.
        for tag in self._tags:
            try:
                remote = self._inventory(tag).get(path.name)
            except Exception as error:
                if getattr(error, 'code', None) in {403, 429, 503}:
                    raise
                # Preserve fresh evidence elsewhere when one older release
                # cannot be reconciled. Report that uncertainty to the caller;
                # an unavailable partition never counts as empty or verified.
                self._unavailable[tag] = error
                continue
            if remote is not None:
                return self._proof(tag, path, capture, remote)
        return None

    def publish(self, path, capture):
        if (not path.is_file() or path.stat().st_size != capture['bytes'] or
                file_sha256(path) != capture['sha256']):
            raise ValueError('capture missing or does not match its receipt: '+path.name)
        if proof := self.reconcile(path.name, capture):
            return proof
        while True:
            tag = next((tag for tag in self._tags if tag not in self._unavailable and
                        len(self._inventory(tag)) < 1000), None)
            if tag is None:
                tag = self.base_tag+'-part-'+str(max(map(self._ordinal, self._tags))+1)
                self._tags.append(tag)
                self._assets[tag] = {}
            if tag not in self._ids:
                releases.ensure_release(self.repository, tag, self.title, self.notes)
                self._ids[tag] = releases.public_release(self.repository, tag)['id']
            try:
                remote = releases.upload_streaming(self.repository, self._ids[tag], path.name, path)
            except Exception as error:
                # Reconcile once even when the server response was lost. Only a
                # confirmed full release permits rollover; other failures retry
                # through the caller's durable queue instead of creating shards.
                observed = self._inventory(tag, refresh=True)
                if path.name in observed:
                    return self._proof(tag, path, capture, observed[path.name])
                detail = str(getattr(error, 'stderr', '') or error)
                if len(observed) >= 1000 and 'file_count' in detail:
                    continue
                raise
            proof = self._proof(tag, path, capture, remote)
            self._assets[tag][path.name] = remote
            return proof
