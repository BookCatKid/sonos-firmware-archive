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
from urllib.parse import urljoin, urlsplit

USER_AGENT = "sonos-firmware-archive/0.1 (+preservation research)"

OFFICIAL_FIRMWARE_HOSTS = {"update.sonos.com", "update-firmware.sonos.com"}


def official_firmware_url(url: str) -> bool:
    parsed = urlsplit(url)
    return parsed.scheme == "https" and parsed.hostname in OFFICIAL_FIRMWARE_HOSTS


class _OfficialRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not official_firmware_url(newurl):
            raise urllib.error.URLError(f"redirect to unsupported host: {newurl}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(_OfficialRedirect())


@dataclass(frozen=True)
class Candidate:
    version: str
    package_model: int
    url: str
    source: str
    filename: str


def _secure(url: str) -> str:
    return "https://" + url.removeprefix("http://") if url.startswith("http://") else url


def parse_manifest(path: str | Path, source_url: str | None = None) -> tuple[dict, list[Candidate]]:
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
    warnings: list[str] = []
    if root.get("version") not in {"1", "1.1"}:
        warnings.append(f"unrecognized manifest version: {root.get('version')}")

    def resolve(url: str) -> str:
        if not urlsplit(url).scheme:
            if source_url:
                return urljoin(source_url, url)
            warnings.append(f"relative URL needs manifest source: {url}")
        return url

    def expand(url: str, version: str, model: int) -> str:
        return resolve(url.replace(f"^{version}", f"{version}-1-{model}.upd"))

    def add(version: str, model: int, url: str, source: str) -> None:
        url = _secure(resolve(url))
        filename = url.rsplit("/", 1)[-1]
        candidates[url] = Candidate(version, model, url, source, filename)

    for image in root.findall(".//image"):
        url = (image.text or "").strip()
        if image.get("is_store") == "1" or url.split("?", 1)[0].endswith(".upm"):
            continue
        version = image.get("version", "unknown")
        model = int(image.get("model", "0").split(".", 1)[0])
        if url:
            add(version, model, expand(url, version, model), "image")
        elif root.get("base_url") and version == root.get("default_version"):
            add(version, model, expand(root.get("base_url"), version, model), "default-image")

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
        if not models:
            models = {int(i.get("model", "0").split(".", 1)[0]) for i in root.findall(".//image")}
            warnings.append("no supported-model enumeration; inferred default candidates need availability checks")
        for model in models:
            url = expand(base_url, default_version, model)
            add(default_version, model, url, "default-base")

    metadata = {
        "manifest_version": root.get("version"),
        "revision": root.get("revision"),
        "system_version": root.get("system_version"),
        "default_version": default_version,
        "warnings": sorted(set(warnings)),
        "manifest_links": sorted({resolve((e.text or "").strip())
            for e in root.iter() if (e.text or "").strip().split("?", 1)[0].endswith(".upm")}),
    }
    return metadata, sorted(candidates.values(), key=lambda item: (item.version, item.package_model, item.url))


def probe(candidate: Candidate, timeout: float = 20) -> dict:
    result = asdict(candidate)
    try:
        request = urllib.request.Request(candidate.url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with _OPENER.open(request, timeout=timeout) as response:
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
    except (urllib.error.URLError, TimeoutError, ValueError) as error:
        result.update(available=False, http_status=None, bytes=None, error=str(error))
    return result


def probe_all(candidates: list[Candidate], workers: int = 6, deadline: float | None = None) -> list[dict]:
    # All source checks stop after throttling/access challenges, not just the
    # directory sweep. A shared monotonic deadline keeps slow scans inside the
    # workflow runtime so the report is always written.
    # Keep the workers argument for caller compatibility.
    import time
    results = []
    blocked = False
    for candidate in candidates:
        if blocked:
            results.append({**asdict(candidate), "available": False, "error": "not checked after source challenge"})
            continue
        if deadline is not None and time.monotonic() > deadline:
            results.append({**asdict(candidate), "available": False, "error": "not checked within probe budget"})
            continue
        row = probe(candidate)
        results.append(row)
        blocked = row.get("http_status") in {403, 429}
        if blocked:
            row["error"] = "source challenge; remaining probes skipped"
        else:
            time.sleep(.25)
    return results


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
