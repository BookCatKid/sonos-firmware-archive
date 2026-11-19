#!/usr/bin/env python3
"""Reconcile every recorded archive asset with GitHub Release metadata."""

from __future__ import annotations

import argparse
import json
import subprocess
import urllib.error
from datetime import datetime, timezone
from pathlib import Path

from sonos_firmware.releases import remote_assets, graphql_assets
from sonos_firmware.storage import atomic_json


def walk_release_records(value, source: str, expected: dict, repository=None, *, inherited_tag=None,
                         inherited_repository=None, published=False) -> None:
    if isinstance(value, dict):
        if value.get('artifact_status') not in {None, 'preserved'} or value.get('published') is False:
            return
        tag = value.get("release_tag") or inherited_tag
        name = value.get("release_asset") or value.get("filename")
        owner = value.get('repository') or inherited_repository
        url = value.get('release_url') or ''
        if not value.get('repository') and url.startswith('https://github.com/'):
            owner = '/'.join(url.split('/')[3:5])
        matches_repository = not repository or not owner or owner.lower() == repository.lower()
        published = published or value.get('status') == 'verified' or value.get('artifact_status') == 'preserved' or value.get('published') is True
        if published and tag and name and value.get("sha256") and isinstance(value.get("bytes"), int) and matches_repository:
            record = {
                "bytes": value["bytes"],
                "sha256": value["sha256"],
                "source": source,
            }
            prior = expected.get((tag, name))
            if prior and (prior['bytes'], prior['sha256']) != (record['bytes'], record['sha256']):
                raise ValueError(f'conflicting publication identities: {tag}/{name}')
            expected[(tag, name)] = record
        for key, child in value.items():
            if key in {'pending_captures', 'pending_publication', 'unpublished_captures'}:
                continue
            # Completed legacy collections predate explicit publication proofs.
            # Superseded captures stay on the Release after a later capture of
            # the same deployment replaces them in the receipt.
            legacy = (key in {'source_manifests', 'raw_images', 'artifacts', 'deployments',
                              'superseded_captures'} and isinstance(child, list)
                      or published and key in {'resources', 'components', 'complete_wrapper', 'bundle', 'bundles', 'alternate_bundles'})
            walk_release_records(child, source, expected, repository, inherited_tag=tag, inherited_repository=owner,
                                 published=legacy)
    elif isinstance(value, list):
        for child in value:
            walk_release_records(child, source, expected, repository, inherited_tag=inherited_tag,
                                 inherited_repository=inherited_repository, published=published)


def recorded_assets(root: Path, repository=None) -> dict:
    expected = {}
    paths = {root/'data/catalog.json', *(root/'data/apps').glob('*.json')}
    if (root/'config/targets.json').exists():
        from sonos_firmware.platform.registry import targets
        for target in targets(root).values():
            if target.provider == 'release-audit':
                continue
            for relative in target.metadata:
                location = root/relative
                paths.update(location.rglob('*.json') if location.is_dir() else [location])
    for path in sorted(paths):
        if not path.exists() or path.suffix != '.json':
            continue
        walk_release_records(
            json.loads(path.read_text(encoding="utf-8")), str(path.relative_to(root)), expected, repository,
        )

    # The GPL catalog predates explicit release_tag fields. Its publication
    # convention is stable and documented as gpl-<release>.
    gpl_path = root / "data/gpl/wayback-catalog.json"
    if gpl_path.exists():
        gpl = json.loads(gpl_path.read_text(encoding="utf-8"))
        for release in gpl["releases"]:
            tag = f"gpl-{release['release']}"
            artifacts = [
                item
                for item in release.get("artifacts", [])
                if item.get("downloaded") and item.get("local_sha256")
            ]
            for item in artifacts:
                walk_release_records({'release_tag': tag, 'release_asset': item['filename'],
                    'bytes': item['local_bytes'], 'sha256': item['local_sha256'],
                    'repository': item.get('repository'), 'release_url': item.get('release_url', '')},
                    str(gpl_path.relative_to(root)), expected, repository, published=True)
            if not any(item["filename"] == "gpl.html" for item in artifacts):
                index = release["index"]
                walk_release_records({'release_tag': tag, 'release_asset': 'gpl.html',
                    'bytes': index['bytes'], 'sha256': index['sha256']},
                    f'{gpl_path.relative_to(root)}:index', expected, repository, published=True)
    live_path = root / "data/gpl/live/artifacts.json"
    if live_path.exists():
        live = json.loads(live_path.read_text(encoding="utf-8"))
        for version, items in live.get("artifacts", {}).items():
            for item in items.values():
                if not item.get("published") or not item.get("sha256") or not isinstance(item.get("bytes"), int):
                    continue
                tag = item.get("release_tag") or f"gpl-{version}"
                name = item.get("release_asset") or item["filename"]
                walk_release_records({**item, 'release_tag': tag, 'release_asset': name},
                                     str(live_path.relative_to(root)), expected, repository, published=True)
    return expected


def github_assets(repository: str) -> tuple[int, dict]:
    output = subprocess.check_output(
        [
            "gh",
            "api",
            "--paginate",
            "--slurp",
            f"repos/{repository}/releases?per_page=100",
        ],
        text=True,
    )
    pages = json.loads(output)
    releases = [release for page in pages for release in page]
    assets = {}
    for release in releases:
        try:
            observed = remote_assets(repository, release['tag_name'])
        except urllib.error.HTTPError as error:
            # A release listed by the authenticated API can be invisible to
            # anonymous REST access (for example a private or draft release).
            if error.code != 404:
                raise
            error.close()
            observed = {item['name']: item for item in graphql_assets(repository, release['tag_name'])}
        for name, asset in observed.items():
            assets[(release["tag_name"], name)] = {
                "bytes": asset["size"],
                "sha256": (asset.get("digest") or "").removeprefix("sha256:"),
                "url": asset["browser_download_url"],
            }
    return len(releases), assets


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", help="GitHub OWNER/REPO; defaults to the current repository")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    repository = args.repo or subprocess.check_output(
        ["gh", "repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"],
        text=True,
    ).strip()
    expected = recorded_assets(root, repository)
    release_total, actual = github_assets(repository)
    missing = []
    mismatched = []
    for key, record in sorted(expected.items()):
        observed = actual.get(key)
        identity = {"release_tag": key[0], "release_asset": key[1]}
        if observed is None:
            missing.append({**identity, **record})
        elif observed["bytes"] != record["bytes"] or observed["sha256"] != record["sha256"]:
            mismatched.append({**identity, "expected": record, "observed": observed})
    extras = [
        {"release_tag": tag, "release_asset": name, **actual[(tag, name)]}
        for tag, name in sorted(set(actual) - set(expected))
    ]
    # An asset whose exact bytes are already recorded under another release is
    # a duplicate publication, not an unexplained one. Distinguishing the two
    # keeps the remaining unrecorded assets actionable.
    by_digest = {}
    for (tag, name), record in expected.items():
        by_digest.setdefault(record["sha256"], []).append({"release_tag": tag, "release_asset": name})
    for extra in extras:
        extra["recorded_as"] = by_digest.get(extra["sha256"], [])
    duplicated = [extra for extra in extras if extra["recorded_as"]]
    unexplained = [extra for extra in extras if not extra["recorded_as"]]
    report = {
        "schema_version": 1,
        "checked": datetime.now(timezone.utc).isoformat(),
        "repository": repository,
        "release_total": release_total,
        "github_asset_total": len(actual),
        "recorded_asset_total": len(expected),
        "missing_total": len(missing),
        "mismatched_total": len(mismatched),
        "unrecorded_extra_total": len(extras),
        "duplicated_extra_total": len(duplicated),
        "unexplained_extra_total": len(unexplained),
        "missing": missing,
        "mismatched": mismatched,
        "unrecorded_extras": extras,
        "duplicated_extras": duplicated,
        "unexplained_extras": unexplained,
    }
    atomic_json(args.output, report)
    print(
        f"releases={release_total} github={len(actual)} recorded={len(expected)} "
        f"missing={len(missing)} mismatched={len(mismatched)} extras={len(extras)} "
        f"duplicated={len(duplicated)} unexplained={len(unexplained)}"
    )
    return 1 if missing or mismatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
