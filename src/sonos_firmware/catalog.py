"""Catalog validation and filesystem inventory helpers."""

from __future__ import annotations

import hashlib
import json
import os
import stat
from pathlib import Path

REQUIRED_PACKAGE_FIELDS = {
    "id",
    "version",
    "package_model",
    "artifact_status",
    "raw_status",
}

DIAGNOSTIC_EVIDENCE_FIELDS = {
    "id",
    "kind",
    "version",
    "manifest_revision",
    "manifest_url",
    "update_url",
    "swgen",
    "latest_swgen",
    "source_type",
    "source_url",
    "observed",
    "redacted",
}

RELEASE_NOTE_EVIDENCE_FIELDS = {
    "id",
    "kind",
    "version",
    "release_scope",
    "source_type",
    "source_url",
    "observed",
    "redacted",
}

FORBIDDEN_EVIDENCE_FIELDS = {
    "body",
    "comment",
    "description",
    "household",
    "householdid",
    "ip",
    "lan",
    "room",
    "serial",
    "serialnumber",
    "sonosid",
    "uuid",
}

EXTRACTABLE_SECTION_TYPES = {3, 4, 6, 13}
SECTION_KIND = {3: "preinstall", 4: "rootfs", 6: "kernel", 13: "device-payload"}


def load_json(path: str | Path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


def validate_catalog(root: str | Path) -> list[str]:
    root = Path(root)
    catalog = load_json(root / "data/catalog.json")
    errors: list[str] = []
    raw_by_package_and_section: dict[tuple[str, int], list[dict]] = {}
    raw_by_package_and_kind: dict[tuple[str, str], list[dict]] = {}
    for raw in catalog.get("raw_images", []):
        package_id = raw.get("package_id")
        section_index = raw.get("section_index")
        if package_id is not None and section_index is not None:
            raw_by_package_and_section.setdefault((package_id, section_index), []).append(raw)
        if package_id is not None and raw.get("kind"):
            raw_by_package_and_kind.setdefault((package_id, raw["kind"]), []).append(raw)
    ids: set[str] = set()
    for index, package in enumerate(catalog.get("packages", [])):
        missing = REQUIRED_PACKAGE_FIELDS - package.keys()
        if missing:
            errors.append(f"packages[{index}] missing: {', '.join(sorted(missing))}")
        package_id = package.get("id")
        if package_id in ids:
            errors.append(f"duplicate package id: {package_id}")
        ids.add(package_id)
        sha = package.get("sha256")
        if sha is not None and (len(sha) != 64 or any(c not in "0123456789abcdef" for c in sha)):
            errors.append(f"{package_id}: invalid sha256")
        if package.get("artifact_status") == "preserved" and not package.get("release_asset"):
            errors.append(f"{package_id}: preserved package has no release_asset")
        if package.get("artifact_status") == "preserved" and package.get("artifact_type") == "upd":
            manifest = root / "data/upd" / f"{package_id}.json"
            if not manifest.exists():
                errors.append(f"{package_id}: preserved UPD has no section manifest")
            else:
                for section in load_json(manifest).get("sections", []):
                    if section.get("section_type") not in EXTRACTABLE_SECTION_TYPES:
                        continue
                    if section.get("encrypted") and package.get("raw_status") != "complete":
                        continue
                    matches = raw_by_package_and_section.get((package_id, section.get("index")), [])
                    if not matches:
                        # A handful of seed-era raw records predate section-index
                        # provenance but are still unambiguous by package and kind.
                        matches = raw_by_package_and_kind.get(
                            (package_id, SECTION_KIND[section["section_type"]]), []
                        )
                    if not matches:
                        errors.append(
                            f"{package_id}: complete UPD section {section.get('index')} "
                            "has no extracted release asset"
                        )
                        continue
                    if not section.get("encrypted") and not any(
                        item.get("sha256") == section.get("sha256") for item in matches
                    ):
                        errors.append(
                            f"{package_id}: plaintext section {section.get('index')} "
                            "does not match its extracted asset"
                        )
                    for item in matches:
                        if not item.get("release_tag") or not item.get("release_asset"):
                            errors.append(
                                f"{package_id}: extracted section {section.get('index')} "
                                "has no release location"
                            )
    for manifest in catalog.get("source_manifests", []):
        if not manifest.get("release_tag") or not manifest.get("release_asset"):
            errors.append(f"source manifest {manifest.get('filename')}: missing release location")
    evidence_ids: set[str] = set()
    for index, record in enumerate(catalog.get("evidence", [])):
        record_id = record.get("id")
        if not record_id:
            errors.append(f"evidence[{index}] missing id")
        if record_id in evidence_ids:
            errors.append(f"duplicate evidence id: {record_id}")
        evidence_ids.add(record_id)
        forbidden = FORBIDDEN_EVIDENCE_FIELDS & record.keys()
        if forbidden:
            errors.append(
                f"evidence[{index}] has forbidden fields: "
                f"{', '.join(sorted(forbidden))}"
            )
        if record.get("redacted") is not True:
            errors.append(f"evidence[{record_id}]: redacted must be true")
        kind = record.get("kind")
        if kind == "available-software-update":
            required = DIAGNOSTIC_EVIDENCE_FIELDS
        elif kind == "evidenced-version":
            required = RELEASE_NOTE_EVIDENCE_FIELDS
        else:
            errors.append(f"evidence[{record_id}]: unknown kind: {kind}")
            continue
        missing = required - record.keys()
        if missing:
            errors.append(f"evidence[{record_id}] missing: {', '.join(sorted(missing))}")
    return errors


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def filesystem_manifest(root: str | Path) -> dict:
    root = Path(root)
    entries = []
    for path in sorted(root.rglob("*"), key=lambda item: item.as_posix()):
        relative = path.relative_to(root).as_posix()
        mode = path.lstat().st_mode
        entry = {"path": relative, "mode": stat.S_IMODE(mode)}
        if path.is_symlink():
            entry.update(type="symlink", target=os.readlink(path))
        elif path.is_dir():
            entry.update(type="directory")
        elif path.is_file():
            entry.update(type="file", size=path.stat().st_size, sha256=_sha256(path))
        else:
            entry.update(type="special")
        entries.append(entry)
    return {"root": root.name, "entries": entries}
