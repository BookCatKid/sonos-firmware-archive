#!/usr/bin/env python3
"""Archive Sonos-hosted GPL index and artifact captures from Wayback."""

from __future__ import annotations

import argparse
import base64
import hashlib
import html.parser
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_RELEASES = (
    "7.2", "7.3", "9.2", "10.2", "10.6", "12.0", "13.2", "14.4", "14.18",
)
USER_AGENT = "sonos-firmware-archive/0.1 (+GPL preservation research)"


class AnchorParser(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.in_anchor = False
        self.href: str | None = None
        self.items: list[tuple[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        self.in_anchor = True
        self.href = dict(attrs).get("href")

    def handle_data(self, data: str) -> None:
        if not self.in_anchor:
            return
        text = data.strip()
        if self.href and text:
            self.items.append((self.href, text))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "a":
            self.in_anchor = False


def request(url: str, timeout: float = 60) -> bytes:
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            if attempt < 2:
                time.sleep(1 + attempt)
    raise RuntimeError(f"request failed: {url}: {last_error}") from last_error


def cdx_rows(url: str, collapse: str) -> list[list[str]]:
    query = urllib.parse.urlencode({
        "url": url,
        "matchType": "prefix",
        "output": "json",
        "filter": "statuscode:200",
        "collapse": collapse,
        "fl": "timestamp,original,statuscode,mimetype,digest,length",
        "limit": 10000,
    })
    rows = json.loads(request(f"https://web.archive.org/cdx/search/cdx/?{query}"))
    return rows[1:]


def download_replay(timestamp: str, url: str, destination: Path) -> bytes:
    destination.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(4):
        try:
            data = request(f"https://web.archive.org/web/{timestamp}id_/{url}")
            break
        except Exception as error:
            if attempt == 3:
                raise
            time.sleep(1 + attempt)
    temporary = destination.with_suffix(destination.suffix + ".part")
    temporary.write_bytes(data)
    temporary.replace(destination)
    return data


def cdx_digest_sha1(digest: str) -> str:
    padded = digest + "=" * ((8 - len(digest) % 8) % 8)
    return base64.b32decode(padded).hex().upper()


def classify(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix == ".html":
        return "index"
    if suffix == ".pdf":
        return "attribution"
    if suffix in {".txz", ".tgz", ".tar.gz", ".tar.bz2"}:
        return "source"
    return "other"


def run(release: str, output_root: Path, delay: float) -> dict:
    prefix = f"https://www.sonos.com/documents/gpl/{release}/"
    index_rows = cdx_rows(prefix + "gpl.html", "timestamp:6")
    if not index_rows:
        return {"release": release, "status": "missing-index"}
    timestamp, original = index_rows[-1][:2]

    index_destination = output_root / "indexes" / f"{release}.html"
    index_data = download_replay(timestamp, original, index_destination)
    parser = AnchorParser()
    parser.feed(index_data.decode("utf-8", errors="replace"))

    artifact_rows = []
    seen: set[str] = set()
    for row in cdx_rows(prefix, "urlkey"):
        row_timestamp, row_original = row[:2]
        if not row_original:
            continue
        host = urllib.parse.urlparse(row_original.replace(":80", "")).hostname or ""
        if "sonos.com" not in host:
            continue
        normalized = row_original.replace(":80", "")
        if normalized in seen:
            continue
        seen.add(normalized)
        artifact_rows.append((row_timestamp, normalized, row[3], row[4], row[5]))

    artifacts = []
    for row_timestamp, row_original, mime, cdx_digest, cdx_length in artifact_rows:
        filename = Path(urllib.parse.urlparse(row_original).path).name
        destination = output_root / "artifacts" / release / filename
        downloaded = True
        error = None
        try:
            data = download_replay(row_timestamp, row_original, destination)
        except Exception as exc:
            downloaded = False
            error = str(exc)
            data = b""
        if delay:
            time.sleep(delay)
        actual_sha1 = hashlib.sha1(data).hexdigest().upper()
        expected_sha1 = cdx_digest_sha1(cdx_digest)
        artifacts.append({
            "filename": filename,
            "kind": classify(filename),
            "original_url": row_original,
            "wayback_timestamp": row_timestamp,
            "wayback_url": f"https://web.archive.org/web/{row_timestamp}id_/{row_original}",
            "cdx_mimetype": mime,
            "cdx_length": int(cdx_length),
            "cdx_sha1": expected_sha1,
            "local_bytes": len(data),
            "local_sha256": hashlib.sha256(data).hexdigest() if data else None,
            "local_sha1": actual_sha1,
            "cdx_digest_match": downloaded and actual_sha1 == expected_sha1,
            "downloaded": downloaded,
            "error": error,
        })

    return {
        "release": release,
        "index": {
            "original_url": original,
            "wayback_timestamp": timestamp,
            "wayback_url": f"https://web.archive.org/web/{timestamp}id_/{original}",
            "bytes": len(index_data),
            "sha256": hashlib.sha256(index_data).hexdigest(),
            "links": [{"url": url, "text": text} for url, text in parser.items],
        },
        "artifacts": sorted(artifacts, key=lambda item: item["filename"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--release", action="append", choices=DEFAULT_RELEASES)
    parser.add_argument("--delay", type=float, default=0.2)
    parser.add_argument("--output-root", type=Path, default=ROOT / "data/gpl")
    args = parser.parse_args()
    releases = args.release or DEFAULT_RELEASES
    catalog = {"schema_version": 1, "releases": []}
    for release in releases:
        print(f"processing {release}", flush=True)
        catalog["releases"].append(run(release, args.output_root, args.delay))
        write_path = args.output_root / "wayback-catalog.json"
        write_path.parent.mkdir(parents=True, exist_ok=True)
        write_path.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except RuntimeError as error:
        print(error, file=sys.stderr)
        raise SystemExit(1)
