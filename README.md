# Sonos Firmware Archive

A reproducible catalog of Sonos firmware packages, their container structure,
provenance, cryptographic hashes, and recovered raw images.

This repository follows the same preservation model as
[`irobot-firmware-archive`](https://github.com/BookCatKid/irobot-firmware-archive):
small, reviewable metadata and tooling live in Git; large proprietary binaries
live in GitHub Releases. The catalog never treats a nearby release as a
substitute for an exact missing version.

## Archive snapshot

As of 2026-09-01, the private archive contains:

- **389 preserved artifacts** across **47 exact version labels**;
- **354 Sonos UPD packages** with section-level manifests;
- **16 preserved update manifests** and **134 recovered/raw image components**
  from **38 packages**;
- **14.86 GiB** of unique cataloged package/installer/DFU bytes; and
- **71 explicit gaps**: 70 manifest candidates no longer on the CDN, plus the
  device-observed but still-unrecovered Move `96.0-79160` package.

Every cataloged Release asset is reconciled against GitHub's server-reported
byte size and SHA-256 digest.

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

The retired `2026-Sonos-16-oTC1hrxrvi-GA-1` campaign URL is also preserved as
evidence, but Sonos replaced its body in place with a `96.1-79270` manifest.
It is therefore labeled as a mutated campaign snapshot and is not represented
as the missing `96.0-79160` manifest.

Two additional retired campaign URLs recovered from local task history remain
live. Their exact current bodies preserve the S1 `57.22-68080` manifest and the
S2 `96.0-78270` manifest. They added 52 live packages across the `57.22` and
`96.0` lines; neither is treated as the still-missing Move `96.0-79160` build.

## Repository layout

- `config/models.json` — evidence-scoped package-family observations; numeric
  package models are not assumed to map one-to-one to retail products.
- `data/catalog.json` — package provenance, hashes, release assets, and raw-image records.
- `data/discovery/` — complete candidate/receipt pairs for safe metadata probes.
- `data/manifests/` — exact signed manifest snapshots retained in the repository.
- `schemas/` — validation rules for redacted evidence records.
- `data/completeness.json` — evidence-based target ledger, including unresolved gaps.
- `data/upd/` — section-level manifests generated from each preserved UPD.
- `data/filesystems/` — path, mode, size, symlink, and SHA-256 manifests for extracted root filesystems.
- `data/gpl/` — separately licensed Sonos-published GPL/LGPL index metadata and source captures, published through `gpl-<version>` GitHub releases.
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
sonos-fw extract /path/to/package.upd --directory /path/to/raw
sonos-fw extract /path/to/encrypted.upd --private-key /secure/model-key.pem \
  --directory /path/to/raw --receipt /path/to/raw-receipt.json
sonos-fw fs-manifest /path/to/rootfs-extracted --output manifest.json
```

Raw component assets use package-prefixed names and live in the same private
GitHub Release as their encrypted source OTA. Private keys are never committed
or uploaded; extraction receipts contain only source recipient fingerprints,
component sizes, and SHA-256 hashes.

The archive contains a compatibility mode for the legacy model-8 envelope used
by the Play:1 family. Its recovered key is deliberately retained only in local
ignored recovery storage; the checked-in tooling and receipts are sufficient to
verify, reproduce extraction when that key is supplied, and audit every raw
asset without exposing the key.

See [model-key handling](docs/key-material.md) for the verified key-family
fingerprints, provenance summary, and the archive's withheld-material policy.

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

## Release layout

Upload each exact UPD package to its matching `firmware-<system-version>`
Release. Keep `.upm` snapshots in `manifest-snapshots-<date>` Releases. Raw
extracted components remain beside the source OTA in their package's firmware
Release. Do not mix firmware packages into manifest snapshot Releases.

## Redacted evidence records

Diagnostic and announcement evidence may expose device identifiers or private
network context even when copied accidentally. Preserve only structured,
minimum-necessary firmware fields in `data/catalog.json` under the `evidence`
key. The validator rejects common identifier fields and requires an explicit
`redacted: true` flag. Full issue text, diagnostics, and raw comments are never
checked in.
