# Sonos Firmware Archive

Automatic discovery and preservation of public Sonos firmware, applications,
web deployments, and GPL sources. Git stores provenance and inventories;
GitHub Releases stores source binaries and extracted components.

[One scheduled workflow](.github/workflows/archive.yml) reads
[`config/targets.json`](config/targets.json). Each target supplies a discovery
adapter, cadence, metadata paths, and any required worker capabilities. There
is no initial download, hash inventory, or completed archive to prepare before
adding a target. Discovery starts from public protocols and indexes and resumes
from observations produced by earlier runs.

The workers also initialize an empty archive: prior `data/` records and GitHub
Releases are optional, and destination releases are created when first needed.
Existing captures supplement discovery and retries; they are not bootstrap
requirements. See [starting with no archive](docs/archive-platform.md#starting-with-no-archive).

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
sonos-archive targets
sonos-archive run firmware --plan
python scripts/run_tests.py
# After a worker has created a catalog:
sonos-fw verify
```

Planning is the default and performs no acquisition. `--execute` is reserved
for the GitHub Actions worker. It discovers candidates, downloads payloads into
worker temporary storage, computes hashes, verifies publication, records
inventories, and removes temporary payloads. No workstation backfill is required.

To request a run, choose **discover and archive** in Actions and enter a target
name or `all`. New targets automatically enter the scheduled matrix; they do
not need another workflow file. See [adding a target and worker behavior](docs/archive-platform.md).

## Targets

| Target | Discovery or preservation scope |
| --- | --- |
| `firmware` | Public update protocols, manifest graphs, historical public indexes, UPD extraction, available-key recovery, and inventories across models |
| `desktop` | Current redirects, WinGet, Homebrew history, and historical public indexes |
| `mobile` | Official Sonos-hosted Android APKs and historical captures, with signature and manifest inspection |
| `web` | Public controller and Pro deployments and their referenced assets |
| `apple-listings` | Public Apple listing metadata and history |
| `android-history` | APKPure version/architecture discovery, older selector history and signer-verified deliveries |
| `google-play-listings` | Public listing observations, independent of delivery acquisition |
| `release-audit` | Weekly reconciliation of recorded Release sizes and hashes |
| `gpl-indexes` | Manual refresh of observed GPL indexes |
| `google-play` | Anonymous delivery discovery across device profiles, with verified current and historical acquisition |
| `gpl` | Public site indexes and bounded version exploration |
| `gpl-archive` | Resumable acquisition and verified publication of original source archives |
| `gpl-audit` | Published compressed-stream integrity checks |

Discovery coverage is measured rather than assumed. Opaque historical paths,
removed files, access challenges, unavailable keys, and private store delivery
can leave gaps. Apple listing history is metadata preservation; it does not
imply possession of IPA binaries.

## Records and analysis

- [`data/catalog.json`](data/catalog.json): firmware provenance, hashes, source assets, and extracted components.
- [`data/discovery/`](data/discovery/): observed manifests, candidate checks, and resumable discovery state.
- [`data/apps/`](data/apps/), [`data/gpl/`](data/gpl/): application and GPL evidence and publication receipts.
- [`data/upd/`](data/upd/), [`data/raw/`](data/raw/), [`data/filesystems/`](data/filesystems/): section and filesystem inventories.
- `data/platform/targets/`: generated worker checkpoints once scheduled runs have executed.
- [ConnectX extraction](docs/connectx-anacapad.md), [discovery methods](docs/sonos-discovery-methods-research.md), and [dated archive results and specialist commands](docs/archive-reference.md).

`sonos-fw` provides offline inspection, extraction, filesystem inventories, and
catalog validation. Recovered private keys and private device captures stay out
of Git; see [recovery storage](docs/recovery-vault.md).

This is an independent archive. Sonos firmware and product names belong to
their respective owners. The MIT license covers this repository's original
code and tooling.
