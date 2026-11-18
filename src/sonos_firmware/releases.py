"""Shared GitHub Release access for every archive publisher.

Imports are side-effect free. Network access happens only when called by a worker.
"""
import hashlib
import json
import mimetypes
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from .storage import file_sha256

USER_AGENT = "sonos-firmware-archive/1"


def public_release(repository: str, tag: str) -> dict:
    request = urllib.request.Request(
        f"https://api.github.com/repos/{repository}/releases/tags/{tag}",
        headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code != 403:
            raise
        owner, name = repository.split("/", 1)
        query = """
          query($owner:String!, $name:String!, $tag:String!) {
            repository(owner:$owner, name:$name) {
              release(tagName:$tag) { databaseId url tagName }
            }
          }
        """
        payload = json.loads(subprocess.check_output(
            [
                "gh", "api", "graphql", "-f", f"query={query}",
                "-f", f"owner={owner}", "-f", f"name={name}", "-f", f"tag={tag}",
            ],
            text=True,
        ))
        release = payload["data"]["repository"]["release"]
        if release is None:
            raise urllib.error.HTTPError(request.full_url, 404, "release not found", {}, None)
        return {"id": release["databaseId"], "html_url": release["url"], "tag_name": tag}


def graphql_assets(repository: str, tag: str) -> list[dict]:
    owner, name = repository.split("/", 1)
    query = """
      query($owner:String!, $name:String!, $tag:String!, $after:String) {
        repository(owner:$owner, name:$name) {
          release(tagName:$tag) {
            releaseAssets(first:100, after:$after) {
              nodes { id name size downloadUrl digest }
              pageInfo { hasNextPage endCursor }
            }
          }
        }
      }
    """
    result = []
    cursor = None
    while True:
        command = [
            "gh", "api", "graphql", "-f", f"query={query}",
            "-f", f"owner={owner}", "-f", f"name={name}", "-f", f"tag={tag}",
        ]
        if cursor is not None:
            command.extend(["-f", f"after={cursor}"])
        payload = json.loads(subprocess.check_output(command, text=True))
        connection = payload["data"]["repository"]["release"]["releaseAssets"]
        result.extend(
            {
                "id": item["id"],
                "name": item["name"],
                "size": item["size"],
                "browser_download_url": item["downloadUrl"],
                "digest": item["digest"],
            }
            for item in connection["nodes"]
        )
        if not connection["pageInfo"]["hasNextPage"]:
            return result
        cursor = connection["pageInfo"]["endCursor"]


def remote_assets(repository: str, tag: str) -> dict[str, dict]:
    release = public_release(repository, tag)
    assets = {}
    for page in range(1, 101):
        request = urllib.request.Request(
            f"https://api.github.com/repos/{repository}/releases/{release['id']}"
            f"/assets?per_page=100&page={page}&_archive_ts={int(time.time())}",
            headers={"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=60) as response:
                items = json.load(response)
        except urllib.error.HTTPError as error:
            if error.code != 403:
                raise
            # A later-page challenge must not turn a partial REST result into
            # a successful inventory. GraphQL enumerates the entire release.
            return {item['name']: item for item in graphql_assets(repository, tag)}
        assets.update((item["name"], item) for item in items)
        if len(items) < 100:
            return assets
    raise RuntimeError(f'release asset inventory exceeded pagination limit: {repository}/{tag}')


def upload_asset(repository: str, release_id: int, name: str, path: Path) -> None:
    token = subprocess.check_output(["gh", "auth", "token"], text=True).strip()
    query = urllib.parse.urlencode({"name": name})
    request = urllib.request.Request(
        f"https://uploads.github.com/repos/{repository}/releases/{release_id}/assets?{query}",
        data=path.read_bytes(),
        method="POST",
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "Content-Type": mimetypes.guess_type(name)[0] or "application/octet-stream",
            "X-GitHub-Api-Version": "2022-11-28",
        },
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        if response.status != 201:
            raise RuntimeError(f"GitHub upload failed for {name}: HTTP {response.status}")


def receipt_matches_repository(record: dict, repository: str) -> bool:
    """Whether recorded publication URLs belong to the selected repository."""
    entries = list(record.get('components', []))
    if record.get('complete_wrapper'):
        entries.append(record['complete_wrapper'])
    if not entries:
        entries = [record]
    prefix = f'https://github.com/{repository}/releases/download/'.lower()
    return all(entry.get('release_url', '').lower().startswith(prefix) for entry in entries)


def ensure_release(repository: str, tag: str, title: str, notes: str) -> None:
    exists = subprocess.run(['gh', 'release', 'view', tag, '--repo', repository],
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0
    if not exists:
        subprocess.run(['gh', 'release', 'create', tag, '--repo', repository,
                        '--title', title, '--notes', notes], check=True)


def verified_asset(repository: str, tag: str, name: str, size: int, digest: str, *, inventory=None) -> dict | None:
    """Verify an asset, optionally using an inventory fetched for this operation."""
    try:
        remote = (remote_assets(repository, tag) if inventory is None else inventory).get(name)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            error.close()
            return None
        raise
    if remote is None:
        return None
    if remote.get('size') != size or remote.get('name', name) != name:
        raise RuntimeError(f'GitHub asset mismatch: {tag}/{name}')
    if not (remote.get('browser_download_url') or remote.get('url')):
        raise ValueError('verified GitHub asset has no publication URL')
    if verified_remote_digest(remote) != digest:
        raise RuntimeError(f'GitHub asset mismatch: {tag}/{name}')
    return remote


def publish_verified(repository: str, tag: str, path: Path, size: int, digest: str) -> dict:
    """Publish without clobbering and return only a size/hash-reconciled asset."""
    remote = verified_asset(repository, tag, path.name, size, digest)
    if remote is None:
        subprocess.run(['gh', 'release', 'upload', tag, str(path), '--repo', repository], check=True)
        remote = verified_asset(repository, tag, path.name, size, digest)
    if remote is None:
        raise RuntimeError(f'GitHub asset missing after upload: {tag}/{path.name}')
    return remote


def publish_collection(repository: str, tag: str, files: list[tuple[Path, dict]]) -> dict:
    """Reconcile a collection before upload; return only verified publications.

    A verified remote capture needs no local file. Missing assets require exact
    local bytes. Never replace an existing name, including during a retry.
    """
    expected = {}
    for path, capture in files:
        name = capture['release_asset']
        if not isinstance(name, str) or not name or Path(name).name != name or name in {'.', '..'}:
            raise ValueError('release asset name must be a basename')
        identity = (capture['bytes'], capture['sha256'])
        if (not isinstance(identity[0], int) or isinstance(identity[0], bool) or identity[0] < 0 or
            not isinstance(identity[1], str) or len(identity[1]) != 64 or
            any(char not in '0123456789abcdef' for char in identity[1])):
            raise ValueError('release collection needs sizes and SHA-256 receipts')
        if name in expected and expected[name][1] != identity:
            raise ValueError('conflicting capture identities for release asset: ' + name)
        expected[name] = (path, identity)
    if not expected:
        return {}
    observed = remote_assets(repository, tag)

    def matches(name, asset):
        size, digest = expected[name][1]
        if asset['size'] != size or verified_remote_digest(asset) != digest:
            raise RuntimeError(f'remote asset exists with different content: {tag}/{name}')
        if not (asset.get('browser_download_url') or asset.get('url')):
            raise ValueError('verified GitHub asset has no publication URL')

    missing = []
    # Complete preflight before any mutation: a late collision must not leave
    # unrelated captures partially uploaded.
    for name, (path, (size, digest)) in expected.items():
        if name in observed:
            matches(name, observed[name])
        else:
            if not path.is_file() or path.stat().st_size != size or file_sha256(path) != digest:
                raise ValueError('capture missing or does not match its receipt: ' + name)
            missing.append((name, path))
    if missing:
        release = public_release(repository, tag)
        for name, path in missing:
            upload_streaming(repository, release['id'], name, path)
            time.sleep(1.1)
        for attempt in range(10):
            observed = remote_assets(repository, tag)
            for name in expected.keys() & observed.keys():
                matches(name, observed[name])
            if expected.keys() <= observed.keys():
                break
            if attempt < 9:
                time.sleep(2)
        else:
            raise RuntimeError(f'GitHub assets missing after upload: {tag}')
    return {name: observed[name] for name in expected}


def publication_receipt(repository, tag, name, capture, remote):
    return {'status': 'verified', 'repository': repository, 'release_tag': tag, 'release_asset': name,
            'release_url': remote.get('browser_download_url') or remote['url'],
            'sha256': capture['sha256'], 'bytes': capture['bytes']}


def download_verified(source, destination, repository):
    """Fetch one literal release asset and verify the recorded capture identity."""
    name = source['release_asset']
    if not isinstance(name, str) or not name or Path(name).name != name or name in {'.', '..'}:
        raise ValueError('release asset name must be a basename')
    size, digest = source['bytes'], source['sha256']
    if (not isinstance(size, int) or isinstance(size, bool) or size <= 0 or not isinstance(digest, str) or
        len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest)):
        raise ValueError('release asset needs a positive size and SHA-256 receipt')
    if destination.exists():
        if destination.stat().st_size == size and file_sha256(destination) == digest:
            return
        raise FileExistsError('download destination already contains another capture')
    # gh matches --pattern with Go filepath.Match; escape glob metacharacters.
    # https://github.com/cli/cli/blob/trunk/pkg/cmd/release/download/download.go
    pattern = ''.join('\\' + char if char in '\\*?[]' else char for char in name)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=destination.parent, prefix='release-download-') as temporary:
        payload = Path(temporary)/'payload'
        command = ['gh', 'release', 'download', '--repo', repository, '--pattern='+pattern,
                   '--output', str(payload), '--', source['release_tag']]
        deadline = time.monotonic() + 300
        with subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) as process:
            try:
                while process.poll() is None:
                    if payload.exists() and payload.stat().st_size > size:
                        raise ValueError('release asset exceeds its preservation receipt size')
                    if time.monotonic() >= deadline:
                        raise TimeoutError('release asset download timed out')
                    time.sleep(.1)
                if process.returncode:
                    raise subprocess.CalledProcessError(process.returncode, command)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
        if payload.stat().st_size != size or file_sha256(payload) != digest:
            raise ValueError('release asset does not match its preservation receipt')
        destination.hardlink_to(payload)


def verified_remote_digest(asset: dict) -> str:
    digest = (asset.get("digest") or "").removeprefix("sha256:")
    if digest:
        return digest
    hasher = hashlib.sha256()
    request = urllib.request.Request(
        asset["browser_download_url"], headers={"User-Agent": "sonos-firmware-archive/1"}
    )
    with urllib.request.urlopen(request, timeout=300) as response:
        while block := response.read(1024 * 1024):
            hasher.update(block)
    return hasher.hexdigest()


def upload_streaming(repository: str, release_id: int, name: str, path: Path) -> dict:
    endpoint = (
        f"repos/{repository}/releases/{release_id}/assets?"
        + urllib.parse.urlencode({"name": name})
    )
    response = subprocess.run([
        "gh", "api", "--method", "POST",
        "-H", f"Content-Type: {mimetypes.guess_type(name)[0] or 'application/octet-stream'}",
        "--input", str(path), f"https://uploads.github.com/{endpoint}",
    ], check=True, capture_output=True, text=True)
    return json.loads(response.stdout)
