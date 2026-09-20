# Sonos application preservation sweep

Checked: **2026-09-20**

## Result

The archive now has reproducible, non-AI discovery and preservation paths for
Sonos desktop installers and official Sonos-hosted APKs. Large binaries live in
dedicated GitHub Releases; the repository retains source URLs, discovery
provenance, byte counts, SHA-256 hashes, and the verified Release asset names.

This is a sweep of every binary exposed by the sources listed below. It is not
a claim that every application build Sonos ever shipped can be enumerated:
Sonos, Apple, Google, and Amazon do not publish complete historical binary
indexes.

## Windows and macOS

The canonical current checks are Sonos's own redirect endpoints:

- `https://www.sonos.com/redir/controller_software_pc2`
- `https://www.sonos.com/redir/controller_software_mac2`
- `https://www.sonos.com/redir/controller_software_pc`
- `https://www.sonos.com/redir/controller_software_mac`

The first pair is the modern/S2 controller and the second pair is S1. The
discovery program checks these first. Sonos's Akamai edge rejects ordinary
Python and `curl` clients with `403`; the monitor therefore uses `curl-cffi`
with a Chrome-compatible TLS fingerprint and follows the redirect chain one
hop at a time without downloading the target body. This resolves the live
canonical endpoints directly. Wayback remains historical discovery only, not
the source of truth for the current version.

Historical candidates are combined from:

1. the current official redirects;
2. versioned Sonos URLs recorded in Microsoft's WinGet manifests;
3. versioned Sonos URLs recorded in Homebrew Cask history; and
4. a Wayback CDX prefix query for `.exe` and `.dmg` URLs under
   `update-software.sonos.com/software/`.

The refreshed 2026-09-20 inventory contains **138 candidate URLs**, of which
**98 return HTTP 200 from Sonos**. That includes 24 live Sonos URLs added by the
Wayback sweep beyond the initial WinGet/Homebrew-derived inventory. The
machine-readable evidence is
[`data/apps/desktop-discovery.json`](../data/apps/desktop-discovery.json), and
verified uploaded artifacts are recorded in
[`data/apps/desktop-archive.json`](../data/apps/desktop-archive.json).

## Official APK / Fire OS path

Sonos's [downloads page](https://support.sonos.com/en-us/downloads) identifies
Fire OS as a supported download and routes the modern Fire OS download through
`https://www.sonos.com/redir/controller_software_android2`. The live redirect
is resolved directly with the same Chrome-compatible TLS client used for the
desktop endpoints. A Wayback CDX prefix sweep over the same Sonos software
namespace found **33 historical APK URLs**;
adding the current redirect target produces **34 candidates**. At discovery
time, **33 originals remain live on the Sonos CDN**. The one dead original,
`SonosAndroidController1305.apk`, has only a 1 MiB truncated Wayback response;
ZIP validation rejects it and the archive records it as an explicit gap rather
than presenting the fragment as an APK.

The discovery receipt is
[`data/apps/mobile-discovery.json`](../data/apps/mobile-discovery.json); the
hash-verified Release receipt is
[`data/apps/mobile-archive.json`](../data/apps/mobile-archive.json). The completed
sweep preserves **34 valid APKs** and records the single truncated capture as a
gap. The live redirects currently resolve to macOS `90.0-81181` and APK
`89.00.51`; both are preserved. These are described as official Sonos-hosted
APKs. They must not be assumed byte-for-byte
identical to Google Play variants without comparing package metadata,
architectures, split configuration, and signing certificates.

As a provenance check, the current official Sonos-hosted APK inspected during
this sweep identifies the modern package as `com.sonos.acr2` and contains a
self-issued Sonos release certificate whose subject includes `O=Sonos, Inc.`
and `OU=Sonos Software Release Team`. Its certificate SHA-256 fingerprint is
`7C:34:EB:3C:FB:DA:05:FA:F5:6E:88:90:A2:AB:BA:C1:4B:30:36:E6:E4:35:88:49:B9:8E:88:19:B6:B7:B3:29`.
That fingerprint is a useful comparison anchor, not proof that every old S1 or
legacy APK must use the same certificate.

Amazon's own documentation says Fire OS uses Android package version codes and
prevents installation of a lower version code over a newer installed package:
[Update a Published App](https://www.developer.amazon.com/docs/app-submission/update-published-app.html).
That is why preserving an APK does not by itself guarantee an easy downgrade.

## Android from Google Play

Google Play offers the current compatible build, not a public arbitrary-version
picker. APKMirror has broad histories for the modern package
`com.sonos.acr2` and S1 package `com.sonos.acr`, but those are third-party
copies. They remain a useful second phase only if each package's version code,
architecture/configuration, and Sonos signing-certificate fingerprint are
verified and split packages are preserved as complete install sets.

The current recovery snapshot has now been archived separately from the
first-party Sonos-hosted APKs. The modern recovery set contains a base APK and
12 configuration splits plus its original XAPK wrapper; the current S1 build is
a standalone APK. Every APK passes Google's `apksig` verifier and has the same
Sonos signing certificate as the corresponding official Sonos-hosted package.
This strongly authenticates the contents but does not turn APKPure acquisition
into direct-Google provenance.

The immutable receipt is
[`data/apps/android-store-archive.json`](../data/apps/android-store-archive.json),
and the binaries are in the
[`apps-google-play-recovery-s2`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/apps-google-play-recovery-s2)
and
[`apps-google-play-recovery-s1`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/apps-google-play-recovery-s1)
Releases. See the [Google Play acquisition report](google-play-acquisition-2026-09-20.md)
for the direct-download boundary and signer evidence.

## Fire OS is not a separate Fire TV controller

The official current Fire OS APK is a mobile/tablet controller. Its decoded
manifest has the ordinary Android `LAUNCHER` category and no
`LEANBACK_LAUNCHER` or `amazon.hardware.fire_tv` declaration. Sonos describes
Fire OS under its mobile-device requirements, while its Fire TV setup article
uses the Alexa app on an iOS or Android device rather than a Sonos TV app:
[Sonos app requirements](https://support.sonos.com/en/article/sonos-app-requirements),
[Fire TV setup](https://support.sonos.com/en/article/set-up-amazon-fire-tv-with-sonos).
Amazon also says an existing Amazon app normally needs a separate Fire TV APK
and identifies `amazon.hardware.fire_tv` as the platform feature
([Amazon Fire TV FAQ](https://developer.amazon.com/docs/fire-tv/faq-general.html)).
No separately identifiable official Sonos Fire TV controller package was found,
so the existing Fire OS release series is tablet coverage—not a missing TV
archive disguised under another name.

## iOS and jailbroken-device recovery

A jailbroken iPad materially improves the odds. AppStore++ can request older
App Store build identifiers, download versions Apple still serves to the
signed-in account, and package/export account-authorized IPAs; its current
[release history](https://github.com/CokePokes/AppStorePlus-TrollStore/releases)
explicitly includes an IPA Library and IPA packaging support.

That does **not** establish that Apple keeps every build forever. Apple's
[Last Compatible Version Settings](https://developer.apple.com/help/app-store-connect/manage-your-apps-availability/make-a-version-unavailable-for-download)
let a developer deselect submitted versions so they are no longer available
for redownload. Retrieval also depends on Apple's backend still serving the
build, the Apple account's authorization/purchase history, device/OS
compatibility, and storefront. App Store version-history metadata is evidence
that a release existed, not proof its IPA remains retrievable.

Therefore the defensible workflow is to enumerate both Sonos app IDs on an
authorized jailbroken device, immediately export every retrievable version,
and record app ID, external version, build ID, account/storefront conditions,
file size, and SHA-256. FairPlay/account authorization and executable
decryption are separate concerns and must be documented separately rather
than treating an exported IPA as universally installable.

## Automated monitoring

The existing daily GitHub Actions workflow now runs four independent checks:

- signed firmware/manifests and known public firmware sources;
- the four desktop redirects plus WinGet, Homebrew, and Wayback evidence;
- the official APK redirect plus the Sonos-CDN Wayback prefix inventory; and
- the official public Google Play listing update dates for both package IDs.

Each checker is deterministic and does not use AI. A newly observed unarchived
URL or redirect target opens or updates a dedicated GitHub issue, and the full
JSON report is attached to the workflow run. A source error fails the workflow
instead of silently reporting that nothing changed. The same run reconciles
every receipt-listed GitHub Release asset against GitHub's server-reported size
and SHA-256 digest.

The preservation scripts stream one binary at a time and checkpoint after each
verified upload, making them safe to resume on machines with limited disk
space:

```bash
python scripts/archive_desktop_installers.py
python scripts/archive_mobile_installers.py
python scripts/archive_android_store_apps.py --source apkpure-recovery
python scripts/audit_release_assets.py --output release-asset-audit.json
```

Direct Google Play acquisition is a separate manually dispatched workflow. It
uses the vendored pinned `apkeep` source, refuses implicit terms acceptance,
requires a dedicated account's repository secrets, verifies every returned APK,
uploads the complete served set, and commits the resulting receipt.
