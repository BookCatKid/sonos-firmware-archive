#!/usr/bin/env python3
"""Recover web-capture publication receipts from their published bundles.

A web capture publishes one bundle per capture plus every captured resource.
If a worker's receipt commit is lost -- an interrupted job, a rejected rebase, a
worker that published and then failed -- the Release keeps verified assets while
`data/apps/web-app-archive.json` never learns about them. The audit then reports
them as unrecorded extras.

Each bundle contains the capture's own `capture.json`, which records the exact
capture ID, deployment fingerprint, resource identities and publication
receipts. That document, plus the Release-side size and SHA-256 of every asset,
is sufficient evidence to rebuild the missing receipt without downloading any
resource body and without deleting historical evidence.

Never infer a capture from an asset name alone: the bundle must exist, its bytes
must match its own receipt, and its embedded `capture.json` must agree with the
Release tag, app and asset names it claims.
"""

from __future__ import annotations

import argparse
import io
import json
import subprocess
import tarfile
import urllib.request
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath

from sonos_firmware.web_archive import APPS, _register_deployments, receipt_capture
from sonos_firmware.storage import atomic_json

ROOT = Path(__file__).resolve().parents[1]
USER_AGENT = "sonos-firmware-archive/1"

# A bundle is the only asset that carries the capture's own metadata. Its name
# embeds the capture ID twice-over through the sonos-<app>-<capture>-- prefix.
BUNDLE_SUFFIX = "--complete.tar.gz"


def release_assets(repository: str, tag: str) -> list[dict]:
    """List a release's assets by ID; `releases/tags/.../assets` is not a route."""
    release = json.loads(subprocess.check_output(
        ["gh", "api", f"repos/{repository}/releases/tags/{tag}"], text=True))
    output = subprocess.check_output(
        ["gh", "api", "--paginate", "--slurp",
         f"repos/{repository}/releases/{release['id']}/assets?per_page=100"],
        text=True,
    )
    return [asset for page in json.loads(output) for asset in page]


def bundle_capture_id(name: str, app: str) -> str | None:
    prefix = f"sonos-{app}-"
    if not name.startswith(prefix) or not name.endswith(BUNDLE_SUFFIX):
        return None
    remainder = name[len(prefix):-len(BUNDLE_SUFFIX)]
    capture_id, _, digest = remainder.partition("--")
    if not capture_id or not capture_id[0].isdigit():
        return None
    if digest and (len(digest) != 12 or any(char not in "0123456789abcdef" for char in digest)):
        return None
    return capture_id


def read_capture_document(url: str) -> dict:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=300) as response:
        body = response.read()
    with tarfile.open(fileobj=io.BytesIO(body), mode="r:gz") as archive:
        members = [member for member in archive.getmembers()
                   if PurePosixPath(member.name).name == "capture.json" and member.isfile()]
        if len(members) != 1:
            raise ValueError(f"bundle must contain exactly one capture.json, found {len(members)}")
        extracted = archive.extractfile(members[0])
        if extracted is None:
            raise ValueError("bundle capture.json is not a regular file")
        return json.loads(extracted.read())


def recover_capture(document: dict, assets: dict[str, dict], *, app: str, tag: str,
                    repository: str, run_url: str, bundle_name: str) -> dict:
    """Verify a bundle's own claim against the Release before trusting it.

    The bundle embeds the capture document as it stood before publication, so
    it may not carry the bundle receipt itself. Release metadata supplies that
    identity; the document must still prove the capture and its resource set.
    """
    capture_id = document.get("capture_id")
    if not isinstance(capture_id, str) or not capture_id:
        raise ValueError("capture document has no capture ID")
    if document.get("app") != app:
        raise ValueError(f"capture app {document.get('app')!r} does not match release app {app!r}")
    if document.get("release_tag") != tag:
        raise ValueError(f"capture release tag {document.get('release_tag')!r} does not match {tag!r}")
    if not document.get("deployment_fingerprint"):
        # An identity-incomplete capture was never treated as a published
        # deployment, so it cannot become one retroactively.
        raise ValueError(f"{capture_id}: capture has no deployment fingerprint")
    resources = document.get("resources")
    if not isinstance(resources, list):
        raise ValueError(f"{capture_id}: capture document is not a publication receipt")

    observed = assets.get(bundle_name)
    if observed is None:
        raise ValueError(f"{capture_id}: published bundle {bundle_name!r} is absent from the release")
    document.setdefault("bundle", {})
    embedded = document["bundle"] if isinstance(document["bundle"], dict) else {}
    digest = (observed.get("digest") or "").removeprefix("sha256:")
    if embedded.get("sha256") and digest and embedded["sha256"] != digest:
        raise ValueError(f"{capture_id}: bundle digest disagrees with release metadata")

    # Every resource the capture claims to have published must exist with the
    # size and hash the capture recorded. A partial release is not recovered.
    recovered = 0
    for row in resources:
        name = row.get("release_asset")
        if not row.get("file") or not name:
            continue
        remote = assets.get(name)
        if remote is None:
            raise ValueError(f"{capture_id}: published resource {name} is absent from the release")
        remote_digest = (remote.get("digest") or "").removeprefix("sha256:")
        if remote.get("size") != row.get("bytes") or (remote_digest and remote_digest != row.get("sha256")):
            raise ValueError(f"{capture_id}: resource {name} disagrees with release metadata")
        row.update(
            status="verified", repository=repository, release_tag=tag, release_url=remote["browser_download_url"],
            receipt_recovered_from=run_url,
        )
        recovered += 1
    if not recovered:
        raise ValueError(f"{capture_id}: capture document lists no published resources")
    document["bundle"] = {
        **embedded,
        "release_asset": bundle_name,
        "bytes": observed["size"],
        "sha256": digest or embedded.get("sha256"),
        "status": "verified", "repository": repository, "release_tag": tag,
        "release_url": observed["browser_download_url"], "receipt_recovered_from": run_url,
    }
    if not document["bundle"]["sha256"]:
        raise ValueError(f"{capture_id}: Release metadata exposes no bundle digest")
    document["repository"] = repository
    document["receipt_recovered_from"] = run_url
    document["receipt_recovered_at"] = datetime.now(timezone.utc).isoformat()
    return {"document": receipt_capture(document), "resources": recovered}


def reconcile(state: dict, *, repository: str, run_url: str, app_names=None, dry_run=False) -> dict:
    """Add only captures whose bundle proves its own publication identity.

    A later capture of an unchanged deployment supersedes an earlier one for
    receipt purposes, but the earlier capture's assets stay on the Release. They
    are retained as superseded evidence instead of being discarded, so the
    archive audit can still account for every published asset.
    """
    recorded = {(row.get("app"), row.get("deployment_fingerprint")) for row in state.get("deployments", [])
                if not row.get("repository") or row.get("repository") == repository}
    retained = {(row.get("app"), row.get("capture_id")) for row in state.get("superseded_captures", [])}
    # A resumed legacy capture can be published under a bundle name that differs
    # from the capture ID inside it. Once a bundle is already recorded there is
    # nothing to recover, so it is skipped before its contents are judged.
    accounted = set()
    for row in [*state.get("deployments", []), *state.get("superseded_captures", [])]:
        for item in [row.get("bundle"), *(row.get("bundles") or []), *(row.get("alternate_bundles") or [])]:
            if isinstance(item, dict) and item.get("release_asset"):
                accounted.add((row.get("app"), item["release_asset"]))
    apps = list(APPS) if not app_names else list(app_names)
    report = {"recovered": [], "superseded": [], "skipped": [], "errors": []}
    for app in apps:
        tag = APPS[app]["release_tag"]
        try:
            assets = {asset["name"]: asset for asset in release_assets(repository, tag)}
        except subprocess.CalledProcessError as error:
            report["errors"].append({"app": app, "release_tag": tag, "error": str(error)})
            continue
        documents, older = [], []
        for name, asset in sorted(assets.items()):
            capture_id = bundle_capture_id(name, app)
            if capture_id is None:
                continue
            if (app, name) in accounted:
                report["skipped"].append({"app": app, "capture_id": capture_id, "bundle": name,
                                          "reason": "bundle already recorded"})
                continue
            try:
                document = read_capture_document(asset["browser_download_url"])
                if document.get("capture_id") != capture_id:
                    raise ValueError(f"bundle name says {capture_id} but capture.json says {document.get('capture_id')}")
                recovered = recover_capture(document, assets, app=app, tag=tag,
                                            repository=repository, run_url=run_url, bundle_name=name)
            except Exception as error:
                report["errors"].append({"app": app, "release_tag": tag, "asset": name, "error": str(error)})
                continue
            identity = (app, recovered["document"]["deployment_fingerprint"])
            entry = {"app": app, "capture_id": capture_id,
                     "resources": recovered["resources"], "bundle": name}
            if identity in recorded:
                if (app, capture_id) in retained:
                    report["skipped"].append({**entry, "reason": "already retained"})
                else:
                    older.append((identity, recovered["document"]))
                    report["superseded"].append({**entry, "reason": "later capture holds this deployment"})
                continue
            recorded.add(identity)
            documents.append(recovered["document"])
            report["recovered"].append(entry)
        if older and not dry_run:
            existing = state.setdefault("superseded_captures", [])
            existing.extend(document for _, document in older)
            existing.sort(key=lambda row: (row.get("app", ""), row.get("capture_id", "")))
        if documents and not dry_run:
            _register_deployments(state, documents)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--receipt", type=Path, default=ROOT / "data/apps/web-app-archive.json")
    parser.add_argument("--app", action="append", choices=list(APPS), help="limit to one app; repeatable")
    parser.add_argument("--run-url", required=True,
                        help="workflow run that published these captures")
    parser.add_argument("--report", type=Path, help="write the recovery report as JSON")
    parser.add_argument("--dry-run", action="store_true", help="report only; do not write the receipt")
    args = parser.parse_args()

    state = json.loads(args.receipt.read_text()) if args.receipt.exists() else {"schema_version": 1, "deployments": []}
    report = reconcile(state, repository=args.repo, run_url=args.run_url,
                       app_names=args.app, dry_run=args.dry_run)
    if args.report:
        atomic_json(args.report, {"schema_version": 1, **report})
    if not args.dry_run and (report["recovered"] or report["superseded"]):
        state["updated"] = datetime.now(timezone.utc).isoformat()
        state["deployment_total"] = len(state["deployments"])
        atomic_json(args.receipt, state)
    print(f"recovered={len(report['recovered'])} superseded={len(report['superseded'])} "
          f"skipped={len(report['skipped'])} errors={len(report['errors'])}")
    return 1 if report["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
