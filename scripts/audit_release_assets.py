#!/usr/bin/env python3
"""Reconcile every recorded archive asset with GitHub Release metadata."""

from __future__ import annotations

import argparse
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path


def walk_release_records(value, source: str, expected: dict) -> None:
    if isinstance(value, dict):
        tag = value.get("release_tag")
        name = value.get("release_asset") or value.get("filename")
        if tag and name and value.get("sha256") and isinstance(value.get("bytes"), int):
            expected[(tag, name)] = {
                "bytes": value["bytes"],
                "sha256": value["sha256"],
                "source": source,
            }
        for child in value.values():
            walk_release_records(child, source, expected)
    elif isinstance(value, list):
        for child in value:
            walk_release_records(child, source, expected)


def recorded_assets(root: Path) -> dict:
    expected = {}
    catalog_path = root / "data/catalog.json"
    walk_release_records(
        json.loads(catalog_path.read_text(encoding="utf-8")),
        str(catalog_path.relative_to(root)),
        expected,
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
                expected[(tag, item["filename"])] = {
                    "bytes": item["local_bytes"],
                    "sha256": item["local_sha256"],
                    "source": str(gpl_path.relative_to(root)),
                }
            if not any(item["filename"] == "gpl.html" for item in artifacts):
                index = release["index"]
                expected[(tag, "gpl.html")] = {
                    "bytes": index["bytes"],
                    "sha256": index["sha256"],
                    "source": f"{gpl_path.relative_to(root)}:index",
                }
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
        for asset in release.get("assets", []):
            assets[(release["tag_name"], asset["name"])] = {
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
    expected = recorded_assets(root)
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
        "missing": missing,
        "mismatched": mismatched,
        "unrecorded_extras": extras,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        f"releases={release_total} github={len(actual)} recorded={len(expected)} "
        f"missing={len(missing)} mismatched={len(mismatched)} extras={len(extras)}"
    )
    return 1 if missing or mismatched else 0


if __name__ == "__main__":
    raise SystemExit(main())
