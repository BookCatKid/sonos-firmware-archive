"""Discover firmware artifacts named by a signed Sonos update manifest."""

from __future__ import annotations

import concurrent.futures
import hashlib
import json
import os
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path

USER_AGENT = "sonos-firmware-archive/0.1 (+preservation research)"


@dataclass(frozen=True)
class Candidate:
    version: str
    package_model: int
    url: str
    source: str
    filename: str


def _secure(url: str) -> str:
    return "https://" + url.removeprefix("http://") if url.startswith("http://") else url


def parse_manifest(path: str | Path) -> tuple[dict, list[Candidate]]:
    # Some historical mirrors preserved a complete XML document but truncated
    # the trailing signature comment. Parse only through the signed document's
    # closing element while retaining the original bytes untouched on disk.
    text = Path(path).read_text(encoding="utf-8")
    closing = "</update_manifest>"
    end = text.find(closing)
    if end < 0:
        raise ValueError("manifest has no closing update_manifest element")
    root = ET.fromstring(text[: end + len(closing)])
    candidates: dict[str, Candidate] = {}

    def add(version: str, model: int, url: str, source: str) -> None:
        url = _secure(url)
        filename = url.rsplit("/", 1)[-1]
        candidates[url] = Candidate(version, model, url, source, filename)

    for image in root.findall(".//image"):
        url = (image.text or "").strip()
        if not url or image.get("is_store") == "1":
            continue
        version = image.get("version", "unknown")
        model = int(image.get("model", "0").split(".", 1)[0])
        url = url.replace(f"/^{version}", f"/{version}-1-{model}.upd")
        add(version, model, url, "image")

    base_url = root.get("base_url")
    default_version = root.get("default_version")
    if base_url and default_version:
        models: set[int] = set()
        for model_list in root.findall("./supported_models/model_list"):
            if model_list.get("swgen") != root.get("swgen"):
                continue
            for value in (model_list.text or "").split(","):
                if value.strip():
                    models.add(int(value.strip().split(".", 1)[0]))
        for model in models:
            url = base_url.replace(f"/^{default_version}", f"/{default_version}-1-{model}.upd")
            add(default_version, model, url, "default-base")

    metadata = {
        "manifest_version": root.get("version"),
        "revision": root.get("revision"),
        "system_version": root.get("system_version"),
        "default_version": default_version,
    }
    return metadata, sorted(candidates.values(), key=lambda item: (item.version, item.package_model, item.url))


def probe(candidate: Candidate, timeout: float = 20) -> dict:
    request = urllib.request.Request(candidate.url, method="HEAD", headers={"User-Agent": USER_AGENT})
    result = asdict(candidate)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            result.update(
                available=response.status == 200,
                http_status=response.status,
                bytes=int(response.headers["Content-Length"]) if response.headers.get("Content-Length") else None,
                resolved_url=response.url,
                etag=response.headers.get("ETag"),
                last_modified=response.headers.get("Last-Modified"),
            )
    except urllib.error.HTTPError as error:
        result.update(available=False, http_status=error.code, bytes=None)
    except (urllib.error.URLError, TimeoutError) as error:
        result.update(available=False, http_status=None, bytes=None, error=str(error))
    return result


def probe_all(candidates: list[Candidate], workers: int = 6) -> list[dict]:
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(probe, candidates))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def download(candidate: dict, directory: str | Path) -> dict:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    destination = directory / candidate["filename"]
    expected = candidate.get("bytes")
    if destination.exists() and (expected is None or destination.stat().st_size == expected):
        return {**candidate, "local_file": destination.name, "sha256": _sha256(destination), "downloaded": True}
    temporary = destination.with_suffix(destination.suffix + ".part")
    request = urllib.request.Request(candidate["url"], headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
            while block := response.read(1024 * 1024):
                output.write(block)
        if expected is not None and temporary.stat().st_size != expected:
            raise ValueError(f"size mismatch for {candidate['filename']}")
        os.replace(temporary, destination)
        return {**candidate, "local_file": destination.name, "sha256": _sha256(destination), "downloaded": True}
    except Exception as error:
        return {**candidate, "downloaded": False, "download_error": str(error)}


def download_all(candidates: list[dict], directory: str | Path, workers: int = 3) -> list[dict]:
    available = [candidate for candidate in candidates if candidate.get("available")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda item: download(item, directory), available))


def write_json(path: str | Path, value) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
