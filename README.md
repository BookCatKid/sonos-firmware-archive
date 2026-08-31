# Sonos Firmware Archive

A reproducible catalog of Sonos firmware packages, their container structure,
provenance, cryptographic hashes, and recovered raw images.

This repository follows the same preservation model as
[`irobot-firmware-archive`](https://github.com/BookCatKid/irobot-firmware-archive):
small, reviewable metadata and tooling live in Git; large proprietary binaries
live in GitHub Releases. The catalog never treats a nearby release as a
substitute for an exact missing version.

## Archive snapshot

As of 2026-08-31, the private archive contains:

- **308 preserved artifacts** across **38 exact version labels**;
- **275 Sonos UPD packages** with section-level manifests;
- **11 preserved update manifests** and **18 recovered/raw image components**;
- **11.34 GiB** of unique cataloged package/installer/DFU bytes; and
- **46 explicit gaps**: 45 manifest candidates no longer on the CDN, plus the
  device-observed but still-unrecovered Move `96.0-79160` package.

The GitHub Releases currently contain 344 assets (11.57 GiB including seed
duplicates). Every cataloged Release asset has been reconciled against
GitHub's server-reported byte size and SHA-256 digest.

> [!IMPORTANT]
> This is an independent research archive, not a Sonos project. Firmware and
> product names remain the property of their respective owners. The MIT license
> covers only the original catalog and tooling in this repository.

## Current coverage

| Product | Model | Target | OTA package | Raw kernel/rootfs |
|---|---:|---:|---|---|
| Play:1 | S1 / image 8 | 86.8-78270 | Preserved | Complete |
| Playbar | S9 / image 9 | 86.8-78270 | Preserved | Complete |
| Play:5 (Gen 2) | S6 / image 13 | 86.8-78270 | Preserved | Blocked on model-13 key material |
| Move | S17 / image 25 | **96.0-79160** | **Missing exact package** | Missing |

The preserved Move `96.1-79270` OTA is cataloged as a reference artifact only.
It is explicitly not considered completion of the `96.0-79160` target.

## Repository layout

- `config/models.json` — evidence-scoped package-family observations; numeric
  package models are not assumed to map one-to-one to retail products.
- `data/catalog.json` — package provenance, hashes, release assets, and raw-image records.
- `data/completeness.json` — evidence-based target ledger, including unresolved gaps.
- `data/upd/` — section-level manifests generated from each preserved UPD.
- `data/filesystems/` — path, mode, size, symlink, and SHA-256 manifests for extracted root filesystems.
- `src/sonos_firmware/` — read-only UPD parser and catalog tools.
- `site/` — compact searchable browser; serve the repository root locally.
- `tests/` — parser and catalog tests.

Recovered private keys, device captures, serial numbers, household identifiers,
and room names are deliberately excluded.

## Use the tools

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .

sonos-fw verify
sonos-fw inspect /path/to/package.upd
sonos-fw fs-manifest /path/to/rootfs-extracted --output manifest.json
```

Regenerate checked-in metadata from a local artifact directory:

```bash
python scripts/generate_metadata.py /path/to/sonos-firmware-downloads
```

To browse the catalog directly (including from a `file://` URL):

```bash
python scripts/build_site.py
open site/index.html
```

## Integrity and safety

The tooling is read-only: it parses, hashes, and inventories files. It contains
no flashing or update-sending path. Verify every downloaded Release asset
against `data/catalog.json` before analysis.

The archive records observable evidence and does not claim that the current
seed is a complete history of all Sonos firmware.

## Expand the archive

The discovery pipeline expands every concrete image and placeholder URL in a
signed Sonos update manifest, adds the default package-model set, probes each
candidate, downloads live artifacts atomically, and records a hash receipt:

```bash
sonos-fw discover update.upm --output data/discovery/current.json
sonos-fw fetch data/discovery/current.json \
  --directory artifacts/discovered \
  --receipt data/discovery/current-receipt.json
python scripts/import_discovery.py \
  data/discovery/current.json \
  data/discovery/current-receipt.json \
  artifacts/discovered
```

Discovery misses remain in the catalog as `missing-cdn`; they are not erased.
