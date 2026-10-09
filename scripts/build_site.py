#!/usr/bin/env python3
"""Build the normalized static dataset consumed by the archive web viewer."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
REPOSITORY = "BookCatKid/sonos-firmware-archive"
RELEASE_BASE = f"https://github.com/{REPOSITORY}/releases"


def load(path: Path, default: Any = None) -> Any:
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8"))


def release_url(tag: str | None, asset: str | None = None) -> str | None:
    if not tag:
        return None
    if not asset:
        return f"{RELEASE_BASE}/tag/{tag}"
    from urllib.parse import quote
    return f"{RELEASE_BASE}/download/{quote(tag, safe='')}/{quote(asset, safe='')}"


def date_from_wayback(value: str | None) -> str | None:
    if not value or len(value) < 8:
        return None
    return f"{value[:4]}-{value[4:6]}-{value[6:8]}"


def simple_status(value: str | None) -> str:
    value = (value or "").lower()
    if value in {"complete", "recovered"}:
        return value
    if value.startswith("preserved"):
        return "preserved"
    if value.startswith("missing") or value in {"unavailable", "unrecoverable"}:
        return "missing"
    if value in {"partial", "blocked", "metadata-only", "observed"}:
        return value
    return value or "unknown"


def version_key(value: str | None) -> tuple:
    if not value:
        return ()
    parts = re.findall(r"\d+|[A-Za-z]+", value)
    result = []
    for part in parts:
        result.append((0, int(part)) if part.isdigit() else (1, part.lower()))
    return tuple(result)


def json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, sort_keys=True, default=str))


def build_payload(root: Path = ROOT) -> dict[str, Any]:
    data = root / "data"
    catalog = load(data / "catalog.json", {})
    completeness = load(data / "completeness.json", {})
    key_ledger = load(data / "key-recovery-ledger.json", {})
    desktop = load(data / "apps/desktop-archive.json", {})
    mobile = load(data / "apps/mobile-archive.json", {})
    android = load(data / "apps/android-store-archive.json", {})
    apple = load(data / "apps/apple-app-store.json", {})
    apple_history = load(data / "apps/apple-app-store-history.json", {})
    google_play = load(data / "apps/google-play-listings.json", {})
    web = load(data / "apps/web-app-archive.json", {})
    gpl = load(data / "gpl/wayback-catalog.json", {})

    # Structural firmware metadata is loaded before package normalization so every
    # firmware row can expose preservation, crypto, extraction, and filesystem state.
    firmware_sections = {}
    for path in sorted((data / "upd").glob("*.json")):
        entry = load(path, {})
        firmware_sections[entry.get("package_id") or path.stem] = entry
    filesystem_manifests = {
        path.stem: load(path, {})
        for path in sorted((data / "filesystems").glob("*.json"))
    }
    raw_receipts = {
        path.stem: load(path, {})
        for path in sorted((data / "raw").glob("*.json"))
    }
    precomputed_diffs = {
        path.stem: load(path, {})
        for path in sorted((data / "diffs").glob("*.json"))
    }
    recovered_recipients = set(key_ledger.get("recovered_keys", {}))

    records: list[dict[str, Any]] = []
    record_ids: set[str] = set()
    version_dates: dict[str, str] = {}
    details: dict[str, Any] = {}
    children: dict[str, list[str]] = defaultdict(list)

    for evidence in catalog.get("evidence", []):
        version = evidence.get("version")
        observed = evidence.get("observed")
        if version and observed:
            if version not in version_dates or observed > version_dates[version]:
                version_dates[version] = observed

    def add(record: dict[str, Any], detail: Any | None = None) -> str:
        base = record["id"]
        candidate = base
        index = 2
        while candidate in record_ids:
            candidate = f"{base}:{index}"
            index += 1
        record["id"] = candidate
        record_ids.add(candidate)
        record.setdefault("category", "other")
        record.setdefault("kind", record["category"])
        record.setdefault("platform", "other")
        record.setdefault("family", "")
        record.setdefault("version", "")
        record.setdefault("model", "")
        record.setdefault("product", "")
        record.setdefault("status", "unknown")
        record["status"] = simple_status(record["status"])
        record.setdefault("title", candidate)
        record.setdefault("subtitle", "")
        record.setdefault("date", None)
        record.setdefault("bytes", None)
        record.setdefault("sha256", None)
        record.setdefault("release_url", None)
        record.setdefault("source_urls", [])
        record.setdefault("sources", [])
        record.setdefault("parent_id", None)
        record.setdefault("tags", [])
        record.setdefault("note", "")
        record["source_urls"] = [u for u in record["source_urls"] if u]
        record["sources"] = sorted({str(s) for s in record["sources"] if s})
        record["tags"] = sorted({str(s) for s in record["tags"] if s})
        record["search"] = " ".join(
            str(record.get(k) or "") for k in
            ("id", "category", "kind", "platform", "family", "version", "model",
             "product", "status", "title", "subtitle", "sha256", "note")
        ).lower()
        records.append(record)
        if record["parent_id"]:
            children[record["parent_id"]].append(candidate)
        if detail is not None:
            details[candidate] = json_safe(detail)
        return candidate

    def firmware_analysis(pkg: dict[str, Any]) -> dict[str, Any]:
        """Return explicit preservation/decryption state for one firmware candidate."""
        pid = pkg.get("id") or pkg.get("filename")
        artifact_status = pkg.get("artifact_status")
        raw_status = pkg.get("raw_status")
        manifest = firmware_sections.get(pid, {})
        sections = manifest.get("sections", [])
        encrypted_sections = [section for section in sections if section.get("encrypted")]
        recipients = {
            section.get("recipient_id")
            for section in encrypted_sections
            if section.get("recipient_id")
        }

        if artifact_status == "preserved":
            availability = "preserved"
            status = "preserved"
        elif artifact_status == "missing-exact-package":
            availability = "exact-missing"
            status = "missing"
        elif artifact_status == "missing-cdn":
            # These are negative directory/model probes, not proven archive holes.
            availability = "negative-probe"
            status = "unavailable-probe"
        else:
            availability = artifact_status or "unknown"
            status = artifact_status or "unknown"

        if pkg.get("artifact_type") != "upd":
            decryption_state = "not-applicable"
        elif availability != "preserved":
            decryption_state = "not-preserved"
        elif encrypted_sections and raw_status == "complete":
            decryption_state = "decrypted"
        elif not encrypted_sections and raw_status == "complete":
            decryption_state = "plaintext-extracted"
        elif raw_status == "blocked-model-key":
            decryption_state = "blocked-model-key"
        elif encrypted_sections and recipients and recipients.issubset(recovered_recipients):
            decryption_state = "decryptable"
        elif encrypted_sections:
            decryption_state = "encrypted"
        elif sections:
            decryption_state = "plaintext-unextracted"
        else:
            decryption_state = "unknown"

        return {
            "availability": availability,
            "decryption_state": decryption_state,
            "decrypted": decryption_state == "decrypted",
            "source_encrypted": bool(encrypted_sections),
            "recipient_ids": sorted(recipients),
            "components_extracted": raw_status == "complete" or pid in raw_receipts,
            "filesystem_indexed": pid in filesystem_manifests,
            "status": status,
        }

    # Firmware package artifacts.
    for pkg in catalog.get("packages", []):
        pid = pkg.get("id") or pkg.get("filename")
        analysis = firmware_analysis(pkg)
        tag = pkg.get("release_tag")
        asset = pkg.get("release_asset") or (pkg.get("filename") if tag else None)
        add({
            "id": f"firmware:{pid}",
            "category": "firmware",
            "kind": "firmware-package",
            "platform": "speaker",
            "family": "firmware",
            "version": pkg.get("version", ""),
            "model": str(pkg.get("package_model", "")),
            "product": pkg.get("product") or "",
            "status": analysis["status"],
            "title": pkg.get("filename") or str(pid),
            "subtitle": pkg.get("product") or f"package model {pkg.get('package_model')}",
            "date": version_dates.get(pkg.get("version")),
            "bytes": pkg.get("bytes"),
            "sha256": pkg.get("sha256"),
            "release_url": release_url(tag, asset),
            "source_urls": [pkg.get("source_url")],
            "sources": [pkg.get("provenance"), pkg.get("source_manifest")],
            "tags": [
                pkg.get("raw_status"),
                pkg.get("model_number"),
                analysis["availability"],
                analysis["decryption_state"],
                "filesystem-indexed" if analysis["filesystem_indexed"] else "",
            ],
            "note": pkg.get("note") or "",
            "artifact_status": pkg.get("artifact_status"),
            "raw_status": pkg.get("raw_status"),
            "availability": analysis["availability"],
            "decryption_state": analysis["decryption_state"],
            "decrypted": analysis["decrypted"],
            "source_encrypted": analysis["source_encrypted"],
            "recipient_ids": analysis["recipient_ids"],
            "components_extracted": analysis["components_extracted"],
            "filesystem_indexed": analysis["filesystem_indexed"],
        }, {**pkg, **analysis})

    # Signed/update manifests.
    for manifest in catalog.get("source_manifests", []):
        key = manifest.get("sha256") or manifest.get("filename") or manifest.get("version")
        add({
            "id": f"manifest:{key}",
            "category": "firmware",
            "kind": "update-manifest",
            "platform": "speaker",
            "family": "firmware",
            "version": manifest.get("version", ""),
            "status": "preserved",
            "title": manifest.get("filename") or f"{manifest.get('version')} manifest",
            "subtitle": manifest.get("revision") or "",
            "date": version_dates.get(manifest.get("version")),
            "bytes": manifest.get("bytes"),
            "sha256": manifest.get("sha256"),
            "release_url": release_url(manifest.get("release_tag"), manifest.get("release_asset")),
            "source_urls": [manifest.get("source_url")],
            "sources": [manifest.get("provenance")],
            "tags": ["signed-manifest"],
        }, manifest)

    # Extracted/raw firmware components.
    for raw in catalog.get("raw_images", []):
        key = raw.get("sha256") or raw.get("filename")
        add({
            "id": f"raw:{raw.get('version')}:{raw.get('package_model')}:{raw.get('kind')}:{key}",
            "category": "firmware",
            "kind": "raw-component",
            "platform": "speaker",
            "family": "firmware",
            "version": raw.get("version", ""),
            "model": str(raw.get("package_model", "")),
            "status": "preserved",
            "title": raw.get("filename") or raw.get("kind") or "raw component",
            "subtitle": raw.get("kind") or "",
            "date": version_dates.get(raw.get("version")),
            "bytes": raw.get("bytes"),
            "sha256": raw.get("sha256"),
            "release_url": release_url(raw.get("release_tag"), raw.get("release_asset")),
            "sources": [raw.get("provenance"), raw.get("artifact_class")],
            "tags": [raw.get("kind"), raw.get("artifact_class")],
        }, raw)

    # Evidence records are first-class so source provenance is browsable.
    for evidence in catalog.get("evidence", []):
        add({
            "id": f"evidence:{evidence.get('id') or evidence.get('version')}",
            "category": "evidence",
            "kind": evidence.get("kind") or "evidence",
            "platform": "speaker",
            "family": "firmware",
            "version": evidence.get("version", ""),
            "status": "observed",
            "title": evidence.get("id") or f"{evidence.get('version')} evidence",
            "subtitle": evidence.get("source_type") or "",
            "date": evidence.get("observed"),
            "source_urls": [evidence.get("source_url"), evidence.get("manifest_url"), evidence.get("update_url")],
            "sources": [evidence.get("source_type")],
            "tags": ["redacted" if evidence.get("redacted") else ""],
        }, evidence)

    # Coverage targets.
    for target in completeness.get("targets", []):
        status = target.get("status") or "partial"
        if status != "complete":
            status = "missing" if target.get("package") == "missing" else "partial"
        add({
            "id": f"target:{target.get('product')}:{target.get('target_version')}",
            "category": "coverage",
            "kind": "coverage-target",
            "platform": "speaker",
            "family": "firmware",
            "version": target.get("target_version", ""),
            "model": str(target.get("package_model", "")),
            "product": target.get("product") or "",
            "status": status,
            "title": f"{target.get('product')} target {target.get('target_version')}",
            "subtitle": target.get("model_number") or "",
            "tags": [target.get("package"), target.get("kernel"), target.get("rootfs")],
        }, target)

    # Desktop applications.
    for artifact in desktop.get("artifacts", []):
        add({
            "id": f"desktop:{artifact.get('platform')}:{artifact.get('family')}:{artifact.get('version')}:{(artifact.get('sha256') or '')[:12]}",
            "category": "apps",
            "kind": "desktop-installer",
            "platform": artifact.get("platform") or "desktop",
            "family": artifact.get("family") or "",
            "version": artifact.get("version", ""),
            "status": "preserved",
            "title": artifact.get("original_filename") or artifact.get("release_asset"),
            "subtitle": f"{artifact.get('family', '')} {artifact.get('platform', '')}".strip(),
            "date": date_from_wayback(artifact.get("wayback_timestamp")),
            "bytes": artifact.get("bytes"),
            "sha256": artifact.get("sha256"),
            "release_url": artifact.get("release_url") or release_url(artifact.get("release_tag"), artifact.get("release_asset")),
            "source_urls": artifact.get("source_urls", []) + [artifact.get("fetched_from")],
            "sources": artifact.get("sources", []),
            "tags": [artifact.get("wayback_timestamp") and "wayback"],
        }, artifact)

    # Official Sonos-hosted Android/FireOS APKs.
    for artifact in mobile.get("artifacts", []):
        add({
            "id": f"mobile:{artifact.get('family')}:{artifact.get('version')}:{(artifact.get('sha256') or '')[:12]}",
            "category": "apps",
            "kind": "official-apk",
            "platform": artifact.get("platform") or "android",
            "family": artifact.get("family") or "",
            "version": artifact.get("version", ""),
            "status": "preserved",
            "title": artifact.get("original_filename") or artifact.get("release_asset"),
            "subtitle": artifact.get("platform") or "",
            "date": date_from_wayback(artifact.get("wayback_timestamp")),
            "bytes": artifact.get("bytes"),
            "sha256": artifact.get("sha256"),
            "release_url": artifact.get("release_url") or release_url(artifact.get("release_tag"), artifact.get("release_asset")),
            "source_urls": artifact.get("source_urls", []) + [artifact.get("fetched_from")],
            "sources": artifact.get("sources", []),
            "tags": [artifact.get("wayback_timestamp") and "wayback"],
        }, artifact)

    for gap in mobile.get("gaps", []):
        add({
            "id": f"mobile-gap:{gap.get('family')}:{gap.get('version')}:{gap.get('wayback_timestamp')}",
            "category": "apps",
            "kind": "official-apk",
            "platform": "android",
            "family": gap.get("family") or "",
            "version": gap.get("version", ""),
            "status": "missing",
            "title": (gap.get("url") or "missing APK").split("/")[-1],
            "subtitle": "known invalid/unrecoverable capture",
            "date": date_from_wayback(gap.get("wayback_timestamp")),
            "source_urls": [gap.get("url")],
            "sources": gap.get("sources", []),
            "note": gap.get("reason") or "",
            "tags": ["gap", "wayback"],
        }, gap)

    # Android store acquisitions: one parent delivery plus individually browsable components.
    for artifact in android.get("artifacts", []):
        wrapper = artifact.get("complete_wrapper") or {}
        components = artifact.get("components", [])
        verified = bool(components) and all(c.get("verified", False) for c in components)
        parent_id = add({
            "id": f"android-store:{artifact.get('package')}:{artifact.get('version_code')}:{artifact.get('source_kind')}:{artifact.get('source_version_selector')}",
            "category": "apps",
            "kind": "android-store-delivery",
            "platform": "android",
            "family": artifact.get("family") or "",
            "version": artifact.get("version_name") or artifact.get("version_code") or "",
            "status": "preserved" if (wrapper or components) and verified else "partial",
            "title": f"{artifact.get('package')} {artifact.get('version_name') or artifact.get('version_code')}",
            "subtitle": artifact.get("source_kind") or "",
            "date": artifact.get("acquired_at"),
            "bytes": wrapper.get("bytes"),
            "sha256": wrapper.get("sha256"),
            "release_url": wrapper.get("release_url"),
            "source_urls": [artifact.get("source_url")],
            "sources": [artifact.get("source_kind"), (artifact.get("acquisition_tool") or {}).get("name")],
            "tags": [artifact.get("delivery_profile"), f"{len(components)} components"],
        }, artifact)
        for component in components:
            add({
                "id": f"android-component:{artifact.get('package')}:{artifact.get('version_code')}:{component.get('name')}:{(component.get('sha256') or '')[:12]}",
                "category": "apps",
                "kind": "android-component",
                "platform": "android",
                "family": artifact.get("family") or "",
                "version": artifact.get("version_name") or artifact.get("version_code") or "",
                "status": "preserved" if component.get("verified") else "partial",
                "title": component.get("name") or "APK component",
                "subtitle": artifact.get("package") or "",
                "date": artifact.get("acquired_at"),
                "bytes": component.get("bytes"),
                "sha256": component.get("sha256"),
                "release_url": component.get("release_url"),
                "source_urls": [artifact.get("source_url")],
                "sources": [artifact.get("source_kind")],
                "tags": component.get("schemes", []) + [component.get("signer_sha256")],
                "parent_id": parent_id,
            }, component)

    # Apple metadata history. Keep one record per distinct bundle/version snapshot.
    apple_snapshots = list(apple_history.get("snapshots", []))
    if apple.get("listings"):
        apple_snapshots.append(apple)
    seen_apple: set[tuple] = set()
    current_apple = {(x.get("bundle_id"), x.get("version")) for x in apple.get("listings", [])}
    for snapshot in apple_snapshots:
        for listing in snapshot.get("listings", []):
            key = (listing.get("bundle_id"), listing.get("version"), listing.get("current_version_release_date"))
            if key in seen_apple:
                continue
            seen_apple.add(key)
            is_current = (listing.get("bundle_id"), listing.get("version")) in current_apple
            add({
                "id": f"ios:{listing.get('bundle_id')}:{listing.get('version')}:{listing.get('current_version_release_date')}",
                "category": "apps",
                "kind": "ios-app-metadata",
                "platform": "ios",
                "family": listing.get("family") or "",
                "version": listing.get("version") or "",
                "status": "metadata-only",
                "title": f"{listing.get('name')} {listing.get('version')}",
                "subtitle": listing.get("bundle_id") or "",
                "date": listing.get("current_version_release_date"),
                "bytes": listing.get("file_size_bytes"),
                "source_urls": [listing.get("track_view_url"), snapshot.get("lookup_url")],
                "sources": [snapshot.get("scope")],
                "tags": [f"iOS {listing.get('minimum_os_version')}+", "current" if is_current else "historical"],
                "note": "App Store metadata is preserved; the IPA binary is not present in the public archive.",
                "is_current": is_current,
            }, {"snapshot_checked": snapshot.get("checked"), **listing})

    # Google Play listing metadata.
    for listing in google_play.get("listings", []):
        add({
            "id": f"google-play:{listing.get('package')}:{listing.get('updated_on')}:{(listing.get('page_sha256') or '')[:12]}",
            "category": "evidence",
            "kind": "google-play-listing",
            "platform": "android",
            "family": listing.get("family") or "",
            "version": listing.get("updated_on") or "",
            "status": "observed",
            "title": listing.get("package") or "Google Play listing",
            "subtitle": f"updated {listing.get('updated_on')}",
            "date": listing.get("updated_on"),
            "sha256": listing.get("page_sha256"),
            "source_urls": [listing.get("url")],
            "sources": [google_play.get("scope")],
        }, listing)

    # Browser deployments and every captured/missing resource.
    for deployment in web.get("deployments", []):
        dep_id = add({
            "id": f"web:{deployment.get('app')}:{deployment.get('capture_id')}",
            "category": "web",
            "kind": "web-deployment",
            "platform": "web",
            "family": deployment.get("app") or "",
            "version": deployment.get("build_id") or deployment.get("capture_id") or "",
            "status": "preserved" if deployment.get("gap_total", 0) == 0 else "partial",
            "title": f"{deployment.get('app')} deployment {deployment.get('capture_id')}",
            "subtitle": f"{deployment.get('captured_total', 0)}/{deployment.get('resource_total', 0)} resources captured",
            "date": deployment.get("captured_at"),
            "bytes": sum(r.get("bytes") or 0 for r in deployment.get("resources", [])),
            "sha256": deployment.get("deployment_fingerprint") or deployment.get("probe_fingerprint"),
            "release_url": release_url(deployment.get("release_tag")),
            "source_urls": [deployment.get("entry_url")],
            "sources": ["public-web-capture"],
            "tags": [deployment.get("build_id"), f"{deployment.get('gap_total', 0)} gaps"],
            "note": deployment.get("note") or "",
        }, deployment)
        for resource in deployment.get("resources", []):
            outcome = resource.get("outcome")
            add({
                "id": f"web-resource:{deployment.get('app')}:{deployment.get('capture_id')}:{resource.get('requested_url')}:{(resource.get('sha256') or '')[:12]}",
                "category": "web",
                "kind": "web-resource",
                "platform": "web",
                "family": deployment.get("app") or "",
                "version": deployment.get("build_id") or deployment.get("capture_id") or "",
                "status": "preserved" if outcome == "captured" else "missing",
                "title": resource.get("file") or resource.get("requested_url") or "web resource",
                "subtitle": resource.get("requested_url") or "",
                "date": deployment.get("captured_at"),
                "bytes": resource.get("bytes"),
                "sha256": resource.get("sha256"),
                "release_url": resource.get("release_url"),
                "source_urls": [resource.get("requested_url"), resource.get("final_url")],
                "sources": ["public-web-capture"],
                "tags": [outcome, str(resource.get("status") or "")],
                "parent_id": dep_id,
            }, resource)

    # Key-recovery coverage (never private key material).
    recovered_keys = key_ledger.get("recovered_keys", {})
    for recipient, meta in recovered_keys.items():
        add({
            "id": f"key:{recipient}",
            "category": "keys",
            "kind": "recipient-key-status",
            "platform": "speaker",
            "family": "firmware",
            "status": "recovered",
            "title": f"Recipient {recipient[:12]}…",
            "subtitle": "key recovered",
            "sha256": None,
            "sources": [meta.get("source") if isinstance(meta, dict) else "recovery-ledger"],
            "tags": ["private key excluded from repository"],
            "note": "The ledger records coverage only. Private key material is deliberately not published.",
        }, {"recipient_id": recipient, "metadata": meta})
    for item in key_ledger.get("missing_keys", []):
        recipient = item.get("recipient_id")
        packages = item.get("packages", [])
        add({
            "id": f"key-missing:{recipient}",
            "category": "keys",
            "kind": "recipient-key-status",
            "platform": "speaker",
            "family": "firmware",
            "status": "blocked",
            "title": f"Recipient {str(recipient)[:12]}…",
            "subtitle": f"{len(packages)} package(s) blocked",
            "sources": ["key-recovery-ledger"],
            "tags": [f"{len(packages)} packages"],
            "note": "Encrypted payload extraction is blocked until the matching model-specific key is recovered.",
        }, item)

    # GPL/LGPL source archives.
    for release in gpl.get("releases", []):
        rel = release.get("release") or ""
        idx = release.get("index") or {}
        add({
            "id": f"gpl-index:{rel}:{idx.get('sha256')}",
            "category": "source",
            "kind": "gpl-index",
            "platform": "source",
            "family": "gpl",
            "version": rel,
            "status": "preserved",
            "title": f"GPL source index {rel}",
            "subtitle": f"{len(release.get('artifacts', []))} archived source item(s)",
            "date": date_from_wayback(idx.get("wayback_timestamp")),
            "bytes": idx.get("bytes"),
            "sha256": idx.get("sha256"),
            "release_url": release_url(f"gpl-{rel}"),
            "source_urls": [idx.get("original_url"), idx.get("wayback_url")],
            "sources": ["sonos-gpl", "wayback"],
            "tags": ["open-source"],
        }, idx)
        for artifact in release.get("artifacts", []):
            add({
                "id": f"gpl:{rel}:{artifact.get('filename')}:{artifact.get('sha256') or artifact.get('cdx_sha1')}",
                "category": "source",
                "kind": "gpl-artifact",
                "platform": "source",
                "family": "gpl",
                "version": rel,
                "status": "preserved" if artifact.get("release_asset") or artifact.get("sha256") else "observed",
                "title": artifact.get("filename") or "GPL artifact",
                "subtitle": artifact.get("kind") or "",
                "date": date_from_wayback(artifact.get("wayback_timestamp")),
                "bytes": artifact.get("bytes") or artifact.get("cdx_length"),
                "sha256": artifact.get("sha256"),
                "release_url": artifact.get("release_url") or release_url(artifact.get("release_tag"), artifact.get("release_asset")),
                "source_urls": [artifact.get("original_url"), artifact.get("wayback_url")],
                "sources": ["sonos-gpl", "wayback"],
                "tags": [artifact.get("kind"), artifact.get("cdx_mimetype")],
            }, artifact)

    # Research/audit receipts are browsable without embedding their entire (sometimes
    # multi-megabyte) bodies into the static payload. The committed file remains the
    # canonical full record and is linked directly from the detail view.
    def receipt_summary(path: Path) -> dict[str, Any]:
        value = load(path, {})
        if not isinstance(value, dict):
            return {"type": type(value).__name__}
        counts = {
            key: len(item)
            for key, item in value.items()
            if isinstance(item, (list, dict))
        }
        scalars = {
            key: item
            for key, item in value.items()
            if isinstance(item, (str, int, float, bool)) or item is None
        }
        return {
            "top_level_keys": list(value),
            "counts": counts,
            "scalars": scalars,
        }

    def committed_file_record(path: Path, kind: str, title_prefix: str) -> None:
        relative = path.relative_to(root).as_posix()
        date_match = re.search(r"(20\d{2}-\d{2}-\d{2})", path.name)
        summary = receipt_summary(path) if path.suffix == ".json" else {"path": relative}
        add({
            "id": f"research:{relative}",
            "category": "research",
            "kind": kind,
            "platform": "metadata",
            "family": "research",
            "status": "observed",
            "title": f"{title_prefix}: {path.name}",
            "subtitle": relative,
            "date": date_match.group(1) if date_match else None,
            "bytes": path.stat().st_size,
            "source_urls": [f"https://github.com/{REPOSITORY}/blob/main/{relative}"],
            "sources": ["committed-research-record"],
            "tags": list(summary.get("top_level_keys", []))[:12],
            "note": "The full committed file is the canonical receipt; this viewer stores only a compact index summary.",
        }, summary)

    for path in sorted((data / "discovery").glob("*.json")):
        committed_file_record(path, "discovery-receipt", "Discovery")
    for path in sorted((data / "decryption-runs").glob("*.json")):
        committed_file_record(path, "decryption-receipt", "Decryption / extraction run")
    for path in sorted((data / "diffs").glob("*.json")):
        committed_file_record(path, "precomputed-diff", "Precomputed diff")
    for path in sorted((root / "docs").glob("*.md")):
        committed_file_record(path, "research-report", "Research report")

    # Structural manifests loaded above are also exposed to the compare UI.

    # Attach child IDs only after all records have been created.
    for record in records:
        record["children"] = children.get(record["id"], [])

    # Firmware matrix. Negative CDN/model probes remain queryable but are not
    # treated as coverage holes or shown by default.
    package_records = [r for r in records if r["kind"] == "firmware-package"]
    matrix_relevant = [
        r for r in package_records
        if r.get("availability") != "negative-probe"
    ]
    matrix_versions = sorted(
        {r["version"] for r in matrix_relevant if r["version"]},
        key=version_key,
        reverse=True,
    )
    matrix_models = sorted(
        {int(r["model"]) for r in matrix_relevant if str(r["model"]).isdigit()}
    )
    matrix_all_versions = sorted(
        {r["version"] for r in package_records if r["version"]},
        key=version_key,
        reverse=True,
    )
    matrix_all_models = sorted(
        {int(r["model"]) for r in package_records if str(r["model"]).isdigit()}
    )
    matrix_cells: dict[str, dict[str, dict[str, Any]]] = defaultdict(dict)
    for r in package_records:
        if not str(r["model"]).isdigit():
            continue
        matrix_cells[r["version"]][r["model"]] = {
            "id": r["id"],
            "status": r["status"],
            "artifact_status": r.get("artifact_status"),
            "availability": r.get("availability"),
            "raw_status": r.get("raw_status"),
            "decryption_state": r.get("decryption_state"),
            "decrypted": r.get("decrypted"),
            "source_encrypted": r.get("source_encrypted"),
            "components_extracted": r.get("components_extracted"),
            "filesystem_indexed": r.get("filesystem_indexed"),
            "bytes": r.get("bytes"),
        }
    firmware_availability = Counter(
        r.get("availability") or "unknown" for r in package_records
    )
    firmware_crypto = Counter(
        r.get("decryption_state") or "unknown" for r in package_records
        if r.get("availability") == "preserved"
    )

    # Source/provenance rollup.
    source_counts: dict[str, dict[str, Any]] = {}
    for record in records:
        for source in record.get("sources", []):
            if not source:
                continue
            bucket = source_counts.setdefault(source, {"total": 0, "statuses": Counter(), "categories": Counter()})
            bucket["total"] += 1
            bucket["statuses"][record["status"]] += 1
            bucket["categories"][record["category"]] += 1
    sources = [{
        "name": name,
        "total": info["total"],
        "statuses": dict(info["statuses"]),
        "categories": dict(info["categories"]),
    } for name, info in source_counts.items()]
    sources.sort(key=lambda x: (-x["total"], x["name"]))

    # Deduplicated preserved byte count.
    unique_blobs: dict[str, int] = {}
    for r in records:
        if r["status"] not in {"preserved", "complete", "recovered"}:
            continue
        sha = r.get("sha256")
        size = r.get("bytes")
        if sha and isinstance(size, int):
            unique_blobs.setdefault(sha, size)

    statuses = Counter(r["status"] for r in records)
    categories = Counter(r["category"] for r in records)
    platforms = Counter(r["platform"] for r in records)
    kinds = Counter(r["kind"] for r in records)

    gap_statuses = {"missing", "partial", "metadata-only", "blocked"}
    gaps = [r["id"] for r in records if r["status"] in gap_statuses]

    timeline = [
        {"id": r["id"], "date": r["date"], "title": r["title"], "category": r["category"],
         "kind": r["kind"], "status": r["status"], "version": r["version"], "platform": r["platform"]}
        for r in records if r.get("date")
    ]
    timeline.sort(key=lambda x: (x["date"] or "", x["title"]), reverse=True)

    payload = {
        "schema_version": 2,
        "repository": REPOSITORY,
        "generated_from": {
            "catalog_generated": catalog.get("generated"),
            "completeness_updated": completeness.get("updated"),
            "desktop_updated": desktop.get("updated"),
            "mobile_updated": mobile.get("updated"),
            "android_updated": android.get("updated"),
            "apple_checked": apple.get("checked"),
            "google_play_checked": google_play.get("checked"),
            "web_updated": web.get("updated"),
        },
        "summary": {
            "record_total": len(records),
            "statuses": dict(statuses),
            "categories": dict(categories),
            "platforms": dict(platforms),
            "kinds": dict(kinds),
            "gap_total": len(gaps),
            "unique_preserved_blobs": len(unique_blobs),
            "unique_preserved_bytes": sum(unique_blobs.values()),
            "firmware_versions": len(matrix_versions),
            "firmware_models": len(matrix_models),
            "firmware_preserved_packages": firmware_availability.get("preserved", 0),
            "firmware_exact_gaps": firmware_availability.get("exact-missing", 0),
            "firmware_negative_probes": firmware_availability.get("negative-probe", 0),
            "firmware_decrypted_packages": firmware_crypto.get("decrypted", 0),
            "firmware_plaintext_extracted_packages": firmware_crypto.get("plaintext-extracted", 0),
            "firmware_decryptable_packages": firmware_crypto.get("decryptable", 0),
            "firmware_encrypted_packages": (
                firmware_crypto.get("encrypted", 0)
                + firmware_crypto.get("blocked-model-key", 0)
            ),
            "firmware_filesystem_indexed_packages": sum(
                1 for r in package_records if r.get("filesystem_indexed")
            ),
            "filesystem_manifests": len(filesystem_manifests),
            "firmware_section_manifests": len(firmware_sections),
        },
        "records": records,
        "details": details,
        "gaps": gaps,
        "timeline": timeline,
        "sources": sources,
        "firmware_matrix": {
            "versions": matrix_versions,
            "models": matrix_models,
            "all_versions": matrix_all_versions,
            "all_models": matrix_all_models,
            "availability_counts": dict(firmware_availability),
            "crypto_counts": dict(firmware_crypto),
            "cells": matrix_cells,
        },
        "compare": {
            "firmware_sections": firmware_sections,
            "filesystems": filesystem_manifests,
            "raw_receipts": raw_receipts,
            "precomputed": precomputed_diffs,
        },
    }
    return json_safe(payload)


def write_payload(payload: dict[str, Any], output: Path, as_javascript: bool) -> None:
    output.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    if as_javascript:
        text = "window.SONOS_ARCHIVE_DATA=" + text + ";\n"
    else:
        text += "\n"
    output.write_text(text, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "site/archive-data.js")
    parser.add_argument("--json", action="store_true", help="write raw JSON instead of a JS assignment")
    parser.add_argument("--check", action="store_true", help="build in memory and validate the normalized payload")
    args = parser.parse_args()

    payload = build_payload()
    ids = [r["id"] for r in payload["records"]]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate normalized record IDs")
    if not payload["records"]:
        raise SystemExit("normalized site payload contains no records")
    for gap_id in payload["gaps"]:
        if gap_id not in set(ids):
            raise SystemExit(f"gap references unknown record: {gap_id}")

    if args.check:
        print(
            f"site payload ok: records={len(ids)} gaps={len(payload['gaps'])} "
            f"firmware_versions={payload['summary']['firmware_versions']}"
        )
        return
    write_payload(payload, args.output, not args.json)
    print(f"wrote {args.output} ({len(ids)} records)")


if __name__ == "__main__":
    main()
