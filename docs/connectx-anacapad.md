# Complete ConnectX firmware archive

[Issue #24](https://github.com/BookCatKid/sonos-firmware-archive/issues/24)
identified an additional official distribution channel:

<https://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd>

ConnectX is package model 17. The archive covers this mutable static URL,
known versioned model-17 packages, researched historical URLs, and literal model-17 filenames in evidenced
Sonos firmware directories. These are distinct sources: a static URL can be
replaced, and the same package can appear in multiple campaigns.

## Automated discovery and preservation

[The shared discovery workflow](../.github/workflows/archive.yml) runs every
six hours and supports manual dispatch. It starts from anonymous S1/S2 Sonos
protocol endpoints and the legacy UPS response, follows canonical response
locations, manifest base/image links and literal package-directory siblings,
and explores inferred Prod/Dev counterparts. It discovers every referenced
model and artifact type, rather than filtering the crawler to ConnectX.
Public archive-index searches supply historical paths when indexed copies exist.

The scanner saves its own frontier in `data/discovery/firmware-frontier.json`.
Pending work resumes across runs; failed sources, dated negatives and host
challenges remain explicit. A failure on one host does not block other hosts.
Successfully discovered packages and captured manifest bodies are published
independently, with verification before catalog registration.

Available model keys then unlock preserved firmware through the shared raw
extraction pipeline. The ConnectX step additionally inventories every section,
unpacks application archives and root filesystems, and retains complete bundles.
Its static recovery endpoint is checked by full-file hash every run. Snapshot
identity includes the parsed version and SHA-256, so replacements do not overwrite
older observations. Four complete ConnectX snapshots are processed per run.

Each snapshot publishes the exact source UPD, a reproducible extraction
bundle, and a section/file manifest. New catalog entries and individual
payload locations are registered only after release uploads pass size and
SHA-256 verification. Existing preserved packages keep their catalog identity;
the complete extraction bundle supplies their additional section inventories.

The source-to-hash index is [`data/discovery/connectx-index.json`](../data/discovery/connectx-index.json).
Section manifests use `data/upd/`, and extraction receipts use `data/raw/`.
The receipts retain decryption results, tar members, unpacked kernel hashes,
and links to filesystem inventories in `data/filesystems/`.
`data/discovery/connectx-scan.json` records acquisition errors.

The [2026-11-13 manifest-coverage audit](../data/discovery/manifest-coverage-2026-11-13.json)
verified all 68 preserved source manifests against their recorded SHA-256s.
Their 1,199 distinct artifact URL references include 864 matching preserved
artifacts and 335 without a preserved match. All 326 unpreserved UPD references
and five installer references returned HTTP 404; the remaining four point to
HTML/TestFlight pages rather than firmware packages. These counts describe
URLs, including aliases and model-list expansion, rather than distinct builds.

All 30 existing model-17 source UPDs were independently verified against
GitHub's current asset sizes and SHA-256s. Five additional exact model-17 URLs
from the preserved manifests also returned 404 and are included in the retry
candidates, bringing the merged ConnectX discovery set to 71 URLs. A manifest
reference or unavailable model-list expansion does not establish that a
corresponding downloadable firmware build ever existed.

A [separate external manifest collection](connectx-additional-research.md)
subsequently supplied 24 additional live versions. Those have all been
decrypted and inventoried, bringing the extraction set to 55 snapshots and
the historical research set to 122 official URLs. Those receipts are evidence,
not the runtime discovery input. Independent discovery is measured separately
from imported historical observations.

The scheduled `firmware` target now handles this endpoint through the shared
discovery, preservation, key-recovery and inventory worker. Its protocol root
is declared in `config/targets.json`; captured versions come from the body.
The worker also inventories all other preserved UPDs, rather than maintaining
a separate ConnectX downloader. To inspect its plan without acquisition:

```sh
sonos-archive run firmware --plan
```

Choose the `firmware` target in Actions to execute it. Public inventory reports
and extraction bundles are recorded in the catalog only after size/hash
verification. [Discovery methods and limits](sonos-discovery-methods-research.md)
distinguish cold discovery from resumed archive observations.

For optional analysis of an existing file, `sonos-fw inventory-upd FILE
--directory OUTPUT` uses the same analyzer. `--private-key` supplies a key,
`--store-sections` also carves stored ciphertext bytes, and `--bundle` writes
a reproducible extraction archive. These are analysis options, not setup steps
for the scheduled worker.

## Everything extracted

Every section is retained byte for byte, including descriptors, signatures,
capabilities, and unknown section types. Encrypted sections are also decrypted
when a matching key is available. The stored and decrypted forms have separate
hashes. Unknown-key sections remain explicitly marked `blocked-key`.

Type-23 application archives are unpacked completely, with hashes and modes
for every regular member. Symlinks and hardlinks are recorded as metadata;
they are not followed during tar extraction. Absolute/traversing paths and
duplicate member names are rejected. SquashFS images, including historical
big-endian images, are unpacked and inventoried by path, mode, size, hash, and
symlink target. Gzip-compressed uImage kernel data and gzip streams inside historical PowerPC
zImage boot wrappers are decompressed separately.
Firmware programs and preinstall scripts are never executed.

A single-executable convenience command is also available:

```sh
sonos-fw extract-anacapad /path/to/connectx.upd \
  --directory /path/to/raw --receipt /path/to/anacapad-receipt.json
```

## Key recovery and verified decryption

The static package uses recipient
`f2acc70d8db3ee388c534d99d4bff64d9dab8154`, the existing model-17 family key.
Its RSA-2048 private key is reproducible with the repository's legacy recovery
implementation from `/bin/upgrade` in `34.16-37101-1-17`.

The automated fallback downloads the rootfs from
[firmware-34.16-37101](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/firmware-34.16-37101),
checks its catalog hash, reads the updater using `unsquashfs -cat`, and runs
`recover_legacy_updater_key(updater, 17)`. It verifies the resulting public
recipient before using the key. No secret is required for this fallback;
an existing `SONOS_MODEL_17_PRIVATE_KEY` Actions secret can also be used.
The temporary key is removed after the run and excluded from all bundles.

| Recovery input | SHA-256 |
| --- | --- |
| `34.16-37101-1-17-rootfs.bin` | `c586832f1212fd38881db731860cb31f4f06aa4496138d732d3636620347f9f2` |
| Extracted `bin/upgrade` | `097d64dfce2b02b620950a3dd6688fd85572a68666474f0d42b0d4d418587e26` |

This key successfully decrypts the static package's preinstall, kernel, and
rootfs. Its rootfs is valid gzip-compressed SquashFS 4.0 with 372 inodes;
the inventory contains 371 entries below the root. The kernel is a PowerPC
Linux `2.6.39.4-yocto-standard` uImage with gzip-compressed kernel data.
See [legacy model-17 recovery](key-material.md#models-16-and-17) for derivation
and earlier validation evidence.

## Verified static snapshot

A direct HTTPS download on 2026-11-13 matched the issue's reported checksum.
It is **19,697,417 bytes**, with type-1 version words `(86, 4, 73207)`, hence
`86.4-73207`. The observation date does not establish its release date or
that it is the latest ConnectX version.

Source SHA-256:

```text
5e4c5bf49d02a9c61fa93c010b5e6c44a0fc5f178e7ff2414f7d4d2711e1501a
```

| Section index | Type | Contents | Extraction |
| ---: | ---: | --- | --- |
| 0 | 1 | Version words | Retained |
| 1 | 21 | Build capabilities/patch markers | Retained |
| 2–4 | 2 | Image descriptors | Retained |
| 5 | 22 | Signature data | Retained |
| 6 | 23 | Application tar: directory `bin/`, executable `bin/anacapad` | Fully unpacked |
| 7 | 3 | Preinstall script | Decrypted |
| 8 | 6 | PowerPC kernel uImage | Decrypted; kernel data unpacked |
| 9 | 4 | Root filesystem | Decrypted; SquashFS unpacked and inventoried |
| 10 | 17 | Application-associated signature data | Retained |

The type-23 tar occupies `[0x249bf, 0xd2b9bf)` in this snapshot. The extractor
uses parsed section boundaries rather than these fixed offsets.

| Artifact | Bytes | SHA-256 |
| --- | ---: | --- |
| Type-23 tar | 13,660,160 | `405d2322d12f125f97888cdcc4e3fe860101a9fcac4a4957883dc596b2916514` |
| `bin/anacapad` | 13,651,008 | `e4f2514c5cae8ba44fcb975c20f23c200c6a5d9e1ba88ba0ba732224a853d84f` |

Anacapa is ELF32, big-endian PowerPC, dynamically linked with `/lib/ld.so.1`,
with build ID `426f7f3a55a84b37cf7ccd5c8abb591208ecdcaf`. The original issue
observation is retained in
[`data/discovery/connectx-2026-11-13.json`](../data/discovery/connectx-2026-11-13.json).

Historical source research and the unrecovered `82.3-60197` static build are
documented in [ConnectX history](connectx-history-research.md).
