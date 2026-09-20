#!/usr/bin/env python3
"""Stream discovered official Sonos APKs into hash-verified GitHub Releases."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import tempfile
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASES = {
    "s2": ("apps-fireos-s2", "Sonos S2/modern official Fire OS APK archive"),
    "s1": ("apps-fireos-s1", "Sonos S1 official Fire OS APK archive"),
    "legacy": ("apps-fireos-legacy", "Sonos legacy official Android APK archive"),
}


class InvalidApkError(RuntimeError):
    pass


def save(path: Path, records: list[dict], gaps: list[dict], discovery: Path) -> None:
    document = {
        "schema_version": 1, "updated": datetime.now(timezone.utc).isoformat(),
        "discovery": str(discovery.relative_to(ROOT)), "artifact_total": len(records),
        "gap_total": len(gaps), "gaps": sorted(gaps, key=lambda x: x["url"]),
        "artifacts": sorted(records, key=lambda x: (x["family"], x["version"], x["sha256"])),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, indent=2) + "\n")
    os.replace(temporary, path)


def ensure_release(repository: str, tag: str, title: str) -> None:
    if subprocess.run(["gh", "release", "view", tag, "--repo", repository],
                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
        subprocess.run([
            "gh", "release", "create", tag, "--repo", repository, "--title", title,
            "--notes", "Official Sonos-hosted APKs preserved byte-for-byte. The repository's "
            "data/apps/mobile-archive.json records source URLs, Wayback provenance, hashes, "
            "sizes, and verified GitHub assets.",
        ], check=True)


def assets(repository: str, tag: str) -> dict:
    output = subprocess.check_output([
        "gh", "release", "view", tag, "--repo", repository, "--json", "assets",
    ], text=True)
    return {x["name"]: x for x in json.loads(output)["assets"]}


def fetch(candidate: dict, destination: Path) -> tuple[int, str, str]:
    urls = [candidate["url"]]
    timestamp = candidate.get("wayback_timestamp")
    if timestamp:
        urls.append(f"https://web.archive.org/web/{timestamp}id_/{candidate['url']}")
    error = None
    for url in urls:
        digest, size = hashlib.sha256(), 0
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "sonos-firmware-archive/1"})
            with urllib.request.urlopen(request, timeout=180) as source, destination.open("wb") as output:
                while chunk := source.read(1024 * 1024):
                    output.write(chunk); digest.update(chunk); size += len(chunk)
            if not zipfile.is_zipfile(destination):
                destination.unlink(missing_ok=True)
                error = InvalidApkError(f"downloaded body is not a valid ZIP/APK: {url}")
                continue
            return size, digest.hexdigest(), url
        except Exception as caught:
            error = caught; destination.unlink(missing_ok=True)
    if isinstance(error, InvalidApkError):
        raise error
    raise RuntimeError(f"failed to fetch {candidate['url']}: {error}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--discovery", type=Path, default=ROOT / "data/apps/mobile-discovery.json")
    parser.add_argument("--receipt", type=Path, default=ROOT / "data/apps/mobile-archive.json")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()
    discovery = json.loads(args.discovery.read_text())
    existing = json.loads(args.receipt.read_text()) if args.receipt.exists() else {}
    records = existing.get("artifacts", [])
    gaps = existing.get("gaps", [])
    urls_done = {u for x in records for u in x.get("source_urls", [])}
    urls_done.update(x["url"] for x in gaps)
    digests = {(x["family"], x["sha256"]): x for x in records}
    ready, count = set(), 0
    candidates = sorted(discovery["candidates"], key=lambda x: (x["family"], x["version"], x["url"]))
    with tempfile.TemporaryDirectory(prefix="sonos-apk-archive-") as temporary:
        root = Path(temporary)
        for candidate in candidates:
            if candidate["url"] in urls_done:
                continue
            if args.limit is not None and count >= args.limit:
                break
            local = root / candidate["url"].rsplit("/", 1)[-1]
            try:
                size, digest, fetched_from = fetch(candidate, local)
            except InvalidApkError as error:
                gaps.append({
                    "url": candidate["url"], "family": candidate["family"],
                    "version": candidate["version"], "reason": str(error),
                    "sources": candidate.get("sources", []),
                    "wayback_timestamp": candidate.get("wayback_timestamp"),
                })
                urls_done.add(candidate["url"])
                save(args.receipt, records, gaps, args.discovery)
                print(f"known gap: {candidate['url']} ({error})", flush=True)
                continue
            key = (candidate["family"], digest)
            if key in digests:
                record = digests[key]; record["source_urls"].append(candidate["url"])
                record["sources"] = sorted(set(record["sources"] + candidate.get("sources", [])))
                local.unlink(missing_ok=True)
                urls_done.add(candidate["url"]); save(args.receipt, records, gaps, args.discovery)
                continue
            tag, title = RELEASES[candidate["family"]]
            if tag not in ready:
                ensure_release(args.repo, tag, title); ready.add(tag)
            original = candidate["url"].rsplit("/", 1)[-1]
            stem, suffix = os.path.splitext(original)
            name = f"{re.sub(r'[^A-Za-z0-9._-]+', '_', stem)}--{digest[:12]}{suffix.lower()}"
            upload = local.with_name(name); local.rename(upload)
            current = assets(args.repo, tag)
            if name not in current:
                subprocess.run(["gh", "release", "upload", tag, str(upload), "--repo", args.repo], check=True)
                current = assets(args.repo, tag)
            remote = current[name]
            remote_digest = (remote.get("digest") or "").removeprefix("sha256:")
            if remote["size"] != size or remote_digest != digest:
                raise RuntimeError(f"GitHub asset mismatch: {tag}/{name}")
            upload.unlink(missing_ok=True)
            record = {
                "family": candidate["family"], "platform": candidate["platform"],
                "version": candidate["version"], "original_filename": original,
                "bytes": size, "sha256": digest, "source_urls": [candidate["url"]],
                "fetched_from": fetched_from, "sources": candidate.get("sources", []),
                "wayback_timestamp": candidate.get("wayback_timestamp"),
                "wayback_digest": candidate.get("wayback_digest"),
                "release_tag": tag, "release_asset": name, "release_url": remote["url"],
            }
            records.append(record); digests[key] = record; urls_done.add(candidate["url"])
            count += 1; save(args.receipt, records, gaps, args.discovery)
            print(f"archived {count}: {tag}/{name} ({size} bytes)", flush=True)
    save(args.receipt, records, gaps, args.discovery)
    print(f"verified artifacts={len(records)} gaps={len(gaps)} new={count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
