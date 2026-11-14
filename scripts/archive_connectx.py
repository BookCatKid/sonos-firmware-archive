#!/usr/bin/env python3
"""Discover, snapshot, decrypt and inventory every evidenced ConnectX update."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import urllib.error
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sonos_firmware.connectx import RECIPIENT, STATIC_URL, extract_package, package_version, sha256
from sonos_firmware.discovery import _OPENER, USER_AGENT, official_firmware_url, write_json
from sonos_firmware.extract import key_recipient_id, load_private_key
from archive_monitor_firmware import ensure_release, upload_small_verified


def acquire(url: str, destination: Path) -> bytes:
    if not official_firmware_url(url):
        raise ValueError(f"not an official firmware URL: {url}")
    import urllib.request
    with _OPENER.open(urllib.request.Request(url, headers={"User-Agent": USER_AGENT}), timeout=60) as response:
        data = response.read(128 * 1024 * 1024 + 1)
    if len(data) > 128 * 1024 * 1024:
        raise ValueError("ConnectX package exceeds 128 MiB limit")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(data)
    return data


def download_release(package, destination, repository):
    subprocess.run(["gh", "release", "download", package["release_tag"], "--repo", repository,
                    "--pattern", package["release_asset"], "--output", str(destination), "--clobber"], check=True)
    data = destination.read_bytes()
    if len(data) != package["bytes"] or sha256(data) != package["sha256"]:
        raise ValueError("release source hash mismatch")
    return data


def recover_key(catalog, directory, repository):
    """Use the shared recipient-verified recovery over cataloged plaintext sources."""
    from sonos_firmware.key_recovery import recover_catalog_key
    output = directory / "model17.pem"
    recover_catalog_key(ROOT, catalog, 17, RECIPIENT, output, repository)
    return output


def candidates(catalog, discovered=None):
    """Use exact model-17 records and literal model-17 siblings of known UPDs."""
    rows = {STATIC_URL: {"source_url": STATIC_URL, "static": True}}
    for p in catalog["packages"]:
        if p.get("package_model") == 17 and p.get("artifact_type") == "upd" and p.get("source_url"):
            if p["source_url"].split("?", 1)[0].endswith("/connectx.upd"):
                rows.setdefault(p["source_url"], {"source_url": p["source_url"], "static": True})
                if p.get("artifact_status") == "preserved":
                    source_key = f"release:{p['release_tag']}/{p['release_asset']}"
                    rows[source_key] = {**p, "source_key": source_key}
            else:
                rows[p["source_url"]] = p
    import re
    for p in catalog["packages"]:
        url = p.get("source_url", "")
        if not official_firmware_url(url):
            continue
        match = re.fullmatch(r"(\d+\.\d+-\d+)-1-\d+\.upd", url.rsplit("/", 1)[-1])
        if match:
            filename = match[1] + "-1-17.upd"
            sibling = url.rsplit("/", 1)[0] + "/" + filename
            rows.setdefault(sibling, {"source_url": sibling, "version": match[1], "filename": filename})
    for item in (discovered or {}).get("candidates", []):
        if item.get("package_model") != 17:
            continue
        url = item["url"]
        if url.startswith("http://"):
            url = "https://" + url[7:]
        if not official_firmware_url(url):
            continue
        from urllib.parse import urlsplit
        filename = urlsplit(url).path.rsplit("/", 1)[-1]
        match = re.fullmatch(r"(\d+\.\d+-\d+)-1-17\.upd", filename)
        if match:
            rows.setdefault(url, {"source_url": url, "version": match[1], "filename": filename})
        elif filename == "connectx.upd":
            rows.setdefault(url, {"source_url": url, "static": True})
    return list(rows.values())


def bundle(directory, destination):
    """Produce reproducible bundles, including the original filesystem links."""
    modes = {}
    manifest = directory / "inventory.json"
    if manifest.exists():
        report = json.loads(manifest.read_text())
        for section in report["sections"]:
            prefix = f"{Path(report['source']).stem}-section-{section['index']:02d}-{section['name']}-files/"
            for entry in section.get("members", []) + section.get("filesystem", {}).get("entries", []):
                name = prefix.rstrip("/") if entry["path"] == "." else prefix + entry["path"]
                modes[name] = entry["mode"]
    with tarfile.open(destination, "w:xz", preset=0) as archive:
        for path in sorted(directory.rglob("*")):
            info = archive.gettarinfo(str(path), arcname=path.relative_to(directory).as_posix())
            info.uid = info.gid = info.mtime = 0
            info.uname = info.gname = ""
            info.mode = modes.get(info.name, 0o755 if info.isdir() else 0o777 if info.issym() else 0o644)
            if info.isfile():
                with path.open("rb") as stream:
                    archive.addfile(info, stream)
            else:
                archive.addfile(info)


def catalog_id(candidate, snapshot):
    if candidate.get("id"):
        return candidate["id"]
    if candidate.get("filename"):
        return Path(candidate["filename"]).stem
    return snapshot["id"]


def registered(catalog, candidate, snapshot):
    return any(p["id"] == catalog_id(candidate, snapshot) and p.get("artifact_status") == "preserved"
               and p.get("sha256") == snapshot["sha256"] for p in catalog["packages"])


def write_inventory(root, identity, report):
    """Use the archive's shared section, extraction and filesystem directories."""
    from sonos_firmware.upd import Section
    fields = Section.__dataclass_fields__
    write_json(root / "upd" / (identity + ".json"), {
        "package_id": identity, "bytes": report["bytes"], "sha256": report["sha256"],
        "sections": [{key: value for key, value in section.items() if key in fields}
                     for section in report["sections"]],
    })
    receipt = {**report, "sections": []}
    for section in report["sections"]:
        entry = dict(section)
        filesystem = entry.pop("filesystem", None)
        if filesystem is not None:
            filename = f"{identity}-section-{section['index']:02d}.json"
            write_json(root / "filesystems" / filename, filesystem)
            entry["filesystem_manifest"] = "filesystems/" + filename
        receipt["sections"].append(entry)
    write_json(root / "raw" / (identity + ".json"), receipt)


def register_published(catalog, candidate, snapshot, report, directory, args):
    """Register individual payloads only after every upload is verified."""
    package_id = catalog_id(candidate, snapshot)
    if registered(catalog, candidate, snapshot):
        return
    components = [c for c in report["components"]
                  if c["kind"] not in {"stored-section", "kernel-data"}]
    for component in components:
        upload_small_verified(args.repository, snapshot["release_tag"],
                              directory / component["filename"], component["sha256"])
    package = {"id": package_id, "version": snapshot["version"], "package_model": 17,
               "product": "ConnectX", "filename": snapshot["release_asset"],
               "artifact_type": "upd", "artifact_status": "preserved",
               "raw_status": "complete" if snapshot["complete"] else "encrypted",
               "source_url": candidate["source_url"], "bytes": snapshot["bytes"],
               "sha256": snapshot["sha256"], "release_tag": snapshot["release_tag"],
               "release_asset": snapshot["release_asset"]}
    catalog["packages"] = [p for p in catalog["packages"] if p["id"] != package_id] + [package]
    catalog["packages"].sort(key=lambda p: (p["version"], p["package_model"], p["id"]))
    raw = [{**c, "package_id": package_id, "release_tag": snapshot["release_tag"],
            "release_asset": c["filename"]} for c in components]
    catalog["raw_images"] = [r for r in catalog["raw_images"] if r.get("package_id") != package_id] + raw
    write_json(ROOT / "data/upd" / (package_id + ".json"),
               {"package_id": package_id, "bytes": snapshot["bytes"], "sha256": snapshot["sha256"],
                "sections": [s for s in report["sections"]]})
    write_json(ROOT / "data/raw" / (package_id + ".json"), {"source": snapshot["release_asset"], "components": raw})
    write_json(args.catalog, catalog)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--key", type=Path)
    parser.add_argument("--catalog", type=Path, default=ROOT / "data/catalog.json")
    parser.add_argument("--frontier", type=Path, default=ROOT / "data/discovery/firmware-frontier.json")
    parser.add_argument("--output", type=Path, default=ROOT / "data")
    parser.add_argument("--repository", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--publish", action="store_true")
    parser.add_argument("--max-packages", type=int, default=0, help="successful new snapshots per run; 0 means all")
    args = parser.parse_args()
    args.work_dir.mkdir(parents=True, exist_ok=True)
    catalog = json.loads(args.catalog.read_text())
    index_path = args.output / "discovery/connectx-index.json"
    index = json.loads(index_path.read_text()) if index_path.exists() else {"schema_version": 1, "snapshots": [], "sources": {}}
    key = args.key or recover_key(catalog, args.work_dir, args.repository)
    if key_recipient_id(load_private_key(key)) != RECIPIENT:
        raise ValueError("key recipient mismatch")
    processed = 0
    failures = []
    discovered = json.loads(args.frontier.read_text()) if args.frontier.exists() else {}
    rows = candidates(catalog, discovered)
    # Static must be fetched every run; otherwise finish pending history first.
    rows.sort(key=lambda p: (not p.get("static", False), p.get("source_key", p["source_url"]) in index["sources"]))
    try:
        for candidate in rows:
            url = candidate["source_url"]
            source_key = candidate.get("source_key", url)
            previous = index["sources"].get(source_key, {})
            if not candidate.get("static") and previous.get("sha256") and any(
                s["sha256"] == previous["sha256"] and s["complete"] and s.get("extraction_sha256")
                and (not args.publish or (s.get("release_tag") and registered(catalog, candidate, s)))
                for s in index["snapshots"]
            ):
                continue
            temporary = args.work_dir / "download.upd"
            try:
                if candidate.get("artifact_status") == "preserved":
                    data = download_release(candidate, temporary, args.repository)
                else:
                    data = acquire(url, temporary)
                version, digest = package_version(data), sha256(data)
                if candidate.get("version") and candidate["version"] != version:
                    raise ValueError("source header does not match evidenced version")
                identity = f"connectx-{version}-{digest[:16]}"
                prior = next((s for s in index["snapshots"] if s["sha256"] == digest), None)
                index["sources"][source_key] = {"version": version, "sha256": digest}
                if prior and prior["complete"] and prior.get("extraction_sha256") and (
                    not args.publish or (prior.get("release_tag") and registered(catalog, candidate, prior))
                ):
                    write_json(index_path, index)
                    continue
                source = args.work_dir / (identity + ".upd")
                temporary.replace(source)
                directory = args.work_dir / identity
                if directory.exists():
                    shutil.rmtree(directory)
                report = extract_package(source, directory, key)
                report.update(id=identity)
                report_path = args.output / "raw" / (identity + ".json")
                write_json(directory / "inventory.json", report)
                snapshot = {"id": identity, "version": version, "sha256": digest,
                            "bytes": len(data), "complete": report["complete"],
                            "manifest": "raw/" + report_path.name}
                archive = args.work_dir / (identity + "-extracted.tar.xz")
                bundle(directory, archive)
                snapshot.update(extraction_asset=archive.name, extraction_sha256=sha256(archive.read_bytes()))
                if args.publish:
                    tag = "firmware-" + version
                    ensure_release(args.repository, tag, "Sonos firmware " + version,
                                   "Official firmware, complete section extraction and filesystem inventories.")
                    for asset in (source, archive):
                        upload_small_verified(args.repository, tag, asset, sha256(asset.read_bytes()))
                    snapshot.update(release_tag=tag, release_asset=source.name, extraction_asset=archive.name,
                                    extraction_sha256=sha256(archive.read_bytes()))
                    register_published(catalog, candidate, snapshot, report, directory, args)
                write_inventory(args.output, identity, report)
                index["snapshots"] = [s for s in index["snapshots"] if s["sha256"] != digest] + [snapshot]
                index["snapshots"].sort(key=lambda s: (s["version"], s["sha256"]))
                write_json(index_path, index)
                processed += 1
                print(f"inventoried {identity}: complete={report['complete']}", flush=True)
                shutil.rmtree(directory)
                if args.publish:
                    source.unlink()
                    archive.unlink()
                if args.max_packages and processed >= args.max_packages:
                    break
            except urllib.error.HTTPError as error:
                if error.code not in (404, 410):
                    failures.append({"url": url, "error": str(error)})
                    if error.code in (403, 429):
                        break
                else:
                    index["sources"][source_key] = {"http_status": error.code}
            except Exception as error:
                failures.append({"url": url, "error": str(error)})
        write_json(index_path, index)
        write_json(args.output / "discovery/connectx-scan.json", {"candidate_total": len(rows), "processed": processed, "errors": failures})
    finally:
        if not args.key:
            key.unlink(missing_ok=True)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
