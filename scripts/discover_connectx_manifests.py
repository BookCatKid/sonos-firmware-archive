#!/usr/bin/env python3
"""Track model-17 URLs in the public historical Sonos manifest collection."""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import urllib.request
from pathlib import Path
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
sys.path.insert(0, str(ROOT / 'scripts'))
from sonos_firmware.discovery import USER_AGENT, official_firmware_url, parse_manifest, write_json
from archive_monitor_firmware import ensure_release, upload_small_verified

REPOSITORY = 'ReverseTelnet/sonos_update_manifests'


def github(path):
    return json.loads(subprocess.check_output(['gh', 'api', f'repos/{REPOSITORY}/{path}'.rstrip('/')], text=True))


def fetch(url):
    with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=30) as response:
        data = response.read(262145)
    if len(data) > 262144:
        raise ValueError('manifest exceeds 256 KiB')
    return data


def discover(output: Path, work: Path):
    branch = github('')['default_branch']
    commit = github('commits/' + quote(branch, safe=''))['sha']
    previous = json.loads(output.read_text()) if output.exists() else {}
    if previous.get('commit') == commit:
        return previous
    tree = github(f'git/trees/{commit}?recursive=1')
    if tree.get('truncated'):
        raise ValueError('incomplete manifest repository tree')
    files = [f for f in tree['tree'] if f['type'] == 'blob' and f['path'].startswith('manifest_files/') and f['path'].endswith('.upm')]
    if not files or len(files) > 2000:
        raise ValueError('unexpected historical manifest count')
    work.mkdir(parents=True, exist_ok=True)
    cached = {(m['path'], m['github_blob_sha']): m for m in previous.get('manifests', [])}
    manifests = []
    candidates = {}
    for item in files:
        path = item['path']
        old = cached.pop((path, item['sha']), None)
        if old:
            record = old
        else:
            url = f'https://raw.githubusercontent.com/{REPOSITORY}/{commit}/{quote(path, safe="/")}'
            data = fetch(url)
            blob = hashlib.sha1(b'blob ' + str(len(data)).encode() + b'\0' + data).hexdigest()
            if blob != item['sha']:
                raise ValueError(f'Git blob hash mismatch: {path}')
            local = work / item['sha']
            local.write_bytes(data)
            metadata, parsed = parse_manifest(local, source_url=url)
            rows = [c.__dict__ for c in parsed if c.package_model == 17 and official_firmware_url(c.url) and c.filename.endswith('.upd')]
            record = {'path': path, 'url': url, 'github_blob_sha': blob,
                      'sha256': hashlib.sha256(data).hexdigest(), 'bytes': len(data),
                      'warnings': metadata['warnings'], 'candidates': rows}
        manifests.append(record)
    manifests.extend(cached.values())
    for record in manifests:
        for candidate in record['candidates']:
            row = candidates.setdefault(candidate['url'], {**candidate, 'evidence': []})
            row['evidence'].append({'url': record['url'], 'sha256': record['sha256']})
    result = {'schema_version': 1, 'repository': REPOSITORY, 'commit': commit,
              'manifests': manifests, 'candidates': list(candidates.values())}
    write_json(output, result)
    return result


def publish_collection(result, output, work, repository):
    """Keep exact upstream manifest bodies alongside their discovery receipts."""
    pending = [m for m in result['manifests'] if not m.get('release_asset')]
    if not pending:
        return
    prepared = set()
    work.mkdir(parents=True, exist_ok=True)
    for index, record in enumerate(result['manifests']):
        if record.get('release_asset'):
            continue
        tag = f"manifest-snapshots-connectx-{result['commit'][:12]}-{index // 1000 + 1}"
        if tag not in prepared:
            ensure_release(repository, tag, 'Historical ConnectX update manifests',
                           'Byte-preserved historical Sonos manifests; pinned source URLs and hashes are recorded in the ConnectX collection receipt.')
            prepared.add(tag)
        cached = work / record['github_blob_sha']
        data = cached.read_bytes() if cached.exists() else fetch(record['url'])
        if len(data) != record['bytes'] or hashlib.sha256(data).hexdigest() != record['sha256']:
            raise ValueError('historical manifest body hash mismatch')
        filename = Path(record['path']).stem + '-' + record['sha256'][:12] + '.upm'
        asset = work / filename
        asset.write_bytes(data)
        upload_small_verified(repository, tag, asset, record['sha256'])
        record.update(release_tag=tag, release_asset=filename)
        write_json(output, result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'data/discovery/connectx-collection.json')
    parser.add_argument('--work-dir', type=Path, required=True)
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--repository', default='BookCatKid/sonos-firmware-archive')
    args = parser.parse_args()
    result = discover(args.output, args.work_dir)
    if args.publish:
        publish_collection(result, args.output, args.work_dir, args.repository)
    print(f"historical manifests={len(result['manifests'])} model17 URLs={len(result['candidates'])} commit={result['commit']}")


if __name__ == '__main__':
    main()
