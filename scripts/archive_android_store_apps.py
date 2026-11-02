#!/usr/bin/env python3
"""Acquire, verify, and preserve Sonos Android store delivery sets."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import time
import urllib.parse
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from xml.etree import ElementTree

ROOT = Path(__file__).resolve().parents[1]
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"
APPS = {
    "com.sonos.acr2": {
        "family": "s2",
        "signer_sha256": "7c34eb3cfbda05faf56e8890a2abbac14b3036e6e4358849b98e8819b6b7b329",
        "apkpure_url": "https://apkpure.com/sonos/com.sonos.acr2",
    },
    "com.sonos.acr": {
        "family": "s1",
        "signer_sha256": "4f1cb188a01ef0f7c763bb2d4bd0cd52b52a830cc852bfc4e4191af83c4cd196",
        "apkpure_url": "https://apkpure.com/sonos-s1-controller/com.sonos.acr",
    },
}
RELEASES = {
    ("google-play-direct", "s2"): "apps-google-play-s2",
    ("google-play-direct", "s1"): "apps-google-play-s1",
    ("apkpure-recovery", "s2"): "apps-google-play-recovery-s2",
    ("apkpure-recovery", "s1"): "apps-google-play-recovery-s1",
}


def digest(path: Path) -> tuple[int, str]:
    hasher = hashlib.sha256()
    with path.open("rb") as source:
        while chunk := source.read(1024 * 1024):
            hasher.update(chunk)
    return path.stat().st_size, hasher.hexdigest()


def verify_apk(path: Path, expected: str) -> dict:
    output = subprocess.check_output(
        [str(ROOT / "tools/android/verify-apk"), str(path)], text=True
    )
    fields: dict[str, str] = {}
    for line in output.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            fields[key] = value
    signer = fields.get("signer.1.sha256")
    if fields.get("verified") != "true" or signer != expected:
        raise RuntimeError(f"signature verification failed for {path.name}: {fields}")
    return {
        "verified": True,
        "signer_sha256": signer,
        "signer_subject": fields.get("signer.1.subject"),
        "schemes": [name for name in ("v1", "v2", "v3", "v4")
                    if fields.get(name) == "true"],
    }


def apk_metadata(path: Path, scratch: Path) -> dict:
    decoded = scratch / (path.name + "-decoded")
    subprocess.run([
        "java", "-jar", str(ROOT / "tools/vendor/apktool-3.0.3.jar"),
        "d", "-f", "-s", str(path), "-o", str(decoded),
    ], check=True, stdout=subprocess.DEVNULL)
    root = ElementTree.parse(decoded / "AndroidManifest.xml").getroot()
    apktool_yml = (decoded / "apktool.yml").read_text()
    yml_code = re.search(r"^\s*versionCode:\s*['\"]?([^'\"\n]+)", apktool_yml, re.MULTILINE)
    yml_name = re.search(r"^\s*versionName:\s*['\"]?([^'\"\n]+)", apktool_yml, re.MULTILINE)
    return {
        "package": root.attrib["package"],
        "version_code": root.attrib.get(ANDROID_NS + "versionCode") or
                        (yml_code.group(1).strip() if yml_code else None),
        "version_name": root.attrib.get(ANDROID_NS + "versionName") or
                        (yml_name.group(1).strip() if yml_name else None),
    }


def unpack_delivery(source: Path, scratch: Path) -> tuple[dict, list[Path]]:
    if source.is_dir():
        files = sorted(x for x in source.rglob("*") if x.is_file() and x.name != "apkeep.ini")
        apks = [x for x in files if x.suffix.lower() == ".apk"]
        if not apks:
            raise RuntimeError(f"no APKs in direct delivery directory {source}")
        preferred = next((x for x in apks if x.name in {"base.apk", "base-master.apk"}), apks[0])
        return apk_metadata(preferred, scratch), files
    if source.suffix.lower() == ".xapk":
        output = scratch / (source.stem + "-components")
        output.mkdir()
        with zipfile.ZipFile(source) as archive:
            metadata = json.loads(archive.read("manifest.json"))
            components = []
            for member in archive.namelist():
                if not member.lower().endswith(".apk"):
                    continue
                target = output / Path(member).name
                with archive.open(member) as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                components.append(target)
        return {
            "package": metadata["package_name"],
            "version_code": str(metadata["version_code"]),
            "version_name": metadata["version_name"],
        }, sorted(components)
    return apk_metadata(source, scratch), [source]


def release_id(repository: str, tag: str) -> int | None:
    endpoint = f"repos/{repository}/releases/tags/{urllib.parse.quote(tag, safe='')}"
    for attempt in range(130):
        result = subprocess.run(
            ["gh", "api", endpoint, "--jq", ".id"], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if result.returncode == 0:
            return int(result.stdout.strip())
        if "HTTP 404" in result.stdout:
            return None
        if "rate limit" in result.stdout.lower() and attempt < 129:
            print(f"GitHub rate-limited release lookup; retrying in 30s ({attempt + 1}/130)",
                  flush=True)
            time.sleep(30)
            continue
        raise RuntimeError(f"GitHub release lookup failed for {tag}: {result.stdout}")
    raise AssertionError("unreachable")


def ensure_release(repository: str, tag: str, source_kind: str) -> int:
    existing = release_id(repository, tag)
    if existing is not None:
        return existing
    provenance = (
        "Packages acquired directly from Google Play using an authenticated dedicated account."
        if source_kind == "google-play-direct" else
        "Third-party APKPure recovery copies of Google Play delivery sets; not direct-Play provenance."
    )
    created = subprocess.run([
        "gh", "release", "create", tag, "--repo", repository,
        "--title", tag.replace("-", " ").title(),
        "--notes", provenance + " Every APK signature and SHA-256 is recorded in "
        "data/apps/android-store-archive.json.",
    ], text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if created.returncode == 0:
        found = release_id(repository, tag)
        if found is None:
            raise RuntimeError(f"created release {tag} but could not resolve its ID")
        return found
    # `gh release view` can transiently fail even though the tag already exists.
    # Treat GitHub's explicit already-exists response as successful idempotency,
    # but preserve all other creation failures.
    if "Release.tag_name already exists" in created.stdout:
        found = release_id(repository, tag)
        if found is not None:
            return found
    raise RuntimeError(f"could not create or find release {tag}: {created.stdout}")


def remote_assets(repository: str, numeric_release_id: int) -> dict[str, dict]:
    endpoint = f"repos/{repository}/releases/{numeric_release_id}/assets?per_page=100"
    for attempt in range(130):
        result = subprocess.run(
            ["gh", "api", "--paginate", "--slurp", endpoint], text=True,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        )
        if result.returncode == 0:
            pages = json.loads(result.stdout)
            return {item["name"]: item for page in pages for item in page}
        if "rate limit" in result.stdout.lower() and attempt < 129:
            print(f"GitHub rate-limited asset lookup; retrying in 30s ({attempt + 1}/130)",
                  flush=True)
            time.sleep(30)
            continue
        raise RuntimeError(f"GitHub asset lookup failed: {result.stdout}")
    raise AssertionError("unreachable")


def upload_batch(repository: str, tag: str, numeric_release_id: int,
                 files: list[tuple[Path, str]]) -> dict[str, dict]:
    assets = remote_assets(repository, numeric_release_id)
    missing = [(path, name) for path, name in files if name not in assets]
    if missing:
        with tempfile.TemporaryDirectory(prefix="sonos-release-assets-") as temporary:
            upload_paths = []
            for path, name in missing:
                upload_path = Path(temporary) / name
                try:
                    os.link(path, upload_path)
                except OSError:
                    shutil.copy2(path, upload_path)
                upload_paths.append(str(upload_path))
            subprocess.run(
                ["gh", "release", "upload", tag, *upload_paths, "--repo", repository],
                check=True,
            )
        assets = remote_assets(repository, numeric_release_id)
    records = {}
    for path, name in files:
        remote = assets.get(name)
        if remote is None:
            raise RuntimeError(f"GitHub asset missing after upload: {tag}/{name}")
        size, sha256 = digest(path)
        remote_sha = (remote.get("digest") or "").removeprefix("sha256:")
        if remote["size"] != size or (remote_sha and remote_sha != sha256):
            raise RuntimeError(f"GitHub asset mismatch: {tag}/{name}")
        records[name] = {
            "release_asset": name,
            "release_url": remote["browser_download_url"],
        }
    return records


def acquire(package: str, source_kind: str, output: Path, apkeep: str,
            accept_google_play_tos: bool = False,
            version_selector: str | None = None) -> Path:
    if source_kind == "apkpure-recovery":
        app = f"{package}@{version_selector}" if version_selector else package
        command = [apkeep, "-a", app, "-d", "apk-pure", str(output)]
    else:
        if version_selector:
            raise RuntimeError("version selectors are not supported by apkeep's direct Play CLI")
        email = os.environ.get("GOOGLE_PLAY_EMAIL")
        aas = os.environ.get("GOOGLE_PLAY_AAS_TOKEN")
        auth = os.environ.get("GOOGLE_PLAY_AUTH_TOKEN")
        if not email or not (aas or auth):
            raise RuntimeError("direct Play acquisition requires GOOGLE_PLAY_EMAIL and "
                               "GOOGLE_PLAY_AAS_TOKEN or GOOGLE_PLAY_AUTH_TOKEN")
        config = output / "apkeep.ini"
        token_line = f"aas_token = {aas}" if aas else f"auth_token = {auth}"
        config.write_text(f"[google]\nemail = {email}\n{token_line}\n")
        config.chmod(0o600)
        command = [
            apkeep, "-a", package, "-d", "google-play",
            "-o", "split_apk=true,include_dex_metadata=true,include_additional_files=true",
            "-i", str(config), str(output),
        ]
        if accept_google_play_tos:
            command.insert(5, "--accept-tos")
    subprocess.run(command, check=True)
    candidates = [x for x in output.iterdir()
                  if x.name != "apkeep.ini" and (x.is_dir() or x.suffix in {".apk", ".xapk"})]
    if len(candidates) != 1:
        raise RuntimeError(f"expected one delivery for {package}, found {candidates}")
    return candidates[0]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", choices=("google-play-direct", "apkpure-recovery"),
                        required=True)
    parser.add_argument("--package", choices=tuple(APPS), action="append")
    parser.add_argument("--version-selector",
                        help="APKPure history selector; requires exactly one --package")
    parser.add_argument("--input", type=Path, action="append",
                        help="pre-fetched APK/XAPK; order must match --package")
    parser.add_argument("--receipt", type=Path,
                        default=ROOT / "data/apps/android-store-archive.json")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--apkeep", default="apkeep")
    parser.add_argument(
        "--accept-google-play-tos", action="store_true",
        help="explicitly pass apkeep's --accept-tos flag; never enabled automatically",
    )
    args = parser.parse_args()
    packages = args.package or list(APPS)
    if args.version_selector and (args.source != "apkpure-recovery" or len(packages) != 1):
        parser.error("--version-selector requires APKPure recovery and exactly one --package")
    if args.input and len(args.input) != len(packages):
        parser.error("the number of --input paths must match --package")
    existing = json.loads(args.receipt.read_text()) if args.receipt.exists() else {
        "schema_version": 1, "artifacts": []
    }
    records = existing.get("artifacts", [])
    known = {(x["source_kind"], x["package"], x["version_code"]) for x in records}
    with tempfile.TemporaryDirectory(prefix="sonos-android-store-") as temporary:
        scratch = Path(temporary)
        for index, package in enumerate(packages):
            info = APPS[package]
            delivery_dir = scratch / package
            delivery_dir.mkdir()
            source = args.input[index] if args.input else acquire(
                package, args.source, delivery_dir, args.apkeep,
                args.accept_google_play_tos, args.version_selector,
            )
            metadata, components = unpack_delivery(source, scratch)
            if metadata["package"] != package:
                raise RuntimeError(f"package mismatch: expected {package}, got {metadata['package']}")
            key = (args.source, package, str(metadata["version_code"]))
            if key in known:
                print(f"already archived: {key}")
                continue
            component_records = []
            for component in components:
                size, sha256 = digest(component)
                record = {"name": component.name, "bytes": size, "sha256": sha256}
                if component.suffix.lower() == ".apk":
                    record.update(verify_apk(component, info["signer_sha256"]))
                component_records.append(record)
            tag = RELEASES[(args.source, info["family"])]
            numeric_release_id = ensure_release(args.repo, tag, args.source)
            safe_version = re.sub(r"[^A-Za-z0-9._+-]+", "_", metadata["version_name"])
            uploads: list[tuple[Path, str]] = []
            for component, record in zip(components, component_records):
                component_name = re.sub(r"[^A-Za-z0-9._-]+", "_", component.stem)
                asset_name = (f"{package}--{safe_version}--{component_name}--"
                              f"{record['sha256'][:12]}{component.suffix.lower()}")
                record.update({"release_tag": tag, "release_asset": asset_name})
                uploads.append((component, asset_name))
            wrapper = None
            if source.is_file() and source.suffix.lower() == ".xapk":
                size, sha256 = digest(source)
                name = f"{package}--{safe_version}--complete--{sha256[:12]}.xapk"
                wrapper = {"bytes": size, "sha256": sha256, "release_tag": tag,
                           "release_asset": name}
                uploads.append((source, name))
            uploaded = upload_batch(args.repo, tag, numeric_release_id, uploads)
            for record in component_records:
                record.update(uploaded[record["release_asset"]])
            if wrapper:
                wrapper.update(uploaded[wrapper["release_asset"]])
            records.append({
                "source_kind": args.source,
                "source_version_selector": args.version_selector,
                "source_url": (f"https://play.google.com/store/apps/details?id={package}"
                               if args.source == "google-play-direct" else info["apkpure_url"]),
                "acquired_at": datetime.now(timezone.utc).isoformat(),
                "acquisition_tool": {
                    "name": "apkeep", "version": "1.0.0",
                    "backend": ("google-play" if args.source == "google-play-direct"
                                else "apk-pure"),
                },
                "delivery_profile": ({
                    "device": "px_9a", "locale": "en_US", "timezone": "UTC",
                    "split_apk": True, "include_dex_metadata": True,
                    "include_additional_files": True,
                } if args.source == "google-play-direct" else None),
                "family": info["family"], "package": package,
                "version_code": str(metadata["version_code"]),
                "version_name": metadata["version_name"],
                "release_tag": tag, "complete_wrapper": wrapper,
                "components": component_records,
            })
            known.add(key)
            existing.update({
                "schema_version": 1,
                "updated": datetime.now(timezone.utc).isoformat(),
                "artifact_total": len(records), "artifacts": records,
            })
            args.receipt.parent.mkdir(parents=True, exist_ok=True)
            args.receipt.write_text(json.dumps(existing, indent=2) + "\n")
            print(f"archived {args.source} {package} {metadata['version_name']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
