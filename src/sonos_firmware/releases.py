"""Shared GitHub Release access for every archive publisher.

Imports are side-effect free. Network access happens only when called by a worker.
"""
import hashlib
import json
import mimetypes
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

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


def verified_asset(repository: str, tag: str, name: str, size: int, digest: str) -> dict | None:
    try:
        remote = remote_assets(repository, tag).get(name)
    except urllib.error.HTTPError as error:
        if error.code == 404:
            error.close()
            return None
        raise
    if remote is None:
        return None
    if remote.get('size') != size or (remote.get('digest') or '').removeprefix('sha256:') != digest:
        raise RuntimeError(f'GitHub asset mismatch: {tag}/{name}')
    if not (remote.get('browser_download_url') or remote.get('url')):
        raise ValueError('verified GitHub asset has no publication URL')
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


def upload_streaming(repository: str, release_id: int, name: str, path: Path) -> None:
    endpoint = (
        f"repos/{repository}/releases/{release_id}/assets?"
        + urllib.parse.urlencode({"name": name})
    )
    subprocess.run([
        "gh", "api", "--method", "POST",
        "-H", f"Content-Type: {mimetypes.guess_type(name)[0] or 'application/octet-stream'}",
        "--input", str(path), f"https://uploads.github.com/{endpoint}",
    ], check=True, stdout=subprocess.DEVNULL)
