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
import subprocess
import tarfile
import tempfile
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
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
URL_RE = re.compile(r'''(?:(?:src|href)=["']([^"']+)|["'(]((?:https?://[^"')\\ ]+|/(?:_next/static|assets|fonts)/[^"')\\ ]+)))["') ]?''')


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


def discover_urls(base_url: str, body: bytes) -> set[str]:
    text = body.decode("utf-8", errors="replace")
    found = set()
    for match in URL_RE.finditer(text):
        raw = (match.group(1) or match.group(2) or "").replace("\\u0026", "&")
        if not raw or raw.startswith(("data:", "javascript:", "mailto:", "#")):
            continue
        found.add(urllib.parse.urljoin(base_url, raw))
    # Next.js manifests often contain escaped route chunks outside attributes.
    for raw in re.findall(r"/_next/static/[A-Za-z0-9_./%?=&+~@\[\]-]+", text):
        found.add(urllib.parse.urljoin(base_url, raw))
    # Next.js build manifests omit the /_next/ prefix for route chunks.
    for raw in re.findall(r'''["'](static/(?:chunks|css|media)/[^"']+)["']''', text):
        if "${" not in raw:
            found.add(urllib.parse.urljoin(base_url, f"/_next/{raw}"))
    # Web manifests use JSON src fields; stylesheets use url(...).
    for raw in re.findall(
        r'''(?:["'](?:src|href)["']\s*:\s*["']|url\(\s*["']?)([^"'()\s]+)''',
        text,
    ):
        if "${" not in raw and not raw.startswith("data:"):
            found.add(urllib.parse.urljoin(base_url, raw))
    found = {url for url in found if "${" not in url}
    return found


def allowed_asset(url: str, host: str) -> bool:
    parsed = urllib.parse.urlsplit(url)
    if parsed.netloc != host:
        return False
    path = parsed.path
    if path in {"/manifest.webmanifest", "/favicon.ico", "/robots.txt"}:
        return True
    if not path.startswith(STATIC_PREFIXES):
        return False
    return bool(re.search(
        r"\.(?:js|css|json|map|woff2?|ttf|otf|png|jpe?g|gif|svg|webp|avif|ico)$",
        path, re.IGNORECASE,
    ))


def safe_asset_name(capture_id: str, url: str, digest: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    basename = Path(parsed.path).name or "index.html"
    stem, suffix = os.path.splitext(basename)
    if not suffix:
        suffix = mimetypes.guess_extension("application/octet-stream") or ".bin"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", stem)[:90]
    return f"{capture_id}--{stem}--{digest[:12]}{suffix.lower()}"


def classify(result: dict) -> str:
    requested = urllib.parse.urlsplit(result["requested_url"])
    final = urllib.parse.urlsplit(result["final_url"])
    expected_static = requested.path.startswith(STATIC_PREFIXES)
    content_type = result["headers"].get("content-type", "").lower()
    if result["status"] != 200:
        return "http-error"
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


def capture(app: str, destination: Path, max_assets: int = 500) -> tuple[dict, Path]:
    config = APPS[app]
    capture_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    capture_dir = destination / app / capture_id
    bodies_dir = capture_dir / "bodies"
    bodies_dir.mkdir(parents=True, exist_ok=False)
    host = urllib.parse.urlsplit(config["entry_url"])[1]
    queue = [config["entry_url"], *config["seed_urls"]]
    seen: set[str] = set()
    resources: list[dict] = []
    while queue and len(seen) < max_assets:
        url = queue.pop(0)
        if url in seen:
            continue
        seen.add(url)
        result = fetch(url)
        digest = sha256(result["body"])
        outcome = classify(result)
        record = {key: result[key] for key in (
            "requested_url", "final_url", "status", "redirects", "headers"
        )}
        record.update({"outcome": outcome, "bytes": len(result["body"]), "sha256": digest})
        if outcome == "captured":
            name = safe_asset_name(capture_id, url, digest)
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
    } for item in resources if item["requested_url"] == config["entry_url"]
       or item["requested_url"] in config["seed_urls"]
       or "_buildManifest.js" in item["requested_url"]
       or "_ssgManifest.js" in item["requested_url"]]
    probe_fingerprint = sha256(json.dumps(probe_items, sort_keys=True).encode())
    # Identity comes from the small stable public probe. Crawler improvements
    # must enrich/replace a deployment, not invent a new Sonos deployment.
    deployment_fingerprint = probe_fingerprint
    document = {
        "schema_version": 1,
        "app": app,
        "entry_url": config["entry_url"],
        "captured_at": datetime.now(timezone.utc).isoformat(),
        "capture_id": capture_id,
        "probe_fingerprint": probe_fingerprint,
        "deployment_fingerprint": deployment_fingerprint,
        "resource_total": len(resources),
        "captured_total": sum(item["outcome"] == "captured" for item in resources),
        "gap_total": sum(item["outcome"] != "captured" for item in resources),
        "release_tag": config["release_tag"],
        "resources": resources,
        "boundary": "Public deployment bytes only; no login session or cloud API data.",
    }
    metadata = capture_dir / "capture.json"
    metadata.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    bundle = capture_dir.with_suffix(".tar.gz")
    with tarfile.open(bundle, "w:gz") as archive:
        archive.add(capture_dir, arcname=f"sonos-{app}-{capture_id}")
    document["bundle"] = {
        "file": str(bundle), "bytes": bundle.stat().st_size,
        "sha256": sha256(bundle.read_bytes()),
        "release_asset": f"sonos-{app}-{capture_id}--complete.tar.gz",
    }
    metadata.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")
    return document, metadata


def ensure_release(repository: str, tag: str, title: str) -> None:
    exists = subprocess.run(
        ["gh", "release", "view", tag, "--repo", repository],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    ).returncode == 0
    if not exists:
        subprocess.run([
            "gh", "release", "create", tag, "--repo", repository,
            "--title", title, "--notes",
            "Account-free deployment snapshots. Every public response body is uploaded "
            "individually and in a reconstruction bundle; authentication/cloud APIs are excluded.",
        ], check=True)


def upload_capture(document: dict, metadata: Path, repository: str) -> None:
    config = APPS[document["app"]]
    ensure_release(repository, config["release_tag"], config["title"])
    capture_dir = metadata.parent
    paths = [capture_dir / item["file"] for item in document["resources"] if item.get("file")]
    bundle = Path(document["bundle"]["file"])
    renamed_bundle = bundle.with_name(document["bundle"]["release_asset"])
    if renamed_bundle != bundle:
        bundle.rename(renamed_bundle)
        document["bundle"]["file"] = str(renamed_bundle)
    paths.append(renamed_bundle)
    subprocess.run([
        "gh", "release", "upload", config["release_tag"], "--repo", repository,
        "--clobber", *map(str, paths),
    ], check=True)
    release = json.loads(subprocess.check_output([
        "gh", "api", f"repos/{repository}/releases/tags/{config['release_tag']}",
    ], text=True))
    pages = json.loads(subprocess.check_output([
        "gh", "api", "--paginate", "--slurp",
        f"repos/{repository}/releases/{release['id']}/assets?per_page=100",
    ], text=True))
    observed = {item["name"]: item for page in pages for item in page}
    expected = {
        item["release_asset"]: (item["bytes"], item["sha256"])
        for item in document["resources"] if item.get("release_asset")
    }
    expected[document["bundle"]["release_asset"]] = (
        document["bundle"]["bytes"], document["bundle"]["sha256"]
    )
    for name, (size, digest) in expected.items():
        remote = observed.get(name)
        remote_digest = ((remote or {}).get("digest") or "").removeprefix("sha256:")
        if remote is None or remote["size"] != size or remote_digest != digest:
            raise RuntimeError(f"GitHub asset verification failed: {config['release_tag']}/{name}")
    base = f"https://github.com/{repository}/releases/download/{config['release_tag']}"
    for item in document["resources"]:
        if item.get("release_asset"):
            item["release_tag"] = config["release_tag"]
            item["release_url"] = f"{base}/{urllib.parse.quote(item['release_asset'])}"
    document["bundle"]["release_tag"] = config["release_tag"]
    document["bundle"]["release_url"] = f"{base}/{urllib.parse.quote(document['bundle']['release_asset'])}"
    metadata.write_text(json.dumps(document, indent=2) + "\n", encoding="utf-8")


def update_receipt(path: Path, deployments: list[dict]) -> None:
    existing = {"schema_version": 1, "deployments": []}
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
    by_identity = {
        (item["app"], item["deployment_fingerprint"]): item
        for item in existing["deployments"]
    }
    for deployment in deployments:
        key = (deployment["app"], deployment["deployment_fingerprint"])
        prior = by_identity.get(key)
        if prior is None or deployment["captured_total"] >= prior["captured_total"]:
            by_identity[key] = deployment
    existing["deployments"] = sorted(
        by_identity.values(), key=lambda item: (item["app"], item["captured_at"])
    )
    existing["updated"] = datetime.now(timezone.utc).isoformat()
    existing["deployment_total"] = len(existing["deployments"])
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(existing, indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--app", choices=[*APPS, "all"], default="all")
    parser.add_argument("--capture-dir", type=Path, default=ROOT / "artifacts/web-captures")
    parser.add_argument("--receipt", type=Path, default=ROOT / "data/apps/web-app-archive.json")
    parser.add_argument("--repo", default="BookCatKid/sonos-firmware-archive")
    parser.add_argument("--max-assets", type=int, default=500)
    parser.add_argument("--upload", action="store_true")
    parser.add_argument("--probe", action="store_true", help="print lightweight JSON fingerprints only")
    args = parser.parse_args()
    apps = list(APPS) if args.app == "all" else [args.app]
    if args.probe:
        print(json.dumps({"schema_version": 1, "probes": [public_probe(app) for app in apps]}, indent=2))
        return 0
    captured = []
    for app in apps:
        document, metadata = capture(app, args.capture_dir, args.max_assets)
        if args.upload:
            upload_capture(document, metadata, args.repo)
        captured.append(document)
        print(f"{app}: captured={document['captured_total']} gaps={document['gap_total']}")
    update_receipt(args.receipt, captured)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
