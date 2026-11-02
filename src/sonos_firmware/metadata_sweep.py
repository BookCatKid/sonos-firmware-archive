"""Rate-limited exploration of synthetic Sonos update-metadata profiles."""

from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

from .metadata import (
    BASE_URI_TYPE,
    MANIFEST_URI_TYPE,
    SYNTHETIC_IDENTIFIERS,
    UPDATE_METADATA_URL,
    USER_AGENT,
    parse_update_metadata,
)

PROFILE_FIELDS = ("cmaj", "cmin", "cbld", "subm", "rev", "reg")
BASELINE = {"cmaj": 4, "cmin": 1, "cbld": 1, "subm": 100, "rev": 1, "reg": 2}


def _profile_key(profile: dict[str, int]) -> str:
    return ":".join(str(profile[field]) for field in PROFILE_FIELDS)


def build_profiles(catalog: dict, limit: int = 1200) -> list[dict[str, int]]:
    """Build a deterministic mix of known-version, axis, and pairwise profiles."""
    profiles: dict[str, dict[str, int]] = {}

    def add(**changes: int) -> None:
        profile = {**BASELINE, **changes}
        profiles.setdefault(_profile_key(profile), profile)

    add()
    versions = {
        item.get("version")
        for group in (catalog.get("packages", []), catalog.get("evidence", []))
        for item in group
        if item.get("version")
    }
    for version in sorted(versions):
        try:
            major_minor, build = version.split("-", 1)
            major, minor = major_minor.split(".", 1)
            values = {"cmaj": int(major), "cmin": int(minor), "cbld": int(build)}
        except (ValueError, TypeError):
            continue
        for submodel in (0, 1, 100):
            add(**values, subm=submodel)

    for value in range(128):
        add(cmaj=value)
    for value in range(64):
        add(cmin=value)
    for value in range(256):
        add(subm=value)
    for value in range(32):
        add(rev=value)
        add(reg=value)
    for value in (0, 1, 10, 100, 1000, 10000, 99999):
        add(cbld=value)

    index = 0
    while len(profiles) < limit:
        add(
            cmaj=(index * 37) % 128,
            cmin=(index * 17) % 64,
            cbld=(index * 7919) % 100000,
            subm=(index * 73) % 256,
            rev=(index * 7) % 32,
            reg=(index * 11) % 32,
        )
        index += 1
    return list(profiles.values())[:limit]


def fetch_profile(profile: dict[str, int], timeout: float = 20) -> bytes:
    query = urlencode({**profile, **SYNTHETIC_IDENTIFIERS})
    request = urllib.request.Request(
        f"{UPDATE_METADATA_URL}?{query}", headers={"User-Agent": USER_AGENT}
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _write_json_atomic(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def run_sweep(
    catalog: dict,
    output: Path,
    raw_directory: Path,
    limit: int = 1200,
    delay: float = 0.2,
    checkpoint_every: int = 25,
) -> dict:
    profiles = build_profiles(catalog, limit)
    planned_keys = {_profile_key(profile) for profile in profiles}
    completed: dict[str, dict] = {}
    started = datetime.now(timezone.utc).isoformat()
    if output.exists():
        previous = json.loads(output.read_text(encoding="utf-8"))
        if previous.get("schema_version") == 1:
            completed = {
                _profile_key(item["profile"]): item
                for item in previous.get("results", [])
                if _profile_key(item["profile"]) in planned_keys
            }
            started = previous.get("started", started)

    raw_directory.mkdir(parents=True, exist_ok=True)
    consecutive_errors = 0

    def report(status: str) -> dict:
        results = list(completed.values())
        variants: dict[str, dict] = {}
        for result in results:
            sha256 = result.get("sha256")
            if not sha256:
                continue
            variant = variants.setdefault(
                sha256,
                {
                    "sha256": sha256,
                    "bytes": result["bytes"],
                    "raw_file": result["raw_file"],
                    "profile_total": 0,
                    "manifest_uris": set(),
                },
            )
            variant["profile_total"] += 1
            variant["manifest_uris"].update(result.get("manifest_uris", []))
            base_uris = result.get("base_uris")
            if base_uris is None and result.get("raw_file"):
                records = parse_update_metadata(Path(result["raw_file"]).read_bytes())
                base_uris = [item["uri"] for item in records if item["type"] == BASE_URI_TYPE]
            variant.setdefault("base_uris", set()).update(base_uris or [])
        serializable_variants = []
        for variant in variants.values():
            serializable_variants.append(
                {
                    **variant,
                    "manifest_uris": sorted(variant["manifest_uris"]),
                    "base_uris": sorted(variant["base_uris"]),
                }
            )
        known_urls = {
            item.get("source_url", "").replace("http://", "https://", 1)
            for item in catalog.get("source_manifests", [])
        }
        observed_urls = {
            uri.replace("http://", "https://", 1)
            for variant in serializable_variants
            for uri in variant["manifest_uris"]
        }
        observed_base_urls = {
            uri.replace("http://", "https://", 1)
            for variant in serializable_variants
            for uri in variant["base_uris"]
        }
        package_urls = [
            item.get("source_url", "").replace("http://", "https://", 1)
            for item in catalog.get("packages", [])
        ]
        unknown_base_urls = sorted(
            uri
            for uri in observed_base_urls
            if not any(url.startswith(uri.split("^", 1)[0]) for url in package_urls)
        )
        return {
            "schema_version": 1,
            "status": status,
            "started": started,
            "updated": datetime.now(timezone.utc).isoformat(),
            "method": (
                "rate-limited GET of default-1-1.ups using public sonostool synthetic "
                "identifiers; query strings and identifier values are not retained"
            ),
            "profile_fields": list(PROFILE_FIELDS),
            "planned_profile_total": len(profiles),
            "completed_profile_total": len(results),
            "error_total": sum("error" in item for item in results),
            "unique_response_total": len(serializable_variants),
            "unknown_manifest_uris": sorted(observed_urls - known_urls),
            "unknown_base_uris": unknown_base_urls,
            "variants": sorted(serializable_variants, key=lambda item: item["sha256"]),
            "results": results,
        }

    pending = [profile for profile in profiles if _profile_key(profile) not in completed]
    for position, profile in enumerate(pending, 1):
        key = _profile_key(profile)
        try:
            body = fetch_profile(profile)
            records = parse_update_metadata(body)
            digest = hashlib.sha256(body).hexdigest()
            raw_path = raw_directory / f"{digest}.ups"
            if not raw_path.exists():
                raw_path.write_bytes(body)
            completed[key] = {
                "profile": profile,
                "bytes": len(body),
                "sha256": digest,
                "raw_file": str(raw_path),
                "manifest_uris": sorted(
                    item["uri"] for item in records if item["type"] == MANIFEST_URI_TYPE
                ),
                "base_uris": sorted(
                    item["uri"] for item in records if item["type"] == BASE_URI_TYPE
                ),
            }
            consecutive_errors = 0
        except urllib.error.HTTPError as error:
            completed[key] = {"profile": profile, "http_status": error.code, "error": str(error)}
            consecutive_errors += 1
        except Exception as error:
            completed[key] = {"profile": profile, "error": str(error)}
            consecutive_errors += 1
        if position % checkpoint_every == 0:
            _write_json_atomic(output, report("running"))
        if consecutive_errors >= 20:
            final = report("stopped-after-20-consecutive-errors")
            _write_json_atomic(output, final)
            return final
        if delay:
            time.sleep(delay)

    final = report("complete")
    _write_json_atomic(output, final)
    return final
