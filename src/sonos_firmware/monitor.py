"""Deterministic monitoring of public Sonos firmware sources."""

from __future__ import annotations

import hashlib
import json
import re
import tempfile
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .discovery import Candidate, USER_AGENT, parse_manifest, probe_all
from .crawl import crawl_manifests
from .metadata import (
    MANIFEST_URI_TYPE, UpdateProfile, fetch_modern_manifest,
    fetch_update_metadata, parse_update_metadata,
)

RELEASE_NOTES_URL = "https://support.sonos.com/en-us/article/release-notes-sonos-system-updates"
VERSION_PATTERN = re.compile(r"\b\d{2,3}\.\d+-\d{5}\b")


def secure_url(url: str) -> str:
    return "https://" + url.removeprefix("http://") if url.startswith("http://") else url


def alternate_host(candidate: Candidate) -> Candidate:
    replacements = (
        ("https://update-firmware.sonos.com/", "https://update.sonos.com/"),
        ("https://update.sonos.com/", "https://update-firmware.sonos.com/"),
    )
    url = candidate.url
    for old, new in replacements:
        if url.startswith(old):
            url = url.replace(old, new, 1)
            break
    return Candidate(
        version=candidate.version,
        package_model=candidate.package_model,
        url=url,
        source=candidate.source,
        filename=candidate.filename,
    )


def package_candidate(package: dict) -> Candidate:
    return Candidate(
        version=package["version"],
        package_model=package["package_model"],
        url=secure_url(package["source_url"]),
        source=package.get("source_manifest", "catalog"),
        filename=package["filename"],
    )


def candidate_is_preserved(candidate: dict, packages: list[dict]) -> bool:
    """Match both exact URLs and stable manifest identity fields."""
    url = secure_url(candidate["url"])
    for package in packages:
        if package.get("artifact_status") != "preserved":
            continue
        if secure_url(package.get("source_url", "")) == url:
            return True
        if (
            package.get("version") == candidate["version"]
            and package.get("package_model") == candidate["package_model"]
            and package.get("filename") == candidate["filename"]
        ):
            return True
    return False


def summarize_changes(
    catalog: dict,
    manifests: list[dict],
    current_probes: list[dict],
    missing_probes: list[dict],
    alternate_missing_probes: list[dict],
    release_note_versions: set[str] | None = None,
    control_probes: list[dict] | None = None,
) -> dict:
    known_manifest_urls = {
        secure_url(item.get("source_url", "")) for item in catalog["source_manifests"]
    }
    known_manifest_hashes = {
        item.get("sha256") for item in catalog["source_manifests"] if item.get("sha256")
    }
    known_versions = {
        value
        for item in catalog["source_manifests"]
        for value in (item.get("version"), item.get("default_version"))
        if value
    }
    # Older catalog manifest rows did not retain default_version, but every
    # expanded package does retain its version.
    known_versions.update(item.get("version") for item in catalog["packages"] if item.get("version"))
    known_versions.update(item.get("version") for item in catalog.get("evidence", []) if item.get("version"))
    unknown_manifest_urls = sorted(
        item["url"] for item in manifests if secure_url(item["url"]) not in known_manifest_urls
    )
    unknown_manifest_hashes = sorted(
        item["sha256"] for item in manifests if item["sha256"] not in known_manifest_hashes
    )
    observed_versions = {
        value
        for item in manifests
        for value in (
            item["metadata"].get("system_version"),
            item["metadata"].get("default_version"),
        )
        if value
    }
    unknown_versions = sorted(observed_versions - known_versions)
    unknown_release_note_versions = sorted((release_note_versions or set()) - known_versions)
    available_uncatalogued = [
        item
        for item in current_probes
        if item.get("available") and not candidate_is_preserved(item, catalog["packages"])
    ]
    revived = [
        item
        for item in [*missing_probes, *alternate_missing_probes]
        if item.get("available")
    ]
    probe_errors = [
        item
        for item in [
            *current_probes,
            *missing_probes,
            *alternate_missing_probes,
            *(control_probes or []),
        ]
        if item.get("error")
    ]
    changes = {
        "unknown_manifest_urls": unknown_manifest_urls,
        "unknown_manifest_hashes": unknown_manifest_hashes,
        "unknown_versions": unknown_versions,
        "unknown_release_note_versions": unknown_release_note_versions,
        "available_uncatalogued_candidates": available_uncatalogued,
        "known_missing_now_available": revived,
    }
    return {
        **changes,
        "probe_errors": probe_errors,
        "change_detected": any(changes.values()),
        "current_available_candidates_all_preserved": not available_uncatalogued
        and not any(item.get("error") for item in current_probes),
    }


def fetch_bytes(url: str, timeout: float = 30) -> bytes:
    request = urllib.request.Request(secure_url(url), headers={"User-Agent": USER_AGENT})
    for attempt in range(3):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read()
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def fetch_metadata_with_retries() -> tuple[bytes, dict]:
    for attempt in range(3):
        try:
            return fetch_update_metadata(UpdateProfile())
        except Exception:
            if attempt == 2:
                raise
            time.sleep(2**attempt)
    raise AssertionError("unreachable")


def run_monitor(catalog: dict, workers: int = 6, *, seed_urls=None, evidence_dir=None, max_manifests=256, probe_budget_seconds: float | None = None, include_history: bool = True) -> dict:
    ups, response = fetch_metadata_with_retries()
    records = parse_update_metadata(ups)
    manifest_urls = sorted(
        {secure_url(item["uri"]) for item in records if item["type"] == MANIFEST_URI_TYPE}
    )
    ups_manifest_urls = manifest_urls.copy()
    # An S2 outage must not block S1 discovery or preservation: report the
    # modern failure and continue with the S1 manifests already fetched.
    modern_body, modern_response, modern_error = None, None, None
    for attempt in range(3):
        try:
            modern_body, modern_response = fetch_modern_manifest()
            break
        except Exception as error:
            modern_error = error
            if attempt < 2:
                time.sleep(2**attempt)
    if modern_response is not None:
        manifest_urls = sorted(set(manifest_urls) | {modern_response["url"]})
    # Only the already-fetched modern body is seeded. Every other feed
    # manifest is retrieved through the crawler's guarded, size-limited and
    # budgeted path so feed data cannot trigger unguarded downloads.
    initial = [
        {"url": url, "body": modern_body}
        if modern_response is not None and url == modern_response["url"]
        else {"url": url}
        for url in manifest_urls
    ]
    probe_deadline = (
        time.monotonic() + probe_budget_seconds if probe_budget_seconds else None
    )
    graph = crawl_manifests(catalog, initial, seed_urls or [],
                            Path(evidence_dir or "manifest-evidence"), max_manifests=max_manifests, include_history=include_history)
    manifests = graph["manifests"]
    current_candidates = {c.url: c for c in graph.pop("candidates")}

    current_probes = [] if graph["blocked"] else probe_all(list(current_candidates.values()), workers, deadline=probe_deadline)
    missing_packages = [
        item for item in catalog["packages"] if include_history and item["artifact_status"] == "missing-cdn"
    ]
    missing_candidates = [package_candidate(item) for item in missing_packages]
    challenged = graph["blocked"] or any(x.get("http_status") in {403,429} for x in current_probes)
    missing_probes = [] if challenged else probe_all(missing_candidates, workers, deadline=probe_deadline)
    challenged = challenged or any(x.get("http_status") in {403,429} for x in missing_probes)
    alternate_missing_probes = [] if challenged else probe_all(
        [alternate_host(item) for item in missing_candidates], workers, deadline=probe_deadline
    )
    challenged = challenged or any(x.get("http_status") in {403,429} for x in alternate_missing_probes)
    missing_prefixes = {item.url.rsplit("/", 1)[0] for item in missing_candidates}
    controls_by_prefix: dict[str, Candidate] = {}
    for package in catalog["packages"]:
        if package.get("artifact_status") != "preserved" or not package.get("source_url"):
            continue
        candidate = package_candidate(package)
        prefix = candidate.url.rsplit("/", 1)[0]
        if prefix in missing_prefixes and prefix not in controls_by_prefix:
            controls_by_prefix[prefix] = candidate
    control_probes = [] if challenged else probe_all(list(controls_by_prefix.values()), workers, deadline=probe_deadline)
    release_notes = fetch_bytes(RELEASE_NOTES_URL)
    release_note_versions = set(VERSION_PATTERN.findall(release_notes.decode("utf-8", errors="replace")))
    summary = summarize_changes(
        catalog,
        manifests,
        current_probes,
        missing_probes,
        alternate_missing_probes,
        release_note_versions,
        control_probes,
    )
    return {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "scope": "current public feeds with historical discovery enabled" if include_history else "current public feeds and their package siblings only",
        "discovery_graph": graph,
        "modern_error": (
            str(modern_error) if modern_response is None and modern_error else None
        ),
        "include_history": include_history,
        "request_profile": UpdateProfile().redacted(),
        "ups": {
            **response,
            "bytes": len(ups),
            "sha256": hashlib.sha256(ups).hexdigest(),
            "manifest_urls": ups_manifest_urls,
        },
        "modern_manifest": modern_response,
        "manifests": manifests,
        "current_candidate_total": len(current_probes),
        "current_available_total": sum(bool(item.get("available")) for item in current_probes),
        "known_missing_checked_total": len(missing_probes),
        "control_total": len(control_probes),
        "available_control_total": sum(bool(item.get("available")) for item in control_probes),
        "release_notes": {
            "url": RELEASE_NOTES_URL,
            "bytes": len(release_notes),
            "sha256": hashlib.sha256(release_notes).hexdigest(),
            "versions": sorted(release_note_versions),
        },
        "current_candidates": current_probes,
        "known_missing_candidates": missing_probes,
        "alternate_host_candidates": alternate_missing_probes,
        "controls": control_probes,
        **summary,
        "discovery_incomplete": challenged or graph["blocked"] or graph["limits_reached"] or any(f["kind"] != "not-found" for f in graph["failures"]) or any(any("unrecognized" in w or "needs manifest source" in w for w in m["metadata"].get("warnings", [])) for m in manifests),
    }


def load_catalog(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))
