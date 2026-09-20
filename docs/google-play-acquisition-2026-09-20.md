# Sonos Google Play acquisition and preservation

Checked: **2026-09-20**

## Bottom line

The two Android packages are:

| Family | Google Play package | Public listing status |
| --- | --- | --- |
| Modern/S2 | `com.sonos.acr2` | Listed; page says updated **2026-09-14** |
| S1 | `com.sonos.acr` | Listed; page says updated **2026-02-23** |

Sources: [modern Sonos listing](https://play.google.com/store/apps/details?id=com.sonos.acr2&hl=en_US&gl=US),
[Sonos S1 Controller listing](https://play.google.com/store/apps/details?id=com.sonos.acr&hl=en_US&gl=US).

A reproducible current-version download is technically possible, but there is
no supported, credential-free Google Play download API suitable for a reliable
GitHub Actions job. The practical choices are:

1. use Aurora Store interactively with an anonymous account and export the
   downloaded files;
2. use a dedicated, expendable Google account plus `apkeep` or its
   `rs-google-play` library in a low-frequency/manual workflow; and
3. continue preserving Sonos's official universal Fire OS APK, which is a very
   useful coverage supplement but is not a Google Play acquisition.

For modern/S2, preserving only a base APK is incomplete. Google Play serves an
install set containing the base APK and the configuration/feature APKs selected
for the requesting device. Google documents that app bundles produce base,
configuration, and feature APKs, and that partial sideloaded installs missing a
required split fail on certified devices and Android 10 or later
([bundle format](https://developer.android.com/guide/app-bundle/app-bundle-format),
[partial-install warning](https://developer.android.com/guide/app-bundle#known_issues)).

## Current metadata observed

Google's public pages show the package, publisher, update date, and changelog,
but not the exact `versionName` and `versionCode` in their visible listing.
Exact current binary metadata therefore needs a package download.

The following was observed from packages fetched on 2026-09-20 with
`apkeep 1.0.0` through its **third-party APKPure backend**, then inspected with
`apktool 3.0.3` and verified with Google's `apksig 9.4.1`. These are not
direct-Google provenance claims:

| Family | `versionName` | `versionCode` | Minimum / target SDK | Signing certificate SHA-256 |
| --- | --- | ---: | --- | --- |
| Modern/S2 | `89.00.51-release+20260911.1d6af27` | `890000051` | 29 / 36 | `7C:34:EB:3C:FB:DA:05:FA:F5:6E:88:90:A2:AB:BA:C1:4B:30:36:E6:E4:35:88:49:B9:8E:88:19:B6:B7:B3:29` |
| S1 | `11.16.1` | `572374171` | 26 / 36 | `4F:1C:B1:88:A0:1E:F0:F7:C7:63:BB:2D:4B:D0:CD:52:B5:2A:83:0C:C8:52:BF:C4:E4:19:1A:F8:3C:4C:D1:96` |

There is unusually strong corroboration for both signer identities:

- The third-party modern base and every tested split were signed with SHA-256
  certificate fingerprint `7C:34:...:B3:29`. The official Sonos-hosted
  `Sonos_89.00.51.apk` already preserved in this archive has the identical
  package, version code, full build string, and certificate fingerprint.
- The S1 package certificate fingerprint is `4F:1C:...:D1:96`, with certificate
  subject `O=Sonos, Inc.`. The official Sonos-hosted historical S1 package
  `Sonos_57.22-71190_32bit.apk` (version `11.16`, code `572271190`) has that same
  certificate fingerprint.

This supports authenticity of the candidate binaries. It does **not** prove
that the bytes were fetched from Google Play, so the archive should record
them as third-party recovery until a direct Play fetch produces matching
hashes.

## Preserved recovery snapshot

The verified third-party recovery snapshot is now preserved in two GitHub
Releases, with every served APK stored individually:

- [`apps-google-play-recovery-s2`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/apps-google-play-recovery-s2): the base APK, all 12 returned configuration APKs, and the original complete XAPK wrapper;
- [`apps-google-play-recovery-s1`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/apps-google-play-recovery-s1): the current standalone S1 APK.

Per-file hashes, byte counts, signer identities, and exact asset names are in
[`data/apps/android-store-archive.json`](../data/apps/android-store-archive.json).
The recovery and direct-Google release namespaces are intentionally separate.

The modern candidate was an XAPK with a base APK plus 12 configuration splits:
11 language/density splits and `config.armeabi_v7a.apk`. That is one served
device configuration, not the complete universe of APKs Google can generate.
The matching official Sonos-hosted Fire OS APK is a 327,874,794-byte universal
APK containing `arm64-v8a`, `armeabi-v7a`, `x86`, and `x86_64` libraries, while
the Play-style base carries no native libraries and the ABI-specific split
carries the native libraries. The two distributions are therefore not
byte-identical even though their identity, version, and signer match.

## Direct Google Play paths

### Aurora Store

Aurora Store is an unofficial open-source Play client. Its upstream project
says it downloads directly from Google Play, supports personal or anonymous
accounts, supports device/locale spoofing, and offers manual old-version
downloads when Google still has the APKs and the caller knows the version
code. The same README warns that its API is reverse engineered and may break;
some functions are unavailable anonymously
([Aurora Store upstream README](https://gitlab.com/AuroraOSS/AuroraStore)).

Anonymous download is therefore viable in the Android app, not a dependable
headless service contract. Aurora's upstream source identifies its default
token dispenser as `https://auroraoss.com/api/auth`, and its README warns that
dispenser downtime is expected. A direct non-Aurora request from this machine
received Cloudflare HTTP 403 on 2026-09-20. That result does not show that the
Android app is broken; it shows that a GitHub Actions `curl` job should not be
built around the dispenser endpoint.

Aurora's FAQ documents external-storage downloads under an
`Aurora/Store/Downloads/<package>/<version>/` hierarchy when the external
storage option is enabled
([upstream FAQ history](https://gitlab.com/AuroraOSS/AuroraStore/-/wikis/Frequently-Asked-Questions/diff?version_id=e61f4145ec5390a9ada3617a1425e053400d3c09)).
An interactive recovery should export the entire version directory, not just
the base APK.

### `apkeep` and `rs-google-play`

EFF's `apkeep` supports direct Google Play downloads, but its upstream
instructions require an email address and either an AAS token or an AUTH token.
Its maintainers explicitly warn that Google may terminate the account and
recommend choosing an account for which that outcome is acceptable
([`apkeep` README](https://github.com/EFForg/apkeep/blob/master/README.md),
[`apkeep` Google Play usage](https://github.com/EFForg/apkeep/blob/master/USAGE-google-play.md)).

For a direct current snapshot with `apkeep 1.0.0`, the relevant options are:

```text
-d google-play
-o device=px_9a,locale=en_US,timezone=UTC,split_apk=true,include_dex_metadata=true,include_additional_files=true
```

Credentials should come from a mode-0600 configuration file created from
masked GitHub secrets, never from a committed file or an echoed command line.
The underlying `rs-google-play` source creates a package directory and downloads the
base, every split returned for the chosen device, optional DexMetadata, and
additional files when those flags are set
([`rs-google-play` download implementation](https://github.com/EFForg/rs-google-play/blob/master/gpapi/src/lib.rs)).

One important limitation: `apkeep 1.0.0` itself refuses package IDs containing
an explicit Google Play version. Its underlying `rs-google-play` API does
accept a numeric `version_code`, so historical recovery needs either Aurora's
manual-download UI or a small pinned wrapper around `rs-google-play`. This is
still opportunistic: a known version code is not proof Google continues to
serve that version.

### Historical candidates

The local APKPure feasibility test exposed 31 modern build identifiers from
2025-07-26 through 2026-09-11 and 29 S1 version names from `8.6` through
`11.16.1`. This is useful candidate enumeration, not an authoritative history
and not Google provenance. Each candidate should be attempted by numeric
version code through Aurora/`rs-google-play`; only successful direct downloads
should enter a direct-Play ledger. Third-party copies may enter a separate
recovery ledger only after signature, package, version, ZIP, and split-set
verification.

No public Google source establishes that every old version remains available.
The only official full app-bundle inventory and generated-APK access is the
publisher-side Play Console/App Bundle Explorer, which archive maintainers do
not control.

## Verification requirements

Treat one Play acquisition as an immutable **served set**. Preserve:

- the base APK;
- every split APK returned for the fixed device profile;
- DexMetadata and OBB/additional files if returned;
- package name, `versionName`, `versionCode`, min/target SDK;
- exact device profile, Android API, ABI, density, locale, timezone, country,
  account mode, and acquisition time;
- the downloader name/version/commit and per-file byte count plus SHA-256; and
- the signing certificate fingerprint for every APK in the set.

Use Android SDK Build Tools' official verifier:

```sh
apksigner verify --verbose --print-certs file.apk
```

Google documents both signature verification and `--print-certs`
([`apksigner` reference](https://developer.android.com/tools/apksigner)). Every
split in a served set must verify, identify the same package and version code,
and have the same signer as the base. A failed or differently signed split
invalidates the set.

For broader preservation, repeat the acquisition with fixed arm64-v8a,
armeabi-v7a, x86_64, density, API-level, and locale profiles, deduplicating by
SHA-256. Do not call the result “all Play APKs”: Play targeting can also depend
on hardware features, Android version, country, account state, and staged
rollout. Only Sonos's publisher account could export the authoritative bundle
inventory.

## Safe automation recommendation

Use two separate jobs:

1. **Daily, credential-free detection.** Parse the public Play listings for
   update-date changes and compare the official Sonos Fire OS redirect's
   package/version metadata. Open an issue when either changes. This job makes
   no download-completeness claim.
2. **Manual or low-frequency acquisition.** Run pinned `apkeep` against a
   dedicated expendable Google account, with one fixed device profile and
   concurrency 1. Preserve the complete returned directory, verify every
   member, and upload only after receipt generation. A missing/expired secret,
   authentication failure, Terms-of-Service response, or empty split set must
   fail closed and open an issue rather than silently reporting “no update.”

The repository's manual `archive Google Play delivery sets` workflow implements
the second job. It will not run unless the dispatcher explicitly checks the
terms-acceptance input, and it requires `GOOGLE_PLAY_EMAIL` plus either
`GOOGLE_PLAY_AAS_TOKEN` or `GOOGLE_PLAY_AUTH_TOKEN` as repository secrets.

Do not silently automate acceptance of changed Google terms. Do not use a personal
account. Do not expose reusable tokens in logs or artifacts. Pin `apkeep` and
Android Build Tools versions and record their hashes so the acquisition remains
reproducible.

The best immediate sequence is:

1. preserve the current modern and S1 served sets from a direct Google session;
2. confirm their hashes/signers against the high-confidence candidates above;
3. seed a version-code queue from verified third-party metadata;
4. attempt old codes slowly via Aurora manual download or a pinned
   `rs-google-play` wrapper; and
5. keep direct-Play, official Sonos-hosted, and third-party-recovered ledgers
   separate even when version strings and certificates match.
