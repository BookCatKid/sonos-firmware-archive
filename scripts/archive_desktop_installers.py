#!/usr/bin/env python3
"""Stream discovered Sonos desktop installers into verified GitHub Releases.

Only one installer is retained locally at a time. The receipt is rewritten
after every verified upload so interrupted runs are safely resumable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES = {
    ("s2", "windows"): ("apps-s2-windows", "Sonos S2/modern Windows app archive"),
    ("s2", "macos"): ("apps-s2-macos", "Sonos S2/modern macOS app archive"),
    ("s1", "windows"): ("apps-s1-windows", "Sonos S1 Windows app archive"),
    ("s1", "macos"): ("apps-s1-macos", "Sonos S1 macOS app archive"),
    ("legacy", "windows"): ("apps-legacy-windows", "Sonos pre-S1/S2 Windows app archive"),
    ("legacy", "macos"): ("apps-legacy-macos", "Sonos pre-S1/S2 macOS app archive"),
}


def write_receipt(path: Path, records: list[dict], discovery: Path) -> None:
    document = {
        "schema_version": 1,
        "updated": datetime.now(timezone.utc).isoformat(),
        "discovery": str(discovery.relative_to(ROOT)),
        "artifact_total": len(records),
        "artifacts": sorted(records, key=lambda x: (x["family"], x["platform"], x["version"], x["sha256"])),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    os.replace(temporary, path)


def ensure_release(repository: str, tag: str, title: str) -> None:
    exists = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repository],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if not exists:
        subprocess.run([
            "gh", "release", "create", tag, "--repo", repository,
            "--title", title, "--notes",
            "Historical installers preserved byte-for-byte from public Sonos CDN URLs. "
            "See data/apps/desktop-archive.json in the repository for original URLs, "
            "SHA-256 hashes, sizes, aliases, and discovery provenance.",
        ], check=True)


def release_assets(repository: str, tag: str) -> dict:
    output = subprocess.check_output([
        "gh", "release", "view", tag, "--repo", repository, "--json", "assets",
    ], text=True)
    return {item["name"]: item for item in json.loads(output)["assets"]}


def wayback_url(candidate: dict) -> str | None:
    for source in candidate.get("sources", []):
        if source.startswith("wayback-cdx:"):
            timestamp = source.split(":", 2)[1]
            return f"https://web.archive.org/web/{timestamp}id_/{candidate['url']}"
    return None


def download(candidate: dict, destination: Path) -> tuple[int, str, str]:
    urls = [candidate["url"]]
    archived = wayback_url(candidate)
    if archived:
        urls.append(archived)
    last_error = None
    for url in urls:
        digest = hashlib.sha256()
        size = 0
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "sonos-firmware-archive/1"})
            with urllib.request.urlopen(request, timeout=120) as response, destination.open("wb") as output:
                while chunk := response.read(1024 * 1024):
                    output.write(chunk)
                    digest.update(chunk)
                    size += len(chunk)
            return size, digest.hexdigest(), url
        except Exception as error:
            last_error = error
            destination.unlink(missing_ok=True)
    raise RuntimeError(f"download failed for {candidate['url']}: {last_error}")


def asset_name(candidate: dict, digest: str) -> str:
    original = candidate["url"].rsplit("/", 1)[-1]
    stem, suffix = os.path.splitext(original)
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)
    return f"{safe}--{digest[:12]}{suffix.lower()}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, default=ROOT / "data/apps/desktop-discovery.json")
    parser.add_argument("--receipt", type=Path, default=ROOT / "data/apps/desktop-archive.json")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--limit", type=int, help="process at most this many new binaries")
    args = parser.parse_args()
    discovery = json.loads(args.discovery.read_text())
    records = []
    if args.receipt.exists():
        records = json.loads(args.receipt.read_text()).get("artifacts", [])
    by_digest = {(x["family"], x["platform"], x["sha256"]): x for x in records}
    urls_done = {url for x in records for url in x.get("source_urls", [])}
    releases_ready: set[str] = set()
    processed = 0
    candidates = [x for x in discovery["candidates"] if x.get("http_status") == 200]
    candidates.sort(key=lambda x: (x.get("expected_sha256") is None, x["family"], x["platform"], x["version"], x["url"]))
    with tempfile.TemporaryDirectory(prefix="sonos-app-archive-") as temporary:
        temporary_path = Path(temporary)
        for candidate in candidates:
            if candidate["url"] in urls_done:
                continue
            expected = candidate.get("expected_sha256")
            if expected and (candidate["family"], candidate["platform"], expected) in by_digest:
                record = by_digest[(candidate["family"], candidate["platform"], expected)]
                if candidate["url"] not in record["source_urls"]:
                    record["source_urls"].append(candidate["url"])
                    urls_done.add(candidate["url"])
                record["sources"] = sorted(set(record["sources"] + candidate.get("sources", [])))
                continue
            if args.limit is not None and processed >= args.limit:
                break
            local = temporary_path / candidate["url"].rsplit("/", 1)[-1]
            size, digest, fetched_from = download(candidate, local)
            if expected and digest != expected:
                raise RuntimeError(f"SHA-256 mismatch for {candidate['url']}: {digest} != {expected}")
            key = (candidate["family"], candidate["platform"], digest)
            if key in by_digest:
                record = by_digest[key]
                if candidate["url"] not in record["source_urls"]:
                    record["source_urls"].append(candidate["url"])
                    urls_done.add(candidate["url"])
                record["sources"] = sorted(set(record["sources"] + candidate.get("sources", [])))
                local.unlink(missing_ok=True)
                write_receipt(args.receipt, records, args.discovery)
                continue
            tag, title = RELEASES[(candidate["family"], candidate["platform"])]
            if tag not in releases_ready:
                ensure_release(args.repo, tag, title)
                releases_ready.add(tag)
            name = asset_name(candidate, digest)
            upload_path = local.with_name(name)
            local.rename(upload_path)
            assets = release_assets(args.repo, tag)
            existing = assets.get(name)
            if existing is None:
                subprocess.run(["gh", "release", "upload", tag, str(upload_path),
                                "--repo", args.repo], check=True)
                assets = release_assets(args.repo, tag)
                existing = assets.get(name)
            observed_digest = (existing.get("digest") or "").removeprefix("sha256:")
            if existing["size"] != size or observed_digest != digest:
                raise RuntimeError(f"GitHub asset mismatch: {tag}/{name}")
            upload_path.unlink(missing_ok=True)
            record = {
                "family": candidate["family"], "platform": candidate["platform"],
                "version": candidate["version"], "original_filename": candidate["url"].rsplit("/", 1)[-1],
                "bytes": size, "sha256": digest, "source_urls": [candidate["url"]],
                "fetched_from": fetched_from, "sources": candidate.get("sources", []),
                "release_tag": tag, "release_asset": name,
                "release_url": existing["url"],
            }
            records.append(record)
            by_digest[key] = record
            urls_done.add(candidate["url"])
            processed += 1
            write_receipt(args.receipt, records, args.discovery)
            print(f"archived {processed}: {tag}/{name} ({size} bytes)", flush=True)
    write_receipt(args.receipt, records, args.discovery)
    print(f"verified artifacts={len(records)} new={processed}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
