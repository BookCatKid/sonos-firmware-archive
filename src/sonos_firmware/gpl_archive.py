"""Direct-Sonos GPL/LGPL source discovery, verification and preservation."""

from __future__ import annotations

import hashlib
import html.parser
import json
import os
import re
import shutil
import tarfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

BASE = "https://www.sonos.com/documents/gpl/"
VERSION_RE = re.compile(r"^[1-9][0-9]{0,2}\.[0-9]{1,2}$")
VERSION_IN_URL = re.compile(r"(?:https?://(?:www\.)?sonos\.com)?/documents/gpl/([1-9][0-9]{0,2}\.[0-9]{1,2})/", re.I)
USER_AGENT = "Mozilla/5.0 (compatible; Sonos-GPL-Preservation/1.0)"
MIN_DELAY = 2.0
MAX_INDEX_BYTES = 512 * 1024
DEFAULT_FREE_RESERVE = 512 * 1024 * 1024
UPSTREAM_HOSTS = {"ftp.gnu.org", "www.uclibc.org"}


def timestamp() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def valid_version(version: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise ValueError(f"invalid GPL version: {version!r}")
    return version


def version_key(version: str) -> tuple[int, int]:
    return tuple(map(int, version.split(".")))


def index_url(version: str) -> str:
    return f"{BASE}{valid_version(version)}/gpl.html"


def source_url(url: str) -> str:
    """Normalize the *path segment*, not the query: '+' must become '%2B'."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in {"www.sonos.com", "sonos.com"}:
        raise ValueError(f"not a HTTPS Sonos source URL: {url}")
    path = parts.path.split("/")
    if len(path) != 5 or path[:3] != ["", "documents", "gpl"]:
        raise ValueError(f"unexpected GPL path: {url}")
    version = valid_version(path[3])
    filename = urllib.parse.unquote(path[4])
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename or "\x00" in filename:
        raise ValueError(f"unsafe source filename: {url}")
    if parts.query or parts.fragment:
        raise ValueError(f"unexpected query or fragment: {url}")
    return BASE + version + "/" + urllib.parse.quote(filename, safe="-._~")


class LinkParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self.current: str | None = None
        self.text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "a":
            self.current = dict(attrs).get("href")
            self.text = []

    def handle_data(self, data: str) -> None:
        if self.current is not None:
            self.text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a" and self.current:
            self.links.append((self.current, "".join(self.text).strip()))
            self.current = None


def upstream_url(url: str) -> str:
    """Only allow the official upstream source hosts present in Sonos indexes."""
    parts = urllib.parse.urlsplit(url)
    if parts.scheme != "https" or parts.hostname not in UPSTREAM_HOSTS or parts.query or parts.fragment:
        raise ValueError(f"untrusted upstream source URL: {url}")
    filename = urllib.parse.unquote(parts.path.rsplit("/", 1)[-1])
    if not filename or filename in {".", ".."} or "/" in filename or "\\" in filename:
        raise ValueError(f"unsafe upstream filename: {url}")
    return url


def parse_index(version: str, body: bytes) -> list[dict]:
    valid_version(version)
    text = body.decode("utf-8", errors="replace")
    if "Sonos GPL/LGPL Downloads" not in text or "<a " not in text.lower():
        raise ValueError("not a Sonos GPL/LGPL index")
    parser = LinkParser()
    parser.feed(text)
    records = []
    seen = set()
    for raw, label in parser.links:
        url = urllib.parse.urljoin(index_url(version), raw)
        parts = urllib.parse.urlsplit(url)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            continue
        sonos = parts.hostname in {"sonos.com", "www.sonos.com"}
        if sonos:
            if parts.scheme == "http":
                url = urllib.parse.urlunsplit(("https", parts.netloc, parts.path, parts.query, ""))
            try:
                normalized = source_url(url)
            except ValueError:
                continue
            if f"/gpl/{version}/" not in normalized:
                continue
            fetch_url = normalized
        else:
            fetch_url = None
        if url in seen:
            continue
        seen.add(url)
        filename = urllib.parse.unquote(parts.path.rsplit("/", 1)[-1])
        lower = filename.lower()
        kind = "attribution" if lower.endswith(".pdf") else "source" if lower.endswith((".txz", ".tgz", ".tar.gz", ".tar.bz2", ".tar.xz")) else "other"
        records.append({"filename": filename, "kind": kind, "url": url, "fetch_url": fetch_url, "origin": "sonos" if sonos else "upstream", "label": label})
    if not records:
        raise ValueError("index contains no downloadable links")
    return records


@dataclass
class HttpResult:
    status: int
    data: bytes
    content_type: str
    final_url: str


def get_page(url: str, *, timeout: int = 25, max_bytes: int = MAX_INDEX_BYTES) -> HttpResult:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT,
                                               "Accept": "text/html,application/xhtml+xml", "Connection": "close"})
    for attempt in range(3):
        try:
            response = urllib.request.urlopen(req, timeout=timeout)
        except urllib.error.HTTPError as error:
            with error:
                response_info = HttpResult(error.code, error.read(4096), error.headers.get("Content-Type", ""), error.url)
            if error.code not in {500, 502, 503, 504} or attempt == 2:
                return response_info
        except (urllib.error.URLError, OSError, TimeoutError):
            if attempt == 2:
                raise
        else:
            with response:
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"GPL index exceeds {max_bytes} bytes: {url}")
                return HttpResult(response.status, data, response.headers.get("Content-Type", ""), response.url)
        time.sleep(2 ** (attempt + 1))
    raise AssertionError("unreachable retry exhaustion")


def classify_index(version: str, response: HttpResult) -> dict:
    result = {"version": version, "url": index_url(version), "http_status": response.status,
              "checked_at": timestamp(), "state": "inconclusive"}
    if response.status == 200:
        try:
            links = parse_index(version, response.data)
        except ValueError as error:
            result["reason"] = str(error)
        else:
            result.update(state="verified", bytes=len(response.data), sha256=sha256_bytes(response.data),
                          links=links, source="direct-sonos")
    elif response.status == 404:
        result["state"] = "not-served"
    elif response.status == 403:
        result["state"] = "access-denied"
        result["reason"] = "AccessDenied XML" if b"AccessDenied" in response.data else "possible access challenge"
    elif response.status == 429:
        result["state"] = "rate-limited"
    else:
        result["reason"] = f"unexpected HTTP {response.status}"
    return result


def offline_candidates(root: Path) -> dict[str, set[str]]:
    """Find versions actually mentioned in archived metadata or documentation."""
    found: dict[str, set[str]] = {}
    for path in sorted((*root.glob("docs/**/*.md"), *root.glob("data/**/*.json"), *root.glob("data/**/*.html"))):
        if "data/gpl/live/" in path.relative_to(root).as_posix() or path.stat().st_size > 20_000_000:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for match in VERSION_IN_URL.finditer(text):
            version = match.group(1)
            if VERSION_RE.fullmatch(version):
                found.setdefault(version, set()).add(str(path.relative_to(root)))
    return found


def candidates(known: set[str], *, major_max: int = 120, minor_max: int = 99, evidence: dict[str, set[str]] | None = None) -> list[dict]:
    """Enumerate entire version grammar, prioritizing observed and adjacent versions."""
    if not (1 <= major_max <= 999 and 0 <= minor_max <= 99):
        raise ValueError("invalid sweep bounds")
    evidence = evidence or {}
    adjacent = set()
    for version in known:
        major, minor = version_key(valid_version(version))
        for n in (minor - 1, minor + 1):
            if 0 <= n <= minor_max:
                adjacent.add(f"{major}.{n}")
    records = []
    for major in range(1, major_max + 1):
        for minor in range(minor_max + 1):
            version = f"{major}.{minor}"
            if version in known:
                continue
            priority = 0 if version in evidence else 1 if version in adjacent else 2
            records.append({"version": version, "priority": priority, "evidence": sorted(evidence.get(version, ()))})
    return sorted(records, key=lambda item: (item["priority"], version_key(item["version"])))


def capture_response(version: str, response: HttpResult, output: Path) -> dict:
    record = classify_index(version, response)
    if record["state"] == "verified":
        path = output / "indexes" / f"{version}.html"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(response.data)
    return record


def verify_artifact(path: Path, kind: str) -> tuple[bool, str, int]:
    if kind == "attribution":
        with path.open("rb") as file:
            header = file.read(5)
            file.seek(max(0, path.stat().st_size - 2048))
            trailer = file.read()
        valid = header == b"%PDF-" and b"%%EOF" in trailer and b"startxref" in trailer
        return valid, "pdf-structure" if valid else "invalid-pdf", 0
    if kind != "source":
        return False, "unknown-extension", 0
    try:
        count = 0
        with tarfile.open(path, mode="r:*") as tar:
            for member in tar:
                if member.name.startswith("/") or ".." in Path(member.name).parts:
                    return False, "unsafe-member-path", count
                count += 1
        return count > 0, "tar-integrity" if count else "empty-tar", count
    except (tarfile.TarError, OSError, EOFError) as error:
        return False, f"invalid-archive:{type(error).__name__}", 0


def download_artifact(
    item: dict, destination_root: Path, *, min_free_bytes: int = DEFAULT_FREE_RESERVE,
    request: Callable[[urllib.request.Request], object] | None = None,
    chunk_size: int = 1024 * 1024,
) -> dict:
    """Download to a resumable partial, validate, then content-address the result."""
    if item.get("origin") == "sonos" and item.get("fetch_url"):
        fetch_url = source_url(item["fetch_url"])
    elif item.get("origin") == "upstream":
        fetch_url = upstream_url(item["url"])
    else:
        return {"state": "external-or-unsupported", "filename": item["filename"]}
    destination_root.mkdir(parents=True, exist_ok=True)
    filename = item["filename"]
    scratch = destination_root / "incomplete" / (sha256_bytes(fetch_url.encode()) + ".part")
    scratch.parent.mkdir(parents=True, exist_ok=True)
    current = scratch.stat().st_size if scratch.exists() else 0
    headers = {"User-Agent": USER_AGENT, "Accept": "*/*", "Connection": "close"}
    if current:
        headers["Range"] = f"bytes={current}-"
    opener = request or (lambda req: urllib.request.urlopen(req, timeout=120))
    req = urllib.request.Request(fetch_url, headers=headers)
    try:
        response = opener(req)
    except urllib.error.HTTPError as error:
        return {"state": "http-error", "http_status": error.code, "filename": filename, "fetch_url": fetch_url}
    with response:
        status = getattr(response, "status", 200)
        content_type = response.headers.get("Content-Type", "")
        if status not in (200, 206):
            return {"state": "unexpected-http", "http_status": status, "filename": filename}
        if "text/html" in content_type or "application/xml" in content_type:
            return {"state": "unexpected-content-type", "http_status": status, "filename": filename, "content_type": content_type}
        if current and status == 206:
            content_range = response.headers.get("Content-Range", "")
            if not content_range.startswith(f"bytes {current}-"):
                return {"state": "invalid-resume-range", "filename": filename, "content_range": content_range}
        if current and status == 200:
            current = 0
        length = response.headers.get("Content-Length")
        required = int(length) if length and length.isdecimal() else 0
        if shutil.disk_usage(destination_root).free - min_free_bytes < required:
            return {"state": "insufficient-space", "filename": filename, "need_bytes": required,
                    "free_bytes": shutil.disk_usage(destination_root).free}
        written = current
        received = 0
        with scratch.open("ab" if current else "wb") as stream:
            while chunk := response.read(chunk_size):
                if shutil.disk_usage(destination_root).free <= min_free_bytes:
                    return {"state": "insufficient-space", "filename": filename, "partial_bytes": written}
                stream.write(chunk)
                written += len(chunk)
                received += len(chunk)
    if required and received != required:
        return {"state": "incomplete-transfer", "filename": filename, "partial_bytes": written,
                "expected_response_bytes": required, "received_response_bytes": received}
    ok, verification, members = verify_artifact(scratch, item["kind"])
    if not ok:
        return {"state": "invalid-artifact", "filename": filename, "verification": verification, "downloaded_bytes": written}
    digest = file_sha256(scratch)
    final = destination_root / "sha256" / digest[:2] / digest
    final.parent.mkdir(parents=True, exist_ok=True)
    if final.exists():
        if file_sha256(final) != digest:
            raise RuntimeError("content-addressed destination is corrupt")
        scratch.unlink()
    else:
        os.replace(scratch, final)
    return {"state": "verified", "filename": filename, "kind": item["kind"],
            "source_url": item["url"], "fetch_url": fetch_url, "bytes": final.stat().st_size,
            "sha256": digest, "verification": verification, "members": members,
            "release_asset": filename}


def load_json(path: Path, default: object) -> object:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def pause(delay: float) -> None:
    if delay < MIN_DELAY:
        raise ValueError(f"Sonos request delay must be at least {MIN_DELAY} seconds")
    time.sleep(delay)
