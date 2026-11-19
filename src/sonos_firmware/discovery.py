"""Discover firmware artifacts named by a signed Sonos update manifest."""

from __future__ import annotations

import concurrent.futures
import gzip
import io
import os
import re
import tempfile
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urljoin, urlsplit

from .storage import atomic_json, file_sha256 as _sha256
from .source_urls import sonos_url as official_firmware_url, open_advertised, advertised_url

USER_AGENT = "sonos-firmware-archive/0.1 (+preservation research)"

@dataclass(frozen=True)
class Candidate:
    version: str
    package_model: int | None
    url: str
    source: str
    filename: str
    advertised_by: str | None = None


def _secure(url: str) -> str:
    return "https://" + url.removeprefix("http://") if url.startswith("http://") else url


def expand_metadata_base(uri: str, models=range(256), *, advertised_by=None) -> list[Candidate]:
    """Expand an observed UPS caret base, without inventing a campaign path."""
    uri = _secure(uri)
    if not advertised_url(uri, advertised_by):
        return []
    parsed = urlsplit(uri)
    match = re.fullmatch(r'(.*/)[\^](\d+\.\d+-\d+)', parsed.path)
    if not match:
        return []
    version = match[2]
    result = []
    for model in models:
        filename = f'{version}-1-{model}.upd'
        url = parsed._replace(path=match[1] + filename, fragment='').geturl()
        result.append(Candidate(version, model, url, 'metadata-base', filename, advertised_by))
    return result


def host_counterpart(candidate: Candidate) -> Candidate | None:
    """Try the other public production host using the observed path unchanged."""
    parsed = urlsplit(candidate.url)
    peers = {'update.sonos.com': 'update-firmware.sonos.com',
             'update-firmware.sonos.com': 'update.sonos.com'}
    if not official_firmware_url(candidate.url) or parsed.hostname not in peers:
        return None
    url = parsed._replace(netloc=peers[parsed.hostname]).geturl()
    return Candidate(candidate.version, candidate.package_model, url, 'host-counterpart', candidate.filename)


def parse_manifest(path: str | Path, source_url: str | None = None) -> tuple[dict, list[Candidate]]:
    # Some historical mirrors preserved a complete XML document but truncated
    # the trailing signature comment. Parse only through the signed document's
    # closing element while retaining the original bytes untouched on disk.
    data = Path(path).read_bytes()
    if data.startswith(b'\x1f\x8b'):
        with gzip.GzipFile(fileobj=io.BytesIO(data)) as stream:
            data = stream.read(2_000_001)
        if len(data) > 2_000_000:
            raise ValueError('decoded manifest exceeds 2 MB')
    text = data.decode('utf-8')
    closing = "</update_manifest>"
    end = text.find(closing)
    if end < 0:
        raise ValueError("manifest has no closing update_manifest element")
    root = ET.fromstring(text[: end + len(closing)])
    candidates: dict[str, Candidate] = {}
    warnings: list[str] = []
    landing_links = []
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
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        candidates[url] = Candidate(version, model, url, source, filename)

    for image in root.findall(".//image"):
        url = (image.text or "").strip()
        if image.get('is_store') == '1':
            if url:
                landing_links.append(_secure(resolve(url)))
            continue
        if url.split("?", 1)[0].endswith(".upm"):
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
        "base_url": _secure(resolve(root.get("base_url"))) if root.get("base_url") else None,
        "manifest_version": root.get("version"),
        "revision": root.get("revision"),
        "system_version": root.get("system_version"),
        "default_version": default_version,
        "warnings": sorted(set(warnings)),
        'landing_links': sorted(set(landing_links)),
        "manifest_links": sorted({resolve((e.text or "").strip())
            for e in root.iter() if (e.text or "").strip().split("?", 1)[0].endswith(".upm")}),
    }
    return metadata, sorted(candidates.values(), key=lambda item: (item.version, item.package_model, item.url))


def probe(candidate: Candidate, timeout: float = 20) -> dict:
    result = asdict(candidate)
    try:
        request = urllib.request.Request(candidate.url, method="HEAD", headers={"User-Agent": USER_AGENT})
        with open_advertised(request, advertised_by=candidate.advertised_by, timeout=timeout) as response:
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


class DownloadFailure(RuntimeError):
    """Retain safe request timing after a failed download becomes a receipt."""
    def __init__(self, receipt):
        super().__init__(receipt.get('download_error', 'firmware acquisition failed'))
        self.status_code = receipt.get('download_http_status')
        self.retry_after = receipt.get('download_retry_after')
        self.pause_replay = bool(receipt.get('download_paused'))


def download(candidate: dict, directory: str | Path) -> dict:
    try:
        directory = Path(directory)
        directory.mkdir(parents=True, exist_ok=True)
        name = candidate['filename']
        if not name or Path(name).name != name or name in {'.', '..'}:
            raise ValueError('candidate filename must be a basename')
        filename = Path(name)
        expected = candidate.get('bytes')
        expected_hash = candidate.get('expected_sha256')
        if expected_hash:
            expected_hash = expected_hash.lower()
            if len(expected_hash) != 64 or any(c not in '0123456789abcdef' for c in expected_hash):
                raise ValueError('invalid expected SHA-256')
            cached = directory / (filename.stem + '--' + expected_hash + filename.suffix)
            if cached.is_file() and (expected is None or cached.stat().st_size == expected) and _sha256(cached) == expected_hash:
                return {**candidate, 'bytes': cached.stat().st_size, 'local_file': cached.name,
                        'release_asset': cached.name, 'sha256': expected_hash, 'downloaded': True}
        # Each request has isolated staging, including simultaneous same-name URLs.
        with tempfile.TemporaryDirectory(dir=directory, prefix='capture-') as temporary:
            body = Path(temporary) / 'payload'
            request = urllib.request.Request(candidate['url'], headers={'User-Agent': USER_AGENT})
            if candidate.get('archive_capture') is not None:
                from .archive_replay import open_capture
                incoming = open_capture(candidate['url'], candidate['archive_capture'],
                    allowed=lambda url: advertised_url(url, candidate.get('advertised_by')))
            else:
                incoming = open_advertised(request, advertised_by=candidate.get('advertised_by'), timeout=60)
            with incoming as response, body.open('wb') as output:
                if getattr(response, 'status', 200) != 200:
                    raise ValueError('firmware source did not return a complete HTTP 200 response')
                declared = response.headers.get('Content-Length')
                fetched_from = response.geturl() if hasattr(response, 'geturl') else candidate['url']
                memento_datetime = response.headers.get('Memento-Datetime')
                while block := response.read(1024 * 1024):
                    output.write(block)
            size = body.stat().st_size
            if declared is not None and size != int(declared):
                raise ValueError('firmware capture does not match response Content-Length')
            if size == 0 or expected is not None and size != expected:
                raise ValueError('empty or size-mismatched firmware capture')
            digest = _sha256(body)
            if expected_hash and digest != expected_hash:
                raise ValueError('firmware capture does not match expected SHA-256')
            destination = directory / (filename.stem + '--' + digest + filename.suffix)
            os.replace(body, destination)
            return {**candidate, 'bytes': size, 'local_file': destination.name,
                    'release_asset': destination.name, 'sha256': digest, 'downloaded': True,
                    'fetched_from': fetched_from,
                    **({'memento_datetime': memento_datetime} if memento_datetime else {})}
    except Exception as error:
        from .retry import retry_after_seconds
        delay = retry_after_seconds(error)
        if isinstance(error, urllib.error.HTTPError):
            error.close()
        return {**candidate, 'downloaded': False, 'download_error': str(error),
                'download_http_status': getattr(error, 'code', None),
                **({'download_retry_after': delay} if delay is not None else {}),
                'download_paused': bool(getattr(error, 'pause_replay', False))}


def download_all(candidates: list[dict], directory: str | Path, workers: int = 3) -> list[dict]:
    available = [candidate for candidate in candidates if candidate.get("available")]
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        return list(pool.map(lambda item: download(item, directory), available))


def write_json(path: str | Path, value) -> None:
    atomic_json(path, value)
