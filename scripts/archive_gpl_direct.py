#!/usr/bin/env python3
"""Discover and preserve GPL sources from Sonos's original download server."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from sonos_firmware.gpl_binary_evidence import scan_binary
from sonos_firmware.gpl_archive import (
    HttpResult, atomic_json, candidates, capture_response, download_artifact,
    get_page, index_url, load_json, offline_candidates, pause, sha256_bytes,
    source_url, timestamp, valid_version, verify_artifact, file_sha256,
)

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "data/gpl/live"
LEGACY = ROOT / "data/gpl/wayback-catalog.json"
SEED = ROOT / "data/gpl/seed-versions.json"


def seed_versions() -> set[str]:
    seed = load_json(SEED, {"versions": []})
    versions = set(seed["versions"])
    if LEGACY.exists():
        versions.update(item["release"] for item in load_json(LEGACY, {"releases": []})["releases"])
    return {valid_version(item) for item in versions}


def live_catalog(output: Path) -> dict:
    catalog = load_json(output / "catalog.json", {"schema_version": 1, "releases": {}})
    if catalog.get("schema_version") != 1 or not isinstance(catalog.get("releases"), dict):
        raise ValueError("unsupported live GPL catalog format")
    return catalog


def save_index(version: str, response: HttpResult, output: Path) -> dict:
    record = capture_response(version, response, output)
    if record["state"] == "verified":
        catalog = live_catalog(output)
        catalog["releases"][version] = record
        catalog["updated"] = timestamp()
        atomic_json(output / "catalog.json", catalog)
    return record


def discover(args: argparse.Namespace) -> int:
    output = args.output
    catalog = live_catalog(output)
    known = seed_versions() | {k for k, v in catalog["releases"].items() if v["state"] == "verified"}
    evidence = offline_candidates(ROOT)
    sitemap_evidence = load_json(output / "site-evidence.json", {"version_evidence": {}})
    for version, sources in sitemap_evidence.get("version_evidence", {}).items():
        if valid_version(version) not in known:
            evidence.setdefault(version, set()).update(sources)
    binary_manifest = load_json(output / "binary-evidence.json", {"version_evidence": {}})
    for version, sources in binary_manifest.get("version_evidence", {}).items():
        if valid_version(version) not in known:
            evidence.setdefault(version, set()).update(sources)
    for binary in args.binary:
        report = scan_binary(binary)
        print(f"binary {report['filename']}: {report['entries_scanned']} decoded entries; "
              f"complete={report['complete']}; GPL versions={list(report['version_evidence'])}", flush=True)
        for version, locations in report["version_evidence"].items():
            for location in locations:
                evidence.setdefault(version, set()).add(
                    f"binary:{report['filename']}:{location.get('entry') or '<raw>'}:"
                    f"{location['offset']}:{location['format']}")
    todo = candidates(known, major_max=args.major_max, minor_max=args.minor_max, evidence=evidence)
    state = load_json(output / "discovery.json", {"schema_version": 1, "candidates": {}})
    checked = state["candidates"]
    total = 0
    for item in todo:
        version = item["version"]
        if not args.recheck and version in checked:
            continue
        if total >= args.limit:
            break
        try:
            response = get_page(index_url(version))
            record = save_index(version, response, output)
        except (OSError, ValueError) as error:
            record = {"version": version, "url": index_url(version), "checked_at": timestamp(),
                      "state": "network-error", "reason": str(error)}
        record["priority"] = item["priority"]
        record["evidence"] = item["evidence"]
        checked[version] = {key: value for key, value in record.items() if key != "links"}
        state["updated"] = timestamp()
        atomic_json(output / "discovery.json", state)
        total += 1
        print(f"{total}: {version} {record['state']} HTTP {record.get('http_status', 'n/a')}", flush=True)
        if record["state"] == "verified":
            print(f"NEW verified GPL release: {version} ({len(record['links'])} links)", flush=True)
        # An object-store AccessDenied response can indicate an absent key; don't
        # interpret it as positive absence or an Akamai ban. Stop on challenges.
        if record["state"] in {"rate-limited", "network-error"} or (
            record["state"] == "access-denied" and record.get("reason") != "AccessDenied XML"
        ):
            print("stopping sweep on access challenge or network failure", flush=True)
            break
        pause(args.delay)
    print(f"checked_this_run={total} verified_total={len([r for r in live_catalog(output)['releases'].values() if r['state']=='verified'])}")
    return 0


def indexes(args: argparse.Namespace) -> int:
    output = args.output
    versions = set(args.version)
    if args.all_known:
        versions |= seed_versions()
        versions |= {v for v, x in live_catalog(output)["releases"].items() if x["state"] == "verified"}
    if not versions:
        raise ValueError("specify --version or --all-known")
    source = args.cache_dir
    for i, version in enumerate(sorted(versions, key=lambda s: tuple(map(int, s.split('.'))))):
        valid_version(version)
        if source:
            path = source / f"sonos-gpl-20261112-{version}.html"
            if not path.exists():
                raise FileNotFoundError(f"missing directly captured Sonos index: {path}")
            response = HttpResult(200, path.read_bytes(), "text/html", index_url(version))
        else:
            response = get_page(index_url(version))
        record = save_index(version, response, output)
        print(f"{version}: {record['state']} ({len(record.get('links', []))} links)", flush=True)
        if not source and i + 1 < len(versions):
            pause(args.delay)
    return 0


def run_gh(*args: str, capture: bool = True) -> str:
    result = subprocess.run(["gh", *args], check=True, text=True,
                            stdout=subprocess.PIPE if capture else None, stderr=subprocess.PIPE if capture else None)
    return result.stdout if capture else ""


def publish(item: dict, blob: Path, repo: str) -> dict:
    """Upload without overwriting a preexisting (possibly Wayback) asset."""
    version = item["version"]
    tag = f"gpl-{version}"
    try:
        release = json.loads(run_gh("api", f"repos/{repo}/releases/tags/{tag}"))
    except subprocess.CalledProcessError as error:
        if "404" not in error.stderr and "Not Found" not in error.stderr:
            raise
        run_gh("release", "create", tag, "-R", repo, "--title", f"Sonos GPL/LGPL {version}",
               "--notes", "Sources downloaded directly from Sonos or their linked upstream distributors. Original licenses apply.")
        release = json.loads(run_gh("api", f"repos/{repo}/releases/tags/{tag}"))
    name = item["filename"]
    existing = next((asset for asset in release.get("assets", []) if asset["name"] == name), None)
    original_conflict = False
    if existing:
        remote_hash = (existing.get("digest") or "").removeprefix("sha256:")
        if existing["size"] != item["bytes"] or remote_hash != item["sha256"]:
            original_conflict = True
            name = f"direct-{item['sha256'][:12]}-{item['filename']}"
            existing = next((asset for asset in release.get("assets", []) if asset["name"] == name), None)
    if existing:
        remote_hash = (existing.get("digest") or "").removeprefix("sha256:")
        if existing["size"] != item["bytes"] or remote_hash != item["sha256"]:
            raise RuntimeError(f"existing asset digest conflict: {tag}/{name}")
        return {"state": "published", "release_url": existing["browser_download_url"],
                "release_asset": name, "original_name_conflict": original_conflict, "verified_existing": True}
    with tempfile.TemporaryDirectory(dir=blob.parent) as directory:
        link = Path(directory) / name
        os.link(blob, link)
        run_gh("release", "upload", tag, str(link), "-R", repo)
    updated = json.loads(run_gh("api", f"repos/{repo}/releases/tags/{tag}"))
    observed = next((a for a in updated["assets"] if a["name"] == name), None)
    if not observed or observed.get("size") != item["bytes"] or (observed.get("digest") or "").removeprefix("sha256:") != item["sha256"]:
        raise RuntimeError(f"GitHub reported mismatched upload for {tag}/{name}")
    return {"state": "published", "release_url": observed["browser_download_url"],
            "release_asset": name, "original_name_conflict": original_conflict, "verified_existing": False}


def archive(args: argparse.Namespace) -> int:
    catalog = live_catalog(args.output)
    records = load_json(args.output / "artifacts.json", {"schema_version": 1, "artifacts": {}})
    count = 0
    total_bytes = 0
    terminal_states = {"invalid-artifact", "unexpected-content-type", "unknown-extension", "external-or-unsupported"}
    def incomplete(item: dict | None) -> bool:
        if not item:
            return True
        if item.get("published"):
            return False
        if item.get("state") in terminal_states:
            return args.retry_errors
        if item.get("state") == "http-error" and item.get("http_status") in {403, 404}:
            return args.retry_errors
        return True
    versions = list(args.version)
    if args.next:
        if versions:
            raise ValueError("--next cannot be combined with --version")
        if not args.limit and not args.max_total_bytes:
            raise ValueError("--next requires --limit or --max-total-bytes to bound archive work")
        for candidate, entry in sorted(catalog["releases"].items(), key=lambda pair: tuple(map(int, pair[0].split('.')))):
            if entry["state"] != "verified":
                continue
            prior = records["artifacts"].get(candidate, {})
            pending = [link for link in entry["links"] if
                       link["kind"] in ("source", "attribution") and
                       (link["origin"] == "sonos" or args.include_upstream) and
                       incomplete(prior.get(link["filename"]))]
            if pending:
                versions.append(candidate)
                print(f"queued_incomplete_release={candidate} remaining={len(pending)}", flush=True)
    if not versions:
        print("No pending GPL downloads in the chosen scope; review recorded failures and use --retry-errors to revisit them.")
        return 0
    for version in versions:
        valid_version(version)
        record = catalog["releases"].get(version)
        if not record or record["state"] != "verified":
            raise ValueError(f"no verified direct-Sonos index for {version}")
        version_assets = records["artifacts"].setdefault(version, {})
        if args.publish and "gpl.html" not in version_assets:
            index_path = args.output / "indexes" / f"{version}.html"
            if file_sha256(index_path) != record["sha256"]:
                raise ValueError(f"index checksum changed for {version}")
            index_meta = {"state": "verified", "filename": "gpl.html", "sha256": record["sha256"],
                          "bytes": index_path.stat().st_size, "release_tag": f"gpl-{version}", "release_asset": "gpl.html"}
            receipt = publish({**index_meta, "version": version}, index_path, args.repo)
            index_meta.update(receipt)
            index_meta["published"] = True
            version_assets["gpl.html"] = index_meta
            atomic_json(args.output / "artifacts.json", records)
        for link in record["links"]:
            if args.filename and link["filename"] not in args.filename:
                continue
            if args.limit and count >= args.limit:
                break
            if link["origin"] != "sonos" and not args.include_upstream:
                continue
            if link["kind"] not in ("source", "attribution"):
                continue
            old = version_assets.get(link["filename"])
            if not incomplete(old):
                continue
            if old and old.get("state") in {"verified", "published"}:
                blob = args.store / "sha256" / old["sha256"][:2] / old["sha256"]
                if blob.is_file() and file_sha256(blob) == old["sha256"]:
                    if args.publish:
                        receipt = publish({**old, "version": version}, blob, args.repo)
                        old.update(receipt)
                        old["published"] = True
                        atomic_json(args.output / "artifacts.json", records)
                    continue
                # Ephemeral runner: recover missing local blobs from Sonos, not a
                # stale receipt left by a previously interrupted publication.
            for attempt in range(3):
                try:
                    result = download_artifact(link, args.store, min_free_bytes=args.reserve_bytes)
                except (OSError, TimeoutError) as error:
                    result = {"state": "network-error", "filename": link["filename"], "error": str(error)}
                if result["state"] not in {"network-error", "incomplete-transfer"} and not (
                    result["state"] == "http-error" and result.get("http_status") in {500, 502, 503, 504}
                ):
                    break
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
            if result["state"] == "verified":
                result["release_tag"] = f"gpl-{version}"
                total_bytes += result["bytes"]
                if args.publish:
                    receipt = publish({**result, "version": version}, args.store / "sha256" / result["sha256"][:2] / result["sha256"], args.repo)
                    result["publish_state"] = receipt["state"]
                    result["published"] = receipt["state"] == "published"
                    if receipt["state"] == "published":
                        result["release_url"] = receipt["release_url"]
                        result["release_asset"] = receipt["release_asset"]
                        result["original_name_conflict"] = receipt["original_name_conflict"]
                        result["state"] = "published"
                    else:
                        result["publish_conflict"] = receipt
            version_assets[link["filename"]] = result
            records["updated"] = timestamp()
            atomic_json(args.output / "artifacts.json", records)
            count += 1
            print(f"{version}/{link['filename']}: {result['state']} bytes={result.get('bytes', 0)}", flush=True)
            if result["state"] in {"insufficient-space", "invalid-artifact", "unexpected-content-type", "network-error", "incomplete-transfer"} or (
                result["state"] == "http-error" and result.get("http_status") in {429, 500, 502, 503, 504}
            ):
                if not args.continue_errors or result["state"] in {"insufficient-space", "network-error", "incomplete-transfer"}:
                    print("stopping on storage or transient failure", flush=True)
                    return 2
                print("recorded unavailable/invalid source; continuing", flush=True)
            if args.max_total_bytes and total_bytes >= args.max_total_bytes:
                break
            pause(args.delay)
        if args.limit and count >= args.limit:
            break
        if args.max_total_bytes and total_bytes >= args.max_total_bytes:
            break
    print(f"attempted={count} verified_bytes={total_bytes}")
    return 0


def report(args: argparse.Namespace) -> int:
    catalog = live_catalog(args.output)
    receipts = load_json(args.output / "artifacts.json", {"artifacts": {}})["artifacts"]
    discovery = load_json(args.output / "discovery.json", {"candidates": {}})["candidates"]
    site = load_json(args.output / "site-evidence.json", {"sitemaps": {}, "version_evidence": {}, "related_pages": []})
    binary = load_json(args.output / "binary-evidence.json", {"scans": {}, "version_evidence": {}})
    versions = {v: r for v, r in catalog["releases"].items() if r["state"] == "verified"}
    source = sum(sum(link["origin"] == "sonos" for link in r["links"]) for r in versions.values())
    upstream = sum(sum(link["origin"] == "upstream" for link in r["links"]) for r in versions.values())
    archived = sum(sum(r.get("state") in {"verified", "published"} for r in ver.values()) for ver in receipts.values())
    published = sum(sum(r.get("published", False) for r in ver.values()) for ver in receipts.values())
    release_coverage = {}
    for version, record in sorted(versions.items(), key=lambda pair: tuple(map(int, pair[0].split('.')))):
        expected = {link["filename"] for link in record["links"]}
        expected.add("gpl.html")
        data = receipts.get(version, {})
        published_expected = {name for name in expected if data.get(name, {}).get("published")}
        source_names = {link["filename"] for link in record["links"] if link["kind"] == "source"}
        source_published = source_names & published_expected
        fully_checked = {name for name in source_published if
                         data[name].get("stream_integrity", {}).get("state") == "verified" and
                         data[name].get("stream_integrity", {}).get("expected_sha256") == data[name].get("sha256")}
        integrity_problems = {name for name in source_published if
                              data[name].get("stream_integrity", {}).get("state") in
                              {"content-mismatch", "invalid-source", "invalid-artifact", "http-error", "network-error"}}
        release_coverage[version] = {
            "expected_assets": len(expected), "published_assets": len(published_expected),
            "remaining_assets": len(expected - published_expected),
            "complete_publication": len(expected) == len(published_expected),
            "published_source_archives": len(source_published),
            "full_compressed_stream_verified": len(fully_checked),
            "stream_integrity_problems": len(integrity_problems),
        }
    published_sources = sum(v["published_source_archives"] for v in release_coverage.values())
    verified_sources = sum(v["full_compressed_stream_verified"] for v in release_coverage.values())
    summary = {"schema_version": 1, "generated": timestamp(), "source": "direct-sonos",
               "verified_index_versions": sorted(versions, key=lambda s: tuple(map(int, s.split('.')))),
               "verified_index_total": len(versions), "sonos_download_links": source,
               "external_upstream_links": upstream, "verified_downloads": archived,
               "published_downloads": published,
               "fully_published_releases": sum(entry["complete_publication"] for entry in release_coverage.values()),
               "published_source_archives": published_sources,
               "full_compressed_stream_verified_source_archives": verified_sources,
               "published_source_archives_awaiting_full_stream_verification": published_sources - verified_sources,
               "release_coverage": release_coverage,
               "candidate_checks": len(discovery),
               "site_sitemaps_checked": len(site["sitemaps"]),
               "site_sitemaps_valid_xml": sum(item.get("state") == "verified-xml" for item in site["sitemaps"].values()),
               "site_linked_gpl_versions": sorted(site["version_evidence"], key=lambda s: tuple(map(int, s.split(".")))),
               "site_related_pages": len(site.get("related_pages", [])),
               "binary_scanned_inputs": len(binary.get("scans", {})),
               "binary_executable_text_bytes_scanned": sum(scan.get("bytes_scanned", 0) for scan in binary.get("scans", {}).values()),
               "binary_incomplete_scans": sum(not scan.get("complete", False) for scan in binary.get("scans", {}).values()),
               "binary_linked_gpl_versions": sorted(binary.get("version_evidence", {}), key=lambda s: tuple(map(int, s.split(".")))),
               "candidate_states": {s: sum(r["state"] == s for r in discovery.values()) for s in sorted({r["state"] for r in discovery.values()})},
               "completeness_proven": False,
               "limitations": ["The version grammar is unbounded in principle; unsuccessful probes do not prove a release never existed.",
                               "HTTP 403 is not treated as proof of absence.",
                               "Source archive validity does not prove exact correspondence with every distributed firmware binary."]}
    atomic_json(args.output / "report.json", summary)
    print(json.dumps(summary, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=LIVE)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("indexes", help="Fetch and validate directly hosted index pages")
    p.add_argument("--version", action="append", default=[])
    p.add_argument("--all-known", action="store_true")
    p.add_argument("--cache-dir", type=Path, help="Use previously captured direct-Sonos HTTP 200 bodies")
    p.add_argument("--delay", type=float, default=2.5)
    p.set_defaults(func=indexes)
    p = sub.add_parser("discover", help="Resume prioritized version grammar sweep")
    p.add_argument("--major-max", type=int, default=120)
    p.add_argument("--minor-max", type=int, default=99)
    p.add_argument("--limit", type=int, default=48)
    p.add_argument("--delay", type=float, default=2.5)
    p.add_argument("--binary", type=Path, action="append", default=[])
    p.add_argument("--recheck", action="store_true")
    p.set_defaults(func=discover)
    p = sub.add_parser("archive", help="Verify sources and optionally publish to GitHub Releases")
    p.add_argument("--version", action="append", default=[])
    p.add_argument("--next", action="store_true", help="Archive oldest release with unpublished download links")
    p.add_argument("--include-upstream", action="store_true", help="Also preserve the 13 distinct GNU/uClibc upstream sources")
    p.add_argument("--continue-errors", action="store_true", help="Record permanent per-artifact gaps without blocking the next file")
    p.add_argument("--retry-errors", action="store_true", help="Explicitly retry previously recorded permanent failures")
    p.add_argument("--filename", action="append", default=[], help="Only archive these exact source filenames")
    p.add_argument("--store", type=Path, default=ROOT / "artifacts/gpl-direct")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--max-total-bytes", type=int, default=0)
    p.add_argument("--reserve-bytes", type=int, default=512 * 1024 * 1024)
    p.add_argument("--delay", type=float, default=2.5)
    p.add_argument("--publish", action="store_true")
    p.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    p.set_defaults(func=archive)
    p = sub.add_parser("report", help="Summarize direct host coverage without overstating completeness")
    p.set_defaults(func=report)
    args = parser.parse_args()
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"GPL archiver: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
