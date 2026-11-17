#!/usr/bin/env python3
"""Capture public Sonos web deployments and optionally publish every body.

This deliberately archives only account-free resources. Redirects into login
and HTML returned for an advertised JavaScript/CSS URL are recorded as gaps,
not mislabeled as the requested asset.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import re
import tarfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

from .releases import ensure_release, publish_collection, publication_receipt
from .storage import atomic_json, file_sha256
from .retry import checkpoint, select
from .source_links import discover_urls

ROOT = Path(__file__).resolve().parents[2]
USER_AGENT = "sonos-firmware-archive/1 (+https://github.com/BookCatKid/sonos-firmware-archive)"
APPS = {
    "controller": {
        "entry_url": "https://play.sonos.com/",
        "seed_urls": ["https://play.sonos.com/manifest.webmanifest"],
        "release_tag": "apps-web-controller",
        "title": "Sonos Web controller deployment archive",
    },
    "pro": {
        "entry_url": "https://pro.sonos.com/",
        "seed_urls": [],
        "release_tag": "apps-web-pro",
        "title": "Sonos Pro web dashboard deployment archive",
    },
}
TEXT_TYPES = ("text/", "javascript", "json", "xml", "manifest")
STATIC_PREFIXES = ("/_next/static/", "/assets/", "/fonts/")


class RedirectRecorder(urllib.request.HTTPRedirectHandler):
    def __init__(self) -> None:
        self.chain: list[dict] = []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self.chain.append({
            "status": code,
            "url": req.full_url,
            "location": urllib.parse.urljoin(req.full_url, newurl),
        })
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def fetch(url: str) -> dict:
    redirects = RedirectRecorder()
    opener = urllib.request.build_opener(
        urllib.request.HTTPCookieProcessor(), redirects
    )
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with opener.open(request, timeout=90) as response:
            body = response.read()
            headers = {key.lower(): value for key, value in response.headers.items()}
            return {
                "requested_url": url,
                "final_url": response.geturl(),
                "status": response.status,
                "redirects": redirects.chain,
                "headers": headers,
                "body": body,
            }
    except urllib.error.HTTPError as error:
        body = error.read()
        return {
            "requested_url": url,
            "final_url": error.geturl(),
            "status": error.code,
            "redirects": redirects.chain,
            "headers": {key.lower(): value for key, value in error.headers.items()},
            "body": body,
        }


def is_text(headers: dict) -> bool:
    content_type = headers.get("content-type", "").lower()
    return any(marker in content_type for marker in TEXT_TYPES)




def allowed_asset(url: str, host: str) -> bool:
    try:
        parsed = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    if parsed.netloc != host:
        return False
    path = parsed.path
    if path in {"/manifest.webmanifest", "/favicon.ico", "/robots.txt"}:
        return True
    if not path.startswith(STATIC_PREFIXES):
        return False
    return bool(re.search(
        r"\.(?:[cm]?js|wasm|css|json|map|woff2?|ttf|otf|png|jpe?g|gif|svg|webp|avif|ico)$",
        path, re.IGNORECASE,
    ))


def safe_asset_name(capture_id: str, url: str, digest: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    basename = Path(parsed.path).name or "index.html"
    stem, suffix = os.path.splitext(basename)
    if not suffix:
        suffix = mimetypes.guess_extension("application/octet-stream") or ".bin"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:90]
    return f"{capture_id}--{stem}--{digest}{suffix.lower()}"


def classify(result: dict) -> str:
    requested = urllib.parse.urlsplit(result["requested_url"])
    final = urllib.parse.urlsplit(result["final_url"])
    expected_static = requested.path.startswith(STATIC_PREFIXES)
    content_type = result["headers"].get("content-type", "").lower()
    if result["status"] != 200:
        return "http-error"
    segments = set(urllib.parse.unquote(final.path).lower().split('/'))
    if not expected_static and (segments & {'login', 'signin', 'sign-in', 'authorize'} or
            final.hostname in {'login.sonos.com', 'accounts.sonos.com', 'auth.sonos.com'}):
        return 'gated-or-redirected'
    if expected_static and (final.netloc != requested.netloc or final.path != requested.path):
        return "gated-or-redirected"
    if expected_static and "text/html" in content_type:
        return "gated-or-mislabeled"
    return "captured"


def public_probe(app: str) -> dict:
    config = APPS[app]
    root = fetch(config["entry_url"])
    records = [root]
    urls = list(config["seed_urls"])
    if app == "pro":
        urls.extend(
            url for url in discover_urls(root["final_url"], root["body"])
            if "_buildManifest.js" in url or "_ssgManifest.js" in url
        )
    for url in sorted(set(urls)):
        records.append(fetch(url))
    evidence = [{
        "requested_url": item["requested_url"],
        "final_url": item["final_url"],
        "status": item["status"],
        "sha256": sha256(item["body"]),
        "bytes": len(item["body"]),
    } for item in records]
    fingerprint = sha256(json.dumps(evidence, sort_keys=True).encode())
    return {"app": app, "fingerprint": fingerprint, "resources": evidence}


def capture(
    app: str, destination: Path, max_assets: int = 500,
    capture_id: str | None = None, *, pending_urls=(),
) -> tuple[dict, Path]:
    config = APPS[app]
    if max_assets <= 0:
        raise ValueError('web asset limit must be positive')
    capture_id = capture_id or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if Path(capture_id).name != capture_id or capture_id in {'', '.', '..'}:
        raise ValueError('capture ID must be a basename')
    capture_dir = destination / app / capture_id
    bodies_dir = capture_dir / "bodies"
    bodies_dir.mkdir(parents=True, exist_ok=False)
    host = urllib.parse.urlsplit(config["entry_url"])[1]
    queue = [config["entry_url"], *config["seed_urls"],
             *(url for url in pending_urls if allowed_asset(url, host))]
    seen: set[str] = set()
    resources: list[dict] = []
    while queue and len(seen) < max_assets:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        try:
            result = fetch(url)
        except (OSError, ValueError) as error:
            resources.append({'requested_url': url, 'outcome': 'network-error', 'error': str(error)})
            continue
        digest = sha256(result["body"])
        outcome = classify(result)
        record = {key: result[key] for key in (
            "requested_url", "final_url", "status", "redirects", "headers"
        )}
        record.update({"outcome": outcome, "bytes": len(result["body"]), "sha256": digest})
        # A public login shell is useful source evidence and advertises public
        # static bundles. Preserve its actual identity without promoting the
        # requested authenticated application to a complete capture.
        public_shell = (url == config['entry_url'] and outcome == 'gated-or-redirected' and
                        result['status'] == 200 and
                        'text/html' in result['headers'].get('content-type', '').lower() and
                        urllib.parse.urlsplit(result['final_url']).scheme == 'https' and
                        urllib.parse.urlsplit(result['final_url']).netloc == host)
        if outcome == "captured" or public_shell:
            name = safe_asset_name(capture_id, result['final_url'] if public_shell else url, digest)
            if public_shell:
                record['evidence_type'] = 'public-login-page'
                if name.endswith('.bin'):
                    name = name[:-4] + '.html'
            relative = Path("bodies") / name
            (capture_dir / relative).write_bytes(result["body"])
            record["file"] = str(relative)
            record["release_asset"] = name
            if is_text(result["headers"]):
                for found in sorted(discover_urls(result["final_url"], result["body"])):
                    if allowed_asset(found, host) and found not in seen:
                        queue.append(found)
        resources.append(record)
    probe_items = [{
        "requested_url": item["requested_url"], "final_url": item["final_url"],
        "status": item["status"], "sha256": item["sha256"], "bytes": item["bytes"],
    } for item in resources if 'sha256' in item and (item["requested_url"] == config["entry_url"]
       or item["requested_url"] in config["seed_urls"]
       or "_buildManifest.js" in item["requested_url"]
       or "_ssgManifest.js" in item["requested_url"])]
    probe_fingerprint = sha256(json.dumps(probe_items, sort_keys=True).encode())
    # Optional discovered manifests may fall into a later bounded batch. Keep
    # deployment identity stable across those batches; retain the richer probe
    # separately as evidence of mutable resources at the same public entrypoint.
    deployment_fingerprint = sha256(json.dumps([item for item in probe_items
        if item['requested_url'] in [config['entry_url'], *config['seed_urls']]], sort_keys=True).encode())
    successful = {row['requested_url'] for row in resources if row['outcome'] == 'captured'}
    identity_complete = all(url in successful for url in [config['entry_url'], *config['seed_urls']])
    document = {
        "schema_version": 1,
        "app": app,
        "entry_url": config["entry_url"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_id": capture_id,
        "probe_fingerprint": probe_fingerprint,
        "deployment_fingerprint": deployment_fingerprint if identity_complete else None,
        "identity_complete": identity_complete,
        "resource_total": len(resources),
        "captured_total": sum(item["outcome"] == "captured" for item in resources),
        "gap_total": sum(item["outcome"] != "captured" for item in resources),
        "release_tag": config["release_tag"],
        "resources": resources,
        "pending_urls": sorted(set(queue) - seen),
        "complete": not (set(queue) - seen) and all(item['outcome'] == 'captured' for item in resources),
        "boundary": "Public deployment bytes only; no login session or cloud API data.",
    }
    metadata = capture_dir / "capture.json"
    metadata.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    bundle = capture_dir.with_name(capture_dir.name + '.tar.gz')
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(capture_dir, arcname=f"sonos-{app}-{capture_id}")
    document["bundle"] = {
        "file": str(bundle), "bytes": bundle.stat().st_size,
        "sha256": file_sha256(bundle),
        "release_asset": f"sonos-{app}-{capture_id}--complete.tar.gz",
    }
    metadata.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document, metadata


def _stage_bundle(document, metadata):
    bundle = Path(document["bundle"]["file"])
    renamed_bundle = bundle.with_name(document["bundle"]["release_asset"])
    if renamed_bundle != bundle:
        try:
            renamed_bundle.hardlink_to(bundle)
        except FileExistsError:
            if (renamed_bundle.stat().st_size != document['bundle']['bytes'] or
                    file_sha256(renamed_bundle) != document['bundle']['sha256']):
                raise ValueError('retained bundle name belongs to another capture')
        document["bundle"]["file"] = str(renamed_bundle)
    atomic_json(metadata, document)


def upload_capture(document: dict, metadata: Path, repository: str) -> None:
    config = APPS[document["app"]]
    document['repository'] = repository
    _stage_bundle(document, metadata)
    ensure_release(repository, config["release_tag"], config["title"],
                   "Account-free deployment snapshots, exact public response bodies and reconstruction bundles.")
    capture_dir = metadata.parent
    paths = {
        item["release_asset"]: capture_dir / item["file"]
        for item in document["resources"] if item.get("file")
    }
    paths[document['bundle']['release_asset']] = Path(document['bundle']['file'])
    captures = [item for item in document["resources"] if item.get("release_asset")] + [document["bundle"]]
    observed = publish_collection(repository, config["release_tag"],
                                  [(paths[item["release_asset"]], item) for item in captures])
    for item in captures:
        item.update(publication_receipt(repository, config["release_tag"], item["release_asset"],
                                        item, observed[item["release_asset"]]))
    metadata.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def update_receipt(path: Path, deployments: list[dict]) -> dict:
    existing = {"schema_version": 1, "deployments": []}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    for deployment in deployments:
        _mark_published(existing, deployment, deployment['repository'])
    if deployments:
        _save_receipt(path, existing, deployments[-1]['repository'])
    return existing


def _register_deployments(existing, deployments):
    by_identity = {
        (item.get('repository'), item["app"], item["deployment_fingerprint"]): item
        for item in existing["deployments"]
    }
    for deployment in deployments:
        deployment = receipt_capture(deployment)
        key = (deployment.get('repository'), deployment["app"], deployment["deployment_fingerprint"])
        prior = by_identity.get(key)
        if prior:
            resources = {(row['requested_url'], row.get('sha256')): row for row in prior['resources']}
            for row in deployment['resources']:
                if row['outcome'] == 'captured':
                    resources = {identity: value for identity, value in resources.items()
                        if value['requested_url'] != row['requested_url'] or value['outcome'] == 'captured'}
                resources[(row['requested_url'], row.get('sha256'))] = row
            deployment['resources'] = list(resources.values())
            successful = {row['requested_url'] for row in resources.values() if row['outcome'] == 'captured'}
            deployment['pending_urls'] = sorted((set(prior.get('pending_urls', [])) |
                                                set(deployment.get('pending_urls', []))) - successful)
            deployment['resource_total'] = len(resources)
            deployment['captured_total'] = sum(row['outcome'] == 'captured' for row in resources.values())
            deployment['gap_total'] = sum(row['outcome'] != 'captured' for row in resources.values())
            deployment['complete'] = not deployment['pending_urls'] and not deployment['gap_total']
            bundles = {row['sha256']: row for row in [*prior.get('bundles', []), prior['bundle'], deployment['bundle']]}
            deployment['bundles'] = list(bundles.values())
        deployment['bundle_scope'] = 'capture-batch'
        deployment['complete'] = deployment['complete'] and deployment.get('identity_complete', True)
        by_identity[key] = deployment
    existing["deployments"] = sorted(
        by_identity.values(), key=lambda item: (item["app"], item["captured_at"])
    )
    existing["updated"] = datetime.now(timezone.utc).isoformat()
    existing["deployment_total"] = len(existing["deployments"])


def receipt_capture(document):
    """Keep the source identity without making a worker path a prerequisite."""
    result = json.loads(json.dumps(document))
    result['bundle'].pop('file', None)
    return result


def _save_receipt(path, state, repository):
    observations = state.setdefault('observations', {}).setdefault(repository, {})
    pending = [row for row in state.get('pending_captures', []) if row['repository'] == repository]
    errors = [{'app': app, 'error': row.get('error', 'incomplete web capture')}
              for app, row in observations.items() if row['status'] != 'published']
    state.update(updated=datetime.now(timezone.utc).isoformat(), repository=repository,
                 pending_total=len(pending), errors=errors, status='partial' if pending or errors else 'ok')
    atomic_json(path, state)


def _mark_published(state, document, repository):
    _register_deployments(state, [document])
    state['pending_captures'] = [row for row in state.get('pending_captures', [])
        if not (row['repository'] == repository and row['bundle']['sha256'] == document['bundle']['sha256'])]
    result = next(row for row in state['deployments'] if row.get('repository') == repository
        and row['app'] == document['app'] and row['deployment_fingerprint'] == document['deployment_fingerprint'])
    observations = state.setdefault('observations', {}).setdefault(repository, {})
    observations[document['app']] = {**checkpoint(observations.get(document['app']), success=result['complete'], success_hours=1),
                                    'status': 'published' if result['complete'] else 'partial'}
    return result


def archive_apps(apps, destination, receipt, repository, *, max_assets=500, capture_id=None, upload=False):
    """Discover, capture and checkpoint independent sites from empty archive state."""
    def load():
        return json.loads(receipt.read_text()) if receipt.exists() else {'schema_version': 1, 'deployments': []}

    state = load()
    observations = state.setdefault('observations', {}).setdefault(repository, {})

    def save():
        _save_receipt(receipt, state, repository)

    def promote(document):
        result = _mark_published(state, document, repository)
        save()
        return result

    resolved = set()
    if upload:
        pending = [row for row in state.get('pending_captures', [])
                   if row['repository'] == repository and row['app'] in apps]
        for document in select(pending, {row['bundle']['sha256']: row for row in pending},
                               key=lambda row: row['bundle']['sha256'], limit=4,
                               state=state, queue=f'{repository}/{",".join(sorted(apps))}'):
            try:
                config = APPS[document['app']]
                ensure_release(repository, document['release_tag'], config['title'],
                               'Account-free web deployment capture recovery.')
                captures = [item for item in document['resources'] if item.get('release_asset')] + [document['bundle']]
                def retained_path(item):
                    if item is document['bundle']:
                        path = destination/document['app']/item['release_asset']
                    else:
                        path = destination/document['app']/document['capture_id']/item['file']
                    if not path.resolve().is_relative_to(destination.resolve()):
                        raise ValueError('retained web capture escapes its worker directory')
                    return path
                observed = publish_collection(repository, document['release_tag'],
                    [(retained_path(item), item) for item in captures])
                for item in captures:
                    item.update(publication_receipt(repository, document['release_tag'], item['release_asset'],
                                                     item, observed[item['release_asset']]))
                promote(document)
                if capture_id == document['capture_id']:
                    resolved.add(document['app'])
            except Exception as error:
                document.update(checkpoint(document), error=str(error))
                save()
    for app in apps:
        if app in resolved:
            continue
        try:
            pending_urls = list(dict.fromkeys(url for row in state['deployments']
                if row['app'] == app and row.get('repository') == repository
                for url in [*row.get('pending_urls', []), *(item['requested_url'] for item in row['resources']
                                                          if item['outcome'] != 'captured')]))
            document, metadata = capture(app, destination, max_assets, capture_id, pending_urls=pending_urls)
            document['repository'] = repository
            document['bundle']['release_asset'] = (
                f"sonos-{app}-{document['capture_id']}--{document['bundle']['sha256']}--complete.tar.gz")
            _stage_bundle(document, metadata)
            state.setdefault('pending_captures', []).append(receipt_capture(document))
            observations[app] = {'status': 'pending', **checkpoint(observations.get(app))}
            save()
            if upload:
                upload_capture(document, metadata, repository)
                document = promote(document)
            complete = document['complete'] and upload
            observations[app] = {**checkpoint(observations.get(app), success=complete, success_hours=1),
                                 'status': 'published' if complete else 'partial',
                                 'captured_total': document['captured_total'], 'gap_total': document['gap_total'],
                                 'pending_url_total': len(document['pending_urls'])}
            print(f"{app}: captured={document['captured_total']} gaps={document['gap_total']}")
        except Exception as error:
            observations[app] = {**checkpoint(observations.get(app)), 'status': 'failed', 'error': str(error)}
        save()
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=[*APPS, "all"], default="all")
    parser.add_argument("--capture-dir", type=Path, default=ROOT / "artifacts/web-captures")
    parser.add_argument("--receipt", type=Path, default=ROOT / "data/apps/web-app-archive.json")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--max-assets", type=int, default=500)
    parser.add_argument("--capture-id", help="fixed UTC capture ID for a resumable single-app run")
    parser.add_argument("--resume-metadata", type=Path,
                        help="upload a retained capture.json instead of crawling again")
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--probe", action="store_true", help="print lightweight JSON fingerprints only")
    args = parser.parse_args()
    if args.max_assets <= 0:
        parser.error('--max-assets must be positive')
    apps = list(APPS) if args.app == "all" else [args.app]
    if args.capture_id and len(apps) != 1:
        parser.error("--capture-id requires --app controller or --app pro")
    if args.resume_metadata:
        document = json.loads(args.resume_metadata.read_text(encoding="utf-8"))
        if args.app != document["app"]:
            parser.error("--app must match the retained capture metadata")
        if args.capture_id:
            document["capture_id"] = args.capture_id
            for item in document["resources"]:
                if item.get("file"):
                    item["release_asset"] = safe_asset_name(
                        args.capture_id, item["requested_url"], item["sha256"]
                    )
        bundle = document["bundle"]
        bundle["release_asset"] = (
            f"sonos-{document['app']}-{args.capture_id or document['capture_id']}--"
            f"{bundle['sha256']}--complete.tar.gz"
        )
        upload_capture(document, args.resume_metadata, args.repo)
        result = update_receipt(args.receipt, [document])
        print(f"{document['app']}: resumed captured={document['captured_total']} gaps={document['gap_total']}")
        return 1 if result['status'] == 'partial' else 0
    if args.probe:
        print(json.dumps({"schema_version": 1, "probes": [public_probe(app) for app in apps]}, indent=2))
        return 0
    result = archive_apps(apps, args.capture_dir, args.receipt, args.repo, max_assets=args.max_assets,
                          capture_id=args.capture_id, upload=args.upload)
    return 1 if result['status'] == 'partial' else 0


if __name__ == "__main__":
    raise SystemExit(main())
