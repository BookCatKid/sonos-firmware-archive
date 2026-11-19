#!/usr/bin/env python3
"""Acquire, verify, and preserve Sonos Android store delivery sets."""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sonos_firmware.apk import verify_apk, apk_metadata
from sonos_firmware.storage import file_sha256
from sonos_firmware.delivery_archive import publish_delivery, recover_deliveries, delivery_profiles
from sonos_firmware.android_store import acquire_play, GOOPDL_REVISION, safe_error, run_apkeep, store_apps, StoreError
from sonos_firmware.storage import atomic_json
from sonos_firmware.apkpure import acquire_variant

ROOT = Path(__file__).resolve().parents[1]
APPS = store_apps(ROOT)


def digest(path: Path) -> tuple[int, str]:
    return path.stat().st_size, file_sha256(path)


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
            names = set()
            for member in archive.namelist():
                if member.endswith(('/', '\\')):
                    continue
                relative = PurePosixPath(member.replace('\\', '/'))
                if relative.is_absolute() or not relative.parts or '..' in relative.parts:
                    raise RuntimeError('unsafe component path in XAPK')
                target = output.joinpath(*relative.parts)
                if not target.resolve().is_relative_to(output.resolve()):
                    raise RuntimeError('unsafe component path in XAPK')
                if relative.as_posix().casefold() in names:
                    raise RuntimeError('duplicate component path in XAPK')
                names.add(relative.as_posix().casefold())
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(member) as incoming, target.open("wb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                components.append(target)
        apks = [path for path in components if path.suffix.lower() == '.apk']
        if not apks:
            raise RuntimeError('XAPK contains no APK')
        preferred = next((path for path in apks if path.name in {
            'base.apk', 'base-master.apk', metadata['package_name'] + '.apk'}), apks[0])
        actual = apk_metadata(preferred, scratch)
        if (actual['package'] != metadata['package_name'] or
                str(actual['version_code']) != str(metadata['version_code'])):
            raise RuntimeError('XAPK manifest does not match its APK')
        return actual, sorted(components)
    return apk_metadata(source, scratch), [source]


def acquire(package: str, source_kind: str, output: Path, apkeep: str,
            version_selector: str | None = None) -> Path:
    if source_kind == "apkpure-recovery":
        app = f"{package}@{version_selector}" if version_selector else package
        command = [apkeep, "-a", app, "-d", "apk-pure", str(output)]
    else:
        raise ValueError('use acquire_play for direct Google Play delivery')
    run_apkeep(command)
    candidates = [x for x in output.iterdir()
                  if x.name != "apkeep.ini" and (x.is_dir() or x.suffix in {".apk", ".xapk"})]
    if len(candidates) != 1:
        raise RuntimeError(f"expected one delivery for {package}, found {candidates}")
    return candidates[0]


def requested_profile(args):
    if args.source == 'google-play-direct':
        return {'arch': args.play_arch, 'device': args.play_profile, 'authentication': 'anonymous',
                'split_apk': True, 'include_dex_metadata': True, 'include_additional_files': True}
    if args.variant:
        return {'arch': args.variant_arch, 'variant': args.variant}
    return None


def preserve_package(args, package, index, scratch):
    info = APPS[package]
    delivery_dir = scratch / package
    delivery_dir.mkdir()
    expected_code = args.version_code
    if not args.input and any(shutil.which(tool) is None for tool in ('java', 'javac')):
        raise StoreError('APKInspectionUnavailable')
    if args.input:
        source = args.input[index]
    elif args.source == 'google-play-direct':
        source, expected_code = acquire_play(package, delivery_dir / 'delivery', args.auth_dir,
            args.play_arch, args.play_profile, args.version_code)
    elif args.asset_url:
        source = acquire_variant(args.asset_url, args.asset_type, delivery_dir)
    else:
        source = acquire(package, args.source, delivery_dir, args.apkeep, args.version_selector)
    metadata, components = unpack_delivery(source, delivery_dir)
    if metadata["package"] != package:
        raise StoreError('APKPackageMismatch')
    if expected_code and str(metadata['version_code']) != str(expected_code):
        raise StoreError('APKVersionMismatch')
    component_records = []
    parts_root = (source if source.is_dir() else delivery_dir / (source.stem + '-components')
                  if source.suffix.lower() == '.xapk' else source.parent)
    for component in components:
        size, sha256 = digest(component)
        record = {"name": component.name, "bytes": size, "sha256": sha256}
        record['path'] = component.relative_to(parts_root).as_posix()
        if component.suffix.lower() == ".apk":
            record.update(verify_apk(component, info["signer_sha256"]))
            actual = apk_metadata(component, delivery_dir)
            if actual['package'] != package or str(actual['version_code']) != str(metadata['version_code']):
                raise StoreError('APKComponentIdentityMismatch')
            record['metadata'] = actual
        component_records.append(record)
    prefix = 'apps-google-play-' if args.source == 'google-play-direct' else 'apps-google-play-recovery-'
    tag = prefix + info['family']
    provenance = (
        'Packages acquired directly from Google Play using anonymous goopdl authentication.'
        if args.source == 'google-play-direct' else
        'Third-party APKPure recovery copies; not direct-Play provenance.'
    )
    safe_version = re.sub(r"[^A-Za-z0-9._+-]+", "_", metadata["version_name"])
    uploads = []
    for component, record in zip(components, component_records):
        if component.suffix.lower() == '.apk':
            split = record['metadata'].get('split')
            role = 'split-' + split if split else 'base'
        else:
            role = component.stem
        component_name = re.sub(r"[^A-Za-z0-9._-]+", "_", role)
        asset_name = (f"{package}--{safe_version}--{component_name}--"
                      f"{record['sha256']}{component.suffix.lower()}")
        record.update({"release_tag": tag, "release_asset": asset_name})
        uploads.append((component, record))
    wrapper = None
    if source.is_file() and source.suffix.lower() == ".xapk":
        size, sha256 = digest(source)
        name = f"{package}--{safe_version}--complete--{sha256}.xapk"
        wrapper = {"bytes": size, "sha256": sha256, "release_tag": tag,
                   "release_asset": name}
        uploads.append((source, wrapper))
    artifact = {
        "source_kind": args.source,
        "source_version_selector": args.version_selector or args.version_code,
        "source_url": (f"https://play.google.com/store/apps/details?id={package}"
                       if args.source == "google-play-direct" else args.asset_url or info["apkpure_url"]),
        "acquired_at": datetime.now(timezone.utc).isoformat(),
        "acquisition_tool": ({"name": "goopdl", "revision": GOOPDL_REVISION, "backend": "google-play"}
                             if args.source == 'google-play-direct' else
                             {"name": "sonos-firmware-archive", "backend": "apkpure-api"} if args.asset_url else
                             {"name": "apkeep", "version": "1.0.0", "backend": "apk-pure"}),
        "delivery_profile": requested_profile(args),
        "family": info["family"], "package": package,
        "version_code": str(metadata["version_code"]),
        "version_name": metadata["version_name"],
        "release_tag": tag, "complete_wrapper": wrapper,
        "components": component_records,
    }
    publish_delivery(args.receipt, args.repo, artifact, uploads,
                     title=tag.replace('-', ' ').title(), notes=provenance +
                     ' Every APK signature and SHA-256 is recorded in data/apps/android-store-archive.json.')
    print(f"archived {args.source} {package} {metadata['version_name']}")


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
    parser.add_argument('--version-code', help='expected numeric version code, verified against actual APKs')
    parser.add_argument('--asset-url', help='APKPure asset advertised by structured discovery')
    parser.add_argument('--asset-type')
    parser.add_argument('--variant-arch')
    parser.add_argument('--variant')
    parser.add_argument('--play-arch', default='arm64')
    parser.add_argument('--play-profile')
    parser.add_argument('--auth-dir', type=Path, help='ephemeral anonymous token directory, never archived')
    parser.add_argument('--result', type=Path, help='sanitized acquisition result for the retry queue')
    args = parser.parse_args()
    packages = args.package or list(APPS)
    if args.version_selector and (args.source != "apkpure-recovery" or len(packages) != 1):
        parser.error("--version-selector requires APKPure recovery and exactly one --package")
    if args.input and len(args.input) != len(packages):
        parser.error("the number of --input paths must match --package")
    if args.asset_url and (args.source != 'apkpure-recovery' or not args.asset_type or
                           not args.variant or not args.variant_arch or not args.version_code or len(packages) != 1):
        parser.error('--asset-url requires one APKPure package, asset type, variant, architecture and version code')
    if args.variant and not args.asset_url:
        parser.error('--variant requires --asset-url')
    if args.version_code and ((args.source != 'google-play-direct' and not args.asset_url) or not args.version_code.isdecimal()
                             or int(args.version_code) <= 0 or len(packages) != 1):
        parser.error('--version-code requires a versioned store source, positive integer and exactly one package')
    selector = args.version_selector or args.version_code
    recovered = recover_deliveries(args.receipt, args.repo, args.source, packages,
                                   selector=selector)
    resolved = {row['package'] for row in recovered if selector
                and selector in {row.get('source_version_selector'), *row.get('source_version_selectors', [])}
                and (args.version_code is None or str(row.get('version_code')) == args.version_code)
                and (requested_profile(args) or {}) in delivery_profiles(row)}
    errors = []
    with tempfile.TemporaryDirectory(prefix="sonos-android-store-") as temporary:
        scratch = Path(temporary)
        if args.auth_dir is None:
            args.auth_dir = scratch / 'anonymous-auth'
        for index, package in enumerate(packages):
            if package in resolved and not (args.input and args.input[index].exists()):
                print(f'reconciled prior delivery: {package}@{args.version_selector}')
                continue
            try:
                preserve_package(args, package, index, scratch)
            except Exception as error:
                sanitized = safe_error(error) if args.source == 'google-play-direct' else error
                errors.append({'package': package, 'error': str(sanitized),
                               'status_code': getattr(sanitized, 'status_code', None),
                               'observed_version_code': getattr(sanitized, 'observed_version_code', None),
                               'retry_after': getattr(sanitized, 'retry_after', None)})
                print(f'FAILED {package}: {sanitized}', flush=True)
    document = json.loads(args.receipt.read_text()) if args.receipt.exists() else {}
    pending = [row for row in document.get('pending_captures', []) if row['repository'] == args.repo
               and row['artifact']['source_kind'] == args.source and row['artifact']['package'] in packages
               and (selector is None or selector in {
                   row['artifact'].get('source_version_selector'), *row['artifact'].get('source_version_selectors', [])})]
    if args.result:
        atomic_json(args.result, {'status': 'partial' if errors or pending else 'ok', 'errors': errors,
                                  'pending_total': len(pending)})
    return 1 if errors or pending else 0


if __name__ == "__main__":
    raise SystemExit(main())
