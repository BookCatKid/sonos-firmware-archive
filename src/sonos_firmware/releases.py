"""Shared GitHub Release access for every archive publisher.

Imports are side-effect free. Network access happens only when called by a worker.
"""
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
