#!/usr/bin/env python3
"""Generate missing adjacent macOS application smart diffs.

This intentionally runs on macOS because it mounts or safely extracts preserved
DMGs and uses the platform's native Mach-O tooling. Post-preservation runs can
generate only the newest adjacent pair, while scheduled/manual backfills fill
every missing historical adjacent pair. DMGs with embedded software-license
prompts are extracted without accepting the agreement.
"""

from __future__ import annotations

import argparse
import collections
import hashlib
import importlib.util
import json
import plistlib
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_PATH = ROOT / "data/apps/desktop-archive.json"
DIFF_DIR = ROOT / "data/app-diffs"
SMART_DIFF_SCRIPT = ROOT / "scripts/smart_diff_app_bundles.py"


def load_smart_diff_module():
    spec = importlib.util.spec_from_file_location("smart_diff_app_bundles", SMART_DIFF_SCRIPT)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {SMART_DIFF_SCRIPT}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def version_key(value: str) -> tuple[int, ...]:
    classic = re.fullmatch(r"classic-(\d+)([A-Za-z]*)", value or "")
    if classic:
        token = classic.group(1)
        if len(token) == 2:
            return (int(token[0]), int(token[1]), 0)
        if len(token) >= 3:
            first_two = int(token[:2])
            if first_two <= 19:
                major = first_two
                minor = int(token[2])
                patch = int(token[3:]) if len(token) > 3 else 0
            else:
                major = int(token[0])
                minor = int(token[1])
                patch = int(token[2:]) if len(token) > 2 else 0
            return (major, minor, patch)
    return tuple(int(part) for part in re.findall(r"\d+", value or ""))


def run(args: list[str], *, capture: bool = False) -> str:
    proc = subprocess.run(
        args,
        text=True,
        stdout=subprocess.PIPE if capture else None,
        stderr=subprocess.PIPE if capture else None,
        check=False,
    )
    if proc.returncode != 0:
        extra = ""
        if capture:
            extra = f"\nstdout:\n{proc.stdout}\nstderr:\n{proc.stderr}"
        raise RuntimeError(f"command failed ({proc.returncode}): {' '.join(args)}{extra}")
    return proc.stdout.strip() if capture else ""


def download(url: str, destination: Path) -> None:
    run([
        "curl", "-fL", "--retry", "3", "--retry-all-errors",
        "--output", str(destination), url,
    ])


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def artifact_cache_key(artifact: dict[str, Any]) -> str:
    return artifact.get("sha256") or artifact["release_url"]


def cached_installer_path(artifact: dict[str, Any], cache_dir: Path) -> Path:
    version = re.sub(r"[^A-Za-z0-9._-]+", "_", artifact["version"])
    identity = hashlib.sha256(
        artifact_cache_key(artifact).encode("utf-8")
    ).hexdigest()[:16]
    return cache_dir / f"{version}--{identity}.dmg"


def cached_installer(artifact: dict[str, Any], cache_dir: Path) -> Path:
    """Download one preserved installer once and verify it before reuse."""
    expected = artifact.get("sha256")
    destination = cached_installer_path(artifact, cache_dir)

    if destination.exists():
        if expected and sha256_file(destination) != expected:
            destination.unlink()
        else:
            print(f"cache hit {artifact['version']}: {destination.name}", flush=True)
            return destination

    partial = destination.with_suffix(".dmg.part")
    partial.unlink(missing_ok=True)
    print(f"download {artifact['version']}: {artifact['release_url']}", flush=True)
    download(artifact["release_url"], partial)
    if expected:
        actual = sha256_file(partial)
        if actual != expected:
            partial.unlink(missing_ok=True)
            raise RuntimeError(
                f"SHA-256 mismatch for {artifact['version']}: "
                f"expected {expected}, got {actual}"
            )
    partial.replace(destination)
    return destination


def _find_app(root: Path) -> Path:
    app_candidates = sorted(root.glob("*.app"))
    if not app_candidates:
        app_candidates = sorted(root.rglob("Sonos.app"))
    if not app_candidates:
        app_candidates = sorted(root.rglob("*.app"))
    if not app_candidates:
        raise RuntimeError(f"no app bundle found under {root}")
    return app_candidates[0]


def image_has_sla(dmg: Path) -> bool:
    """Detect an embedded disk-image SLA without accepting or mounting it."""
    proc = subprocess.run(
        ["hdiutil", "imageinfo", "-plist", str(dmg)],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if proc.returncode != 0:
        return False
    try:
        payload = plistlib.loads(proc.stdout)
    except Exception:
        return False

    def scan(value: Any) -> bool:
        if isinstance(value, dict):
            for key, item in value.items():
                if key == "Software License Agreement" and item is True:
                    return True
                if scan(item):
                    return True
        elif isinstance(value, list):
            return any(scan(item) for item in value)
        return False

    return scan(payload)


def extracted_app(dmg: Path, destination: Path) -> Path:
    """Extract an SLA-bearing DMG without accepting its license agreement."""
    seven_zip = shutil.which("7z") or shutil.which("7zz")
    if seven_zip is None:
        raise RuntimeError(
            "7z is required to inspect DMGs with embedded license agreements "
            "without accepting the agreement"
        )
    destination.mkdir(parents=True, exist_ok=True)
    run([seven_zip, "x", "-y", f"-o{destination}", str(dmg)])
    return _find_app(destination)


def mounted_app(dmg: Path, mountpoint: Path) -> tuple[Path, str]:
    mountpoint.mkdir(parents=True, exist_ok=True)
    output = run([
        "hdiutil", "attach", "-readonly", "-nobrowse",
        "-mountpoint", str(mountpoint), str(dmg),
    ], capture=True)
    try:
        app = _find_app(mountpoint)
    except RuntimeError as exc:
        raise RuntimeError(f"no app bundle found after mounting {dmg}\n{output}") from exc
    return app, str(mountpoint)


def detach(mountpoint: str) -> None:
    subprocess.run(
        ["hdiutil", "detach", mountpoint],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )


def canonical_artifacts() -> dict[str, list[dict[str, Any]]]:
    data = json.loads(ARCHIVE_PATH.read_text(encoding="utf-8"))
    grouped: dict[str, dict[str, list[dict[str, Any]]]] = {}
    for artifact in data.get("artifacts", []):
        if artifact.get("platform") != "macos":
            continue
        family = artifact.get("family") or "unknown"
        version = artifact.get("version")
        if not version or not artifact.get("release_url"):
            continue
        grouped.setdefault(family, {}).setdefault(version, []).append(artifact)

    result: dict[str, list[dict[str, Any]]] = {}
    for family, versions in grouped.items():
        chosen = []
        for version, rows in versions.items():
            rows.sort(
                key=lambda row: (
                    bool(row.get("sha256")),
                    row.get("bytes") or 0,
                    row.get("wayback_timestamp") or "",
                ),
                reverse=True,
            )
            chosen.append(rows[0])
        result[family] = sorted(
            chosen, key=lambda row: version_key(row["version"]), reverse=True
        )
    return result


def output_path(family: str, old_version: str, new_version: str) -> Path:
    safe = lambda value: re.sub(r"[^A-Za-z0-9._-]+", "_", value)
    return DIFF_DIR / (
        f"macos-{safe(family)}-{safe(old_version)}_to_{safe(new_version)}.json"
    )


def missing_pairs(*, all_missing: bool, per_family: int, latest_only: bool) -> list[tuple[str, dict, dict, Path]]:
    selected = []
    for family, rows in canonical_artifacts().items():
        family_count = 0
        for index in range(len(rows) - 1):
            if latest_only and index > 0:
                break
            newer = rows[index]
            older = rows[index + 1]
            destination = output_path(family, older["version"], newer["version"])
            if destination.exists():
                continue
            selected.append((family, older, newer, destination))
            family_count += 1
            if not all_missing and family_count >= per_family:
                break
    return selected


def analyze_pair(
    module,
    family: str,
    old: dict,
    new: dict,
    destination: Path,
    cache_dir: Path,
) -> None:
    old_dmg = cached_installer(old, cache_dir)
    new_dmg = cached_installer(new, cache_dir)
    with tempfile.TemporaryDirectory(prefix="sonos-smart-diff-mounts-") as temp_raw:
        temp = Path(temp_raw)
        old_mount = temp / "old-mount"
        new_mount = temp / "new-mount"

        old_mount_name = new_mount_name = None
        try:
            requires_extract = image_has_sla(old_dmg) or image_has_sla(new_dmg)
            if requires_extract:
                print(
                    f"extract SLA-bearing pair without accepting license: "
                    f"{old['version']} -> {new['version']}",
                    flush=True,
                )
                old_app = extracted_app(old_dmg, temp / "old-extracted")
                new_app = extracted_app(new_dmg, temp / "new-extracted")
            else:
                old_app, old_mount_name = mounted_app(old_dmg, old_mount)
                new_app, new_mount_name = mounted_app(new_dmg, new_mount)
            print(
                f"analyze macos/{family} {old['version']} -> {new['version']}",
                flush=True,
            )
            payload = module.build_diff(
                old_app,
                new_app,
                old_version=old["version"],
                new_version=new["version"],
                platform="macos",
                family=family,
            )
            payload["source"] = {
                "old_record": {
                    "version": old["version"],
                    "sha256": old.get("sha256"),
                    "release_url": old.get("release_url"),
                },
                "new_record": {
                    "version": new["version"],
                    "sha256": new.get("sha256"),
                    "release_url": new.get("release_url"),
                },
                "method": "content-first extracted application bundle semantic diff",
            }
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(
                json.dumps(
                    payload,
                    ensure_ascii=False,
                    separators=(",", ":"),
                    sort_keys=True,
                ) + "\n",
                encoding="utf-8",
            )
            print(
                f"wrote {destination.relative_to(ROOT)}: "
                f"+{payload['summary']['added']} "
                f"-{payload['summary']['removed']} "
                f"~{payload['summary']['changed']} "
                f"semantic={payload['summary']['semantically_analyzed_changed_files']}",
                flush=True,
            )
        finally:
            if new_mount_name:
                detach(new_mount_name)
            if old_mount_name:
                detach(old_mount_name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--all-missing",
        action="store_true",
        help="generate every missing adjacent macOS pair instead of a bounded batch",
    )
    parser.add_argument(
        "--per-family",
        type=int,
        default=1,
        help="maximum missing adjacent pairs per family (default: 1)",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="only consider the newest adjacent pair in each family",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print missing pairs without downloading or analyzing",
    )
    args = parser.parse_args()

    if shutil.which("hdiutil") is None:
        raise SystemExit("hdiutil is required; run this script on macOS")

    pairs = missing_pairs(
        all_missing=args.all_missing,
        per_family=max(1, args.per_family),
        latest_only=args.latest_only,
    )
    if not pairs:
        print("no missing macOS adjacent smart diffs")
        return

    for family, old, new, destination in pairs:
        print(
            f"{family}: {old['version']} -> {new['version']} "
            f"=> {destination.relative_to(ROOT)}"
        )
    if args.list:
        return

    module = load_smart_diff_module()
    remaining_uses = collections.Counter(
        artifact_cache_key(artifact)
        for _, old, new, _ in pairs
        for artifact in (old, new)
    )
    with tempfile.TemporaryDirectory(prefix="sonos-smart-diff-cache-") as cache_raw:
        cache_dir = Path(cache_raw)
        failures = []
        for family, old, new, destination in pairs:
            try:
                analyze_pair(module, family, old, new, destination, cache_dir)
            except Exception as exc:
                failures.append((family, old["version"], new["version"], exc))
                print(
                    f"FAILED {family} {old['version']} -> {new['version']}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            finally:
                for artifact in (old, new):
                    key = artifact_cache_key(artifact)
                    remaining_uses[key] -= 1
                    if remaining_uses[key] <= 0:
                        cached_installer_path(artifact, cache_dir).unlink(missing_ok=True)
        if failures:
            print(f"{len(failures)} smart diff pair(s) failed:", flush=True)
            for family, old_version, new_version, exc in failures:
                print(
                    f"  {family}: {old_version} -> {new_version}: "
                    f"{type(exc).__name__}: {exc}",
                    flush=True,
                )
            raise SystemExit(1)


if __name__ == "__main__":
    main()
