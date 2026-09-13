# Sonos Firmware Archive

A reproducible catalog of Sonos firmware packages, their container structure,
provenance, cryptographic hashes, and recovered raw images.

This repository follows the same preservation model as
[`irobot-firmware-archive`](https://github.com/BookCatKid/irobot-firmware-archive):
small, reviewable metadata and tooling live in Git; large proprietary binaries
live in GitHub Releases. The catalog never treats a nearby release as a
substitute for an exact missing version.

## Archive snapshot

As of 2026-09-10, the private archive contains:

- **454 preserved artifacts** across **50 exact version labels**;
- **413 Sonos UPD packages** with section-level manifests;
- **19 preserved update manifests** and **286 recovered/raw image components**;
- **16.22 GiB** of unique cataloged package/installer/DFU bytes; and
- **169 explicit gaps**: 88 signed-manifest candidates and 80 bounded
  base-URI model expansions no longer on the CDN, plus the device-observed but
  still-unrecovered Move `96.0-79160` package.

Every cataloged Release asset is reconciled against GitHub's server-reported
byte size and SHA-256 digest.

On 2026-09-10, a rate-limited sweep of 1,200 synthetic metadata profiles found
three byte-distinct UPS responses. Most profiles returned the already archived
S1 manifest. Low/invalid controller profiles additionally exposed two
previously uncataloged historical base URIs: `54.2-72160` StubInstaller and
`10.20-75300` in the `Gold/Kanye-v2.8` channel. A bounded model 0–80 expansion
found one surviving package, official model-27
`10.20-75300-1-27.upd` (4,745,434 bytes; SHA-256
`296b1c892fe92ee1fbf73cba2b6566d3c0d7a58d08e32004d44d3b14c82fd07b`).
All ten UPD sections are plaintext, including its Linux kernel and little-endian
CramFS root filesystem, so no private key was needed. The exact package is
preserved in the `firmware-10.20-75300` Release; the complete redacted sweep,
raw unique UPS bodies, and model probes are retained under `data/metadata/`
and `data/discovery/`.

Every named component that is plaintext—or decryptable with a recovered
key—is also retained as its own Release asset. An archive-wide enforcement pass
on 2026-09-10 uploaded the three model-27 components plus two older plaintext
kernels that were embedded beside encrypted sections in models 13 and 20.
Repository validation now rejects any extractable UPD section without a
cataloged Release location, and the scheduled monitor reconciles every
canonical asset against GitHub's server-reported byte count and SHA-256. See
[`data/decryption-runs/2026-09-10-extractable-section-backfill.json`](data/decryption-runs/2026-09-10-extractable-section-backfill.json)
and
[`data/discovery/release-asset-audit-2026-09-10.json`](data/discovery/release-asset-audit-2026-09-10.json).

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

On 2026-09-08, extraction runs decrypted and uploaded the model-8 and model-9
payloads for `57.14-37030`, `57.22-59130`, and `67.1-27100`. Before upload,
their GitHub Releases contained the encrypted `.upd` files but no matching
package-prefixed plaintext assets. The kernel and rootfs hashes were absent
from the repository's Release asset digests; generic preinstall/FPGA payloads
are explicitly recorded as reused hashes. GitHub's server-reported sizes and
SHA-256 values were verified, then the local binary inputs/outputs were
removed. The receipts and release-asset audits are recorded in
[`data/decryption-runs/2026-09-08-new-model8-rootfs.json`](data/decryption-runs/2026-09-08-new-model8-rootfs.json)
and
[`data/decryption-runs/2026-09-08-new-model9-rootfs.json`](data/decryption-runs/2026-09-08-new-model9-rootfs.json);
only metadata and hashes remain locally.

The same research run then recovered previously unavailable model-16 and
model-17 RSA keys from encrypted wrappers embedded in their plaintext
`34.16-37101` updater binaries. Those keys exactly matched the missing OTA
recipient fingerprints and decrypted all 21 preserved packages for the two
families. The resulting 63 components were hash-verified, uploaded to 17
matching GitHub Releases, reconciled against the server, and removed locally.
The complete receipt is
[`data/decryption-runs/2026-09-08-new-model16-model17.json`](data/decryption-runs/2026-09-08-new-model16-model17.json).

On 2026-09-09, the model-12 updater yielded a fifth previously unavailable
RSA key after a complete 16-bit system-word search identified the unique value
`0x0000`. It decrypted all 16 preserved encrypted packages for that recipient,
producing 48 components. A preserved preflight proves those exact filenames
were absent before upload; all 48 server assets were then size/hash reconciled.
See
[`data/decryption-runs/2026-09-09-new-model12.json`](data/decryption-runs/2026-09-09-new-model12.json).

The same run recovered a sixth previously unavailable key from the Renesas SH
model-1 updater. Static analysis established its native little-endian seed
layout, and the exhaustive system-word search selected only `0x0000`. The key
matches all four preserved encrypted model-1 packages; their 12 plaintext
components were uploaded and server-hash reconciled. See
[`data/decryption-runs/2026-09-09-new-model1.json`](data/decryption-runs/2026-09-09-new-model1.json).

On 2026-09-12, an authorized speaker's redacted update-check response exposed
the signed `97.1-80312` manifest and its otherwise opaque release directory.
The archive preserved 23 current `97.1-80312` UPDs, eight `86.10-80260`
compatibility UPDs, and one model-53 accessory image. Recovered model-8, -9,
-12, and -17 keys extracted 13 components from the compatibility packages;
all source and extracted assets were reconciled against GitHub's server-side
sizes and SHA-256 digests. The raw extraction receipt is
[`data/decryption-runs/2026-09-12-86.10-80260.json`](data/decryption-runs/2026-09-12-86.10-80260.json).

The next preserved plaintext gaps are tracked in
[`data/decryption-runs/2026-09-08-next-model13-model25-targets.json`](data/decryption-runs/2026-09-08-next-model13-model25-targets.json).
They require model-specific key recovery; no large source UPD is downloaded
until matching key material is available.

Two additional retired campaign URLs recovered from local task history remain
live. Their exact current bodies preserve the S1 `57.22-68080` manifest and the
S2 `96.0-78270` manifest. They added 52 live packages across the `57.22` and
`96.0` lines; neither is treated as the still-missing Move `96.0-79160` build.

## Repository layout

- `config/models.json` — evidence-scoped package-family observations; numeric
  package models are not assumed to map one-to-one to retail products.
- `data/catalog.json` — package provenance, hashes, release assets, and raw-image records.
- `data/discovery/` — complete candidate/receipt pairs for safe metadata probes.
- `data/metadata/` — exact synthetic `.ups` response snapshots and redacted parsed receipts.
- `data/evidence/` — minimal first-party version/date evidence import records.
- `data/manifests/` — exact signed manifest snapshots retained in the repository.
- `schemas/` — validation rules for redacted evidence records.
- `data/completeness.json` — evidence-based target ledger, including unresolved gaps.
- `data/upd/` — section-level manifests generated from each preserved UPD.
- `data/raw/` — extracted-component receipts with recipient fingerprints and hashes.
- `data/decryption-runs/` — tracked receipts for successful local decryption runs.
- `data/filesystems/` — path, mode, size, symlink, and SHA-256 manifests for extracted root filesystems.
- `data/gpl/` — separately licensed Sonos-published GPL/LGPL index metadata and source captures, published through `gpl-<version>` GitHub releases.
- `data/key-recovery-ledger.json` — UPD envelope recipient coverage and exact packages still blocked by missing model keys.
- `src/sonos_firmware/` — read-only UPD parser and catalog tools.
- `site/` — compact searchable browser; serve the repository root locally.
- `tests/` — parser and catalog tests.

Recovered private keys, device captures, serial numbers, household identifiers,
and room names are deliberately excluded.

An optional local `recovery-work/` vault retains private keys, their exact
historical updater inputs, recovery intermediates, and a hash inventory while
remaining Git-ignored. See [local recovery vault](docs/recovery-vault.md) and
run `python scripts/audit_recovery_vault.py` to verify it without exposing key
material.

## Use the tools

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .

sonos-fw verify
sonos-fw metadata --raw data/metadata/default-1-1-YYYY-MM-DD.ups \
  --receipt data/metadata/default-1-1-YYYY-MM-DD.json
sonos-fw inspect /path/to/package.upd
sonos-fw extract /path/to/package.upd --directory /path/to/raw
sonos-fw extract /path/to/encrypted.upd --private-key /secure/model-key.pem \
  --directory /path/to/raw --receipt /path/to/raw-receipt.json
sonos-fw fs-manifest /path/to/rootfs-extracted --output manifest.json

# Inspect a device manufacturing-page capture (no secrets are printed)
sonos-fw mdp-inspect /secure/device-mdp.bin
# The offline path applies only to the documented Amlogic MDP3/OTP layout.
sonos-fw recover-amlogic-mdp-key /secure/device-mdp.bin \
  --otp /secure/device-otp.bin --output /secure/model-key.pem \
  --expect-model 26 \
  --expect-recipient RECIPIENT_SHA1
sonos-fw key-id /secure/model-key.pem

# Recover a documented legacy updater wrapper (models 1/8/9/12/16/17).
sonos-fw recover-legacy-updater-key /path/to/upgrade \
  --model 16 --output /secure/model16-private.pem \
  --expect-recipient fd88f2642a9c89a44747c7b4342cdb483fe1d437

# Model 1's Renesas SH updater uses native little-endian MDP fields.
sonos-fw recover-legacy-updater-key /path/to/model1/upgrade \
  --model 1 --system-word 0 --byte-order little --wrapper-offset 0xe528 \
  --output /secure/model1-private.pem \
  --expect-recipient 21e7b8c8199c8d6442d2a0a7a4e776c4efd4319c

# Model 5 needs the exact first 16 KiB of /dev/mtd/0 from an authorized unit.
sonos-fw recover-legacy-updater-key /path/to/model5/upgrade \
  --model 5 --byte-order big --wrapper-offset 0x16880 \
  --mtd-prefix /secure/model5-mtd0-first-16k.bin \
  --output /secure/model5-private.pem --expect-recipient RECIPIENT_SHA1
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
For the blocked Encore/model-13, Royale/model-20, Dhuez/model-25, and
Tupelo/model-26 families, see the
[model-13 recovery note](docs/model13-recovery.md),
[model-20 recovery note](docs/model20-recovery.md),
[model-25 recovery note](docs/model25-recovery.md), and
[model-26 recovery note](docs/model26-recovery.md).

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

### Completeness boundary and monitoring

There is no authoritative public Sonos index of every historical build, so
this archive cannot prove that it contains every version ever released. Its
defensible claim is narrower: every version and artifact exposed by the
currently recorded signed manifests, live metadata responses, exact public
diagnostic URLs, selected first-party announcements, public repository leads,
and queried Wayback CDX results is either preserved or represented by an
explicit gap. `data/catalog.json`, `data/completeness.json`, and the discovery
receipts make that boundary auditable rather than silently treating unknown
history as complete.

No Codex heartbeat or AI service is used. The repository-native
`monitor firmware sources` GitHub Actions workflow runs daily and can also be
started manually. It fetches the public synthetic update-metadata profile,
parses every referenced signed manifest, probes its candidates, and rechecks
every exact catalog URL marked `missing-cdn`. It also extracts build numbers
from Sonos's first-party system release-notes page, which provides early
warning when the opaque manifest URL is not publicly discoverable. It uploads
a JSON audit and opens or updates one GitHub issue only when it sees an unknown
manifest, hash, version, downloadable uncataloged candidate, or revived missing
URL. Monitoring errors fail the workflow instead of being interpreted as
absence.

This proves a useful but bounded statement about current availability: all
artifacts currently downloadable through that public profile and the catalog's
known exact URLs are accounted for when the latest audit is green. It cannot
enumerate opaque URLs that no public source references, private/device-specific
update profiles, or every historical release. The `97.1-80312` capture is a
concrete example: release notes exposed the version, while a real authorized
speaker exposed the signed manifest URL and per-model download namespace.
Run the identical public check locally with:

```bash
python scripts/check_new_versions.py --output monitor-report.json
```

The higher-volume metadata-profile sweep is intentionally manual and
rate-limited rather than scheduled daily. It checkpoints progress and stores
only redacted numeric profiles plus unique response bodies:

```bash
python scripts/sweep_metadata_profiles.py \
  --output data/discovery/metadata-profile-sweep-YYYY-MM-DD.json \
  --raw-directory data/metadata/sweep-YYYY-MM-DD
```

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

Recheck every current miss without downloading firmware, and independently
check its exact opaque release directory in Wayback CDX:

```bash
python scripts/audit_missing_live.py \
  --output data/discovery/missing-live-audit-YYYY-MM-DD.json
python scripts/audit_missing_wayback.py \
  --output data/discovery/missing-wayback-audit-YYYY-MM-DD.json
```

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
