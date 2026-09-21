# Firmware discovery from public and cataloged paths: 2026-09-21

## Result

Five firmware lines added **90 live official Sonos UPDs** totaling
**3,130,268,347 bytes**:

| Version | Live models | Bytes | Directory source |
|---|---:|---:|---|
| `82.3-60160` | 28 | 1,415,018,584 | Public device update report |
| `57.9-23010` | 18 | 284,700,630 | Public support-supplied repair URL |
| `80.1-55014` | 25 | 1,140,063,698 | Previously cataloged desktop installer directory |
| `57.22-67080` | 18 | 288,396,021 | Previously cataloged desktop installer directory |
| `47.2-59120` | 1 | 2,089,414 | Previously cataloged desktop installer directory |

All sources and extracted components are published in the corresponding
[`82.3-60160`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/firmware-82.3-60160),
[`57.9-23010`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/firmware-57.9-23010),
[`80.1-55014`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/firmware-80.1-55014),
[`57.22-67080`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/firmware-57.22-67080), and
[`47.2-59120`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/firmware-47.2-59120)
Releases. The targeted release audit reconciled **158/158 assets**, including
pre-existing desktop installers, with no missing, mismatched, or unrecorded
files.

The first exact leads were:

- [Sonos Community packet-capture report](https://en.community.sonos.com/speakers-229128/anyone-else-having-problems-updating-firmware-6926210)
  for model 21 of `82.3-60160`; and
- [Sonos Community repair report](https://en.community.sonos.com/speakers-229128/play-5-gen1-not-work-lost-smbscand-file-anyone-can-help-6862966)
  containing a model-16 `57.9-23010` URL supplied by Sonos support.

Only the minimal exact-URL evidence is retained in the repository. The reports'
surrounding diagnostic or account information is not copied.

The remaining three finds came from expanding opaque official firmware
directories that were already present in the application/firmware catalog for
desktop installers. Before this sweep, those directories had never been tested
over the speaker package naming convention.

## Extraction

Recovered keys for package models 1, 8, 9, 12, 16, and 17 produced **64 raw
components** from 20 packages. Each decryptable package yielded preinstall,
kernel, and root-filesystem components; model 9 additionally yielded its device
payload. The sole surviving `47.2-59120` UPD is package model 5 and remains
blocked on recipient `0cfddc3f6ac5c485ccc435885c42c52a9338e28b`.

## Negative and duplicate leads

Bounded model 0–80 probes found no live speaker UPDs in the exact directories
for `28.1-86141`, `38.9-46251`, `50.1-63230` beta, `57.22-68080`,
`57.23-80060`, `58.1-77280`, `64.3-19080`, `64.3-21150`, `90.0-67300`,
`90.0-77070`, or `90.0-79210`. These negative receipts are retained so the
same directories need not be rediscovered by hand.

An alternate `81.1-58210` GA-2 directory exposed the same 28 model numbers and
byte lengths as the already preserved GA-1 set. The two unusual filenames
`62.1-87080` and `62.1-87130` were also already present in the catalog with the
same live models and lengths. They were not counted as new artifacts.

The independent synthetic metadata sweep completed 10,000/10,000 profiles with
zero errors and three response hashes. It yielded no unknown manifest and only
the already documented `54.2-72160` StubInstaller base URI.
