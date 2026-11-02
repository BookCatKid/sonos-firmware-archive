#!/usr/bin/env python3
"""Automatically preserve deterministic public Sonos firmware discoveries.

This consumes the JSON produced by the repository monitor. It only archives
artifacts from the allowlisted official Sonos update hosts, re-verifies exact
manifest hashes before upload, verifies Release assets through the existing
upload tooling, and then updates the catalog. Novel evidence still remains in
the monitor issue for human review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sonos_firmware.discovery import USER_AGENT, download_all, write_json  # noqa: E402

ALLOWED_FIRMWARE_HOSTS = {"update.sonos.com", "update-firmware.sonos.com"}

def run(*args: str) -> None:
    subprocess.run(args, cwd=ROOT, check=True)


def capture(*args: str) -> str:
    return subprocess.check_output(args, cwd=ROOT, text=True)


def safe(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._+-]+", "_", value)


def official_firmware_url(url: str) -> bool:
    parsed = urllib.parse.urlparse(url)
    return (
        parsed.scheme == "https"
        and parsed.hostname in ALLOWED_FIRMWARE_HOSTS
        and parsed.path.startswith("/firmware/")
    )


def ensure_release(repository: str, tag: str, title: str, notes: str) -> None:
    found = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repository],
        cwd=ROOT,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if found.returncode == 0:
        return
    run(
        "gh", "release", "create", tag,
        "--repo", repository,
        "--title", title,
        "--notes", notes,
    )


def release_assets(repository: str, tag: str) -> dict[str, dict]:
    output = capture("gh", "release", "view", tag, "--repo", repository, "--json", "assets")
    return {item["name"]: item for item in json.loads(output)["assets"]}


def upload_small_verified(repository: str, tag: str, path: Path, sha256: str) -> None:
    assets = release_assets(repository, tag)
    remote = assets.get(path.name)
    if remote is None:
        run("gh", "release", "upload", tag, str(path), "--repo", repository)
        assets = release_assets(repository, tag)
        remote = assets.get(path.name)
    if remote is None:
        raise RuntimeError(f"release asset missing after upload: {tag}/{path.name}")
    digest = (remote.get("digest") or "").removeprefix("sha256:")
    if not digest:
        hasher = hashlib.sha256()
        request = urllib.request.Request(
            remote["url"], headers={"User-Agent": USER_AGENT}
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            hasher.update(response.read())
        digest = hasher.hexdigest()
    if remote["size"] != path.stat().st_size or digest != sha256:
        raise RuntimeError(f"release asset mismatch: {tag}/{path.name}")


def fetch_verified_manifest(item: dict, destination: Path) -> Path:
    url = item["url"]
    if not official_firmware_url(url):
        raise RuntimeError(f"refusing non-official manifest URL: {url}")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=60) as response:
        body = response.read()
    digest = hashlib.sha256(body).hexdigest()
    if digest != item["sha256"] or len(body) != item["bytes"]:
        raise RuntimeError(f"manifest changed between detection and archival: {url}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(body)
    return destination


def manifest_name(item: dict) -> str:
    metadata = item.get("metadata") or {}
    version = metadata.get("system_version") or metadata.get("default_version") or "unknown"
    url_hash = hashlib.sha256(item["url"].encode()).hexdigest()[:8]
    return f"{safe(version)}--{item['sha256'][:12]}--{url_hash}.upm"


def preserve_manifests(report: dict, repository: str, work: Path, plan_only: bool) -> int:
    catalog = json.loads((ROOT / "data/catalog.json").read_text())
    known = {
        (entry.get("source_url"), entry.get("sha256"))
        for entry in catalog.get("source_manifests", [])
    }
    pending = [
        item for item in report.get("manifests", [])
        if (item.get("url"), item.get("sha256")) not in known
    ]
    if not pending:
        return 0
    if plan_only:
        for item in pending:
            print(f"would archive manifest {item['url']} {item['sha256']}")
        return len(pending)

    checked = report.get("checked") or datetime.now(timezone.utc).isoformat()
    release_tag = f"manifest-snapshots-{checked[:10]}"
    ensure_release(
        repository,
        release_tag,
        f"Sonos manifest snapshots {checked[:10]}",
        "Exact signed update manifests captured from public official Sonos update URLs by the deterministic monitor.",
    )
    archived = 0
    for item in pending:
        name = manifest_name(item)
        path = fetch_verified_manifest(item, ROOT / "data/manifests" / name)
        upload_small_verified(repository, release_tag, path, item["sha256"])
        run(
            sys.executable, str(ROOT / "scripts/import_manifest.py"), str(path),
            "--source-url", item["url"],
            "--provenance", "official-live-automated-monitor",
            "--release-tag", release_tag,
        )
        archived += 1
        print(f"archived manifest: {name}", flush=True)
    return archived


def candidate_key(item: dict) -> tuple:
    return (item.get("version"), item.get("package_model"), item.get("filename"))


def collect_candidates(monitor: dict, directory: dict | None) -> list[dict]:
    merged: dict[tuple, dict] = {}
    sources = [
        monitor.get("available_uncatalogued_candidates", []),
        monitor.get("known_missing_now_available", []),
    ]
    if directory:
        sources.append(directory.get("uncatalogued_available", []))
    for rows in sources:
        for item in rows:
            if not item.get("available"):
                continue
            if not official_firmware_url(item.get("url", "")):
                print(f"skipping non-official candidate: {item.get('url')}", file=sys.stderr)
                continue
            key = candidate_key(item)
            prior = merged.get(key)
            if prior is None or item.get("url") < prior.get("url", ""):
                merged[key] = item
    return sorted(
        merged.values(),
        key=lambda item: (item["version"], item["package_model"], item["filename"]),
    )


def preserve_candidates(
    candidates: list[dict],
    repository: str,
    work: Path,
    stamp: str,
    plan_only: bool,
) -> int:
    if plan_only:
        for item in candidates:
            print(f"would archive firmware {item['version']} {item['filename']}")
        return len(candidates)

    total = 0
    versions = sorted({item["version"] for item in candidates})
    for version in versions:
        rows = [item for item in candidates if item["version"] == version]
        directory = work / safe(version)
        results = download_all(rows, directory, workers=3)
        failed = [item for item in results if not item.get("downloaded")]
        if failed:
            raise RuntimeError(
                f"failed to download {failed[0]['url']}: {failed[0].get('download_error')}"
            )

        stem = f"auto-{safe(version)}-{stamp}"
        discovery_path = ROOT / "data/discovery" / f"{stem}.json"
        receipt_path = ROOT / "data/discovery" / f"{stem}-receipt.json"
        write_json(
            discovery_path,
            {
                "schema_version": 1,
                "checked": datetime.now(timezone.utc).isoformat(),
                "method": "automated preservation of live official monitor candidates",
                "system_version": version,
                "candidates": rows,
            },
        )
        write_json(
            receipt_path,
            {
                "schema_version": 1,
                "source": str(discovery_path.relative_to(ROOT)),
                "artifacts": results,
            },
        )

        tag = f"firmware-{version}"
        ensure_release(
            repository,
            tag,
            f"Sonos firmware {version}",
            "Official Sonos-hosted software preserved byte-for-byte. Metadata, hashes, and source URLs are recorded in data/catalog.json and data/discovery/.",
        )
        run(
            sys.executable, str(ROOT / "scripts/upload_firmware_release.py"),
            "--tag", tag,
            "--receipt", str(receipt_path),
            "--directory", str(directory),
            "--repo", repository,
        )
        run(
            sys.executable, str(ROOT / "scripts/import_discovery.py"),
            str(discovery_path), str(receipt_path), str(directory),
        )
        total += len(results)
        print(f"archived firmware {version}: {len(results)} artifact(s)", flush=True)

    if total:
        run(sys.executable, str(ROOT / "scripts/refresh_key_recovery_ledger.py"))
    return total


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--monitor-report", type=Path, required=True)
    parser.add_argument("--directory-report", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--plan-only", action="store_true")
    args = parser.parse_args()

    monitor = json.loads(args.monitor_report.read_text())
    directory = (
        json.loads(args.directory_report.read_text())
        if args.directory_report and args.directory_report.exists()
        else None
    )
    args.work_dir.mkdir(parents=True, exist_ok=True)
    stamp_source = monitor.get("checked") or datetime.now(timezone.utc).isoformat()
    stamp = re.sub(r"[^0-9]", "", stamp_source)[:14] + "Z"

    summary = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "manifest_archived": 0,
        "firmware_artifacts_archived": 0,
        "plan_only": args.plan_only,
    }
    try:
        summary["manifest_archived"] = preserve_manifests(
            monitor, args.repo, args.work_dir, args.plan_only
        )
        candidates = collect_candidates(monitor, directory)
        summary["firmware_artifacts_archived"] = preserve_candidates(
            candidates, args.repo, args.work_dir, stamp, args.plan_only
        )
        summary["status"] = "ok"
    except Exception as error:
        summary["status"] = "error"
        summary["error"] = str(error)
        write_json(args.output, summary)
        raise

    write_json(args.output, summary)
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
