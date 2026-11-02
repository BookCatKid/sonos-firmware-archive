# Historical Sonos app download availability

Checked: **2026-09-19**

## Answer

The apps on Sonos's [downloads page](https://support.sonos.com/en-us/downloads)
do not have equally recoverable histories. If the objective is to preserve the
actual old installer binary, rather than merely learn that a version existed,
the priority order is:

1. **Web app: effectively unavailable as versioned downloads.** There is no
   installer. Sonos exposes the current hosted application at
   `play.sonos.com`; an old deployment can only survive in an independent web
   capture, and a capture may omit authenticated API responses or dynamically
   loaded resources.
2. **iOS/iPadOS Sonos and Sonos S1: very difficult.** Apple's store presents
   version-history metadata, but it has no public control for downloading an
   arbitrary listed version or a standalone IPA. Apple can offer a
   *last-compatible* version for an older OS, but the developer decides which
   submitted versions remain eligible. That is not a general historical
   archive.
3. **Fire OS Sonos and Sonos S1: difficult.** The official Sonos page offers a
   current modern Fire OS download and links S1 to Amazon, but neither page
   exposes an official version picker or historical-binary index. Fire-specific
   builds are also much less consistently mirrored than ordinary Android
   packages.
4. **Android Sonos and Sonos S1: officially difficult, practically moderate.**
   Google Play offers the current compatible package, not an arbitrary old
   release. However, independent APK archives have substantial version
   histories for both package IDs, so many versions can be recovered and their
   signing certificates checked. Those are third-party copies, not official
   availability.
5. **Windows and macOS Sonos/S1: easiest, but still not an official complete
   archive.** Sonos's page links only current installers, yet multiple old
   version-named `.exe` and `.dmg` files remain live on Sonos's own CDN. They
   are ordinary files and therefore much easier to preserve and validate than
   store-delivered mobile applications. Discoverability is the problem: Sonos
   publishes no complete installer index, and not every point release is known
   to have had its own versioned bootstrapper.

In short: **the web app and both iOS apps are the hardest to preserve; Fire OS
comes next. Android is recoverable mainly because of third parties. Desktop is
the easiest and should be swept first.**

## Per-app matrix

| Sonos listing | Platform | Official old binary selection | Practical availability | Assessment |
|---|---|---:|---|---|
| Sonos app | iOS/iPadOS | No arbitrary version selector | Store metadata is rich; binary retrieval is account, OS, and Apple-policy dependent | **Very difficult** |
| Sonos app | Android | No | Large third-party APK history exists | **Moderate, unofficial** |
| Sonos app | Fire OS | No index or version selector found | Current APK is linked by Sonos; historical Fire-specific builds are sparse | **Difficult** |
| Sonos Web app | Browser | Not applicable; no installer | Requires independent capture of each deployment | **Effectively unavailable after replacement** |
| Sonos desktop app | Windows | No index | Many old Sonos-CDN installers remain reachable when their names are known | **Relatively easy** |
| Sonos desktop app | macOS | No index | Same situation as Windows; old Sonos-CDN DMGs remain reachable | **Relatively easy** |
| Sonos S1 Controller | iOS/iPadOS | No arbitrary version selector | Version history exists, but not direct IPAs | **Very difficult** |
| Sonos S1 Controller | Android | No | Third-party APK archive includes numerous S1 versions | **Moderate, unofficial** |
| Sonos S1 Controller | Fire OS | No | Amazon listing supplies the current build; old-build discovery is poor | **Difficult** |
| Sonos S1 Controller | Windows | No index | Known Sonos-CDN bootstrapper and older controller installers remain live | **Relatively easy, incomplete** |
| Sonos S1 Controller | macOS | No index | Known historical Sonos-CDN DMGs remain live | **Relatively easy, incomplete** |

## Evidence and qualifications

### What Sonos itself publishes

The [Sonos downloads page](https://support.sonos.com/en-us/downloads) lists the
modern mobile app for iOS, Android, and Fire OS; the hosted web app; modern
Windows and macOS desktop apps; and S1 for iOS, Android, Fire OS, Windows, and
macOS. Its wording is consistently about downloading **the** app or visiting a
store. It does not present an old-version archive.

Sonos separately maintains detailed [mobile app release
notes](https://support.sonos.com/en-us/article/release-notes-sonos-app-updates).
Those notes identify old iOS and Android version numbers and dates, but direct
customers to their app store for the **latest** available version. The release
notes are therefore useful as a historical index, not as proof that the old
binary is still downloadable.

The live Apple pages similarly expose version history for the [modern Sonos
app](https://apps.apple.com/us/app/sonos/id1488977981) and [Sonos S1
Controller](https://apps.apple.com/us/app/sonos-s1-controller/id293523031),
while the install action resolves through Apple's current store distribution.
Apple's own App Store Connect documentation confirms that developers can
choose which submitted builds remain available as
[last-compatible versions](https://developer.apple.com/help/app-store-connect/manage-your-apps-availability/make-a-version-unavailable-for-download).
That mechanism may help a qualifying older device, but it does not let a user
choose any release shown in Version History.

The live Google Play pages for the [modern app](https://play.google.com/store/apps/details?id=com.sonos.acr2)
and [S1](https://play.google.com/store/apps/details?id=com.sonos.acr) expose one
Install action and the current compatible release. Google's distribution
documentation says Play selects the compatible package with the highest
version code: see [Multiple APK
support](https://developer.android.com/google/play/publishing/multiple-apks.html)
and [How app updates
work](https://developer.android.com/google/play/app-updates). It does not
describe a user-facing arbitrary downgrade facility.

Sonos also warns that unsupported operating systems stop receiving updates,
and specifically says the S1 app for Android 5 and 6 is no longer in Google
Play. See [Using Sonos with unsupported operating
systems](https://support.sonos.com/en-us/article/using-sonos-with-unsupported-operating-systems).
This is direct evidence that continued store availability cannot be assumed
even for a compatibility build.

### Desktop files remain unusually recoverable

The following historical installer URLs on Sonos infrastructure returned
`HTTP 200` during this check:

- Modern/S2 79.0-52294: [`Windows EXE`](https://update-software.sonos.com/software/nAtvycichO/Sonos_79.0-52294.exe)
  and [`macOS DMG`](https://update-software.sonos.com/software/nAtvycichO/Sonos_79.0-52294.dmg).
- S1 57.22-59130: [`Windows EXE`](https://update-software.sonos.com/software/72ynP0v9lu/Sonos_57.22-59130.exe)
  and [`macOS DMG`](https://update-software.sonos.com/software/72ynP0v9lu/Sonos_57.22-59130.dmg).
- Windows: [`SonosDesktopController86.exe`](https://update-software.sonos.com/software/pc/dcr/SonosDesktopController86.exe),
  [`SonosDesktopController112.exe`](https://update-software.sonos.com/software/pc/dcr/SonosDesktopController112.exe),
  [`SonosDesktopController120.exe`](https://update-software.sonos.com/software/pc/dcr/SonosDesktopController120.exe),
  and [`SonosDesktopController131.exe`](https://update-software.sonos.com/software/pc/dcr/SonosDesktopController131.exe).
- macOS: [`SonosDesktopController81.dmg`](https://update-software.sonos.com/software/mac/mdcr/SonosDesktopController81.dmg),
  [`SonosDesktopController120.dmg`](https://update-software.sonos.com/software/mac/mdcr/SonosDesktopController120.dmg),
  and [`SonosDesktopController131.dmg`](https://update-software.sonos.com/software/mac/mdcr/SonosDesktopController131.dmg).

The 13.1 links were also published by Sonos staff as an official rollback for
a desktop crash in a [Sonos Community support
reply](https://en.community.sonos.com/controllers-and-music-services-228995/sonos-controller-crashing-version-13-1-1-6859207?postid=16541650).
This establishes that the CDN files are genuine historical Sonos downloads,
not merely third-party rehosting. It does **not** establish that every desktop
release can be reconstructed by guessing a filename. Later S1 application
updates may also reuse an older bootstrapper filename, so installer identity
and installed application version must be recorded separately.

### Third-party Android archives

APKMirror currently has downloadable histories for both the [modern Sonos
package](https://www.apkmirror.com/apk/sonos-inc/sonos-for-android/) and the
[S1 package](https://www.apkmirror.com/apk/sonos-inc/sonos/), including
multiple CPU variants and signing-certificate fingerprints. That makes Android
materially easier than iOS in practice.

These archives are useful leads, not first-party evidence. Every retained APK
should be hashed, its package name/version code recorded, and its signing
certificate compared across known Sonos releases. Split APK/XAPK releases must
also be preserved as a complete install set rather than as one base APK.

## Preservation implication

A non-AI archival workflow should treat the categories differently:

- Poll and immediately save every current Windows/macOS installer, current
  direct Fire OS APK, and their redirect destinations, including response
  headers, hashes, signatures, and embedded version metadata.
- Use Sonos and store release notes to enumerate mobile versions, but maintain
  a separate receipt showing whether the corresponding binary was actually
  acquired.
- Preserve Android packages per architecture/configuration and verify the
  Sonos signing identity; never mark a version complete from a third-party
  listing alone.
- Capture the web application's static dependency graph on every detected
  deployment. Label it a web snapshot, not an offline functional copy.
- For iOS, record App Store metadata immediately. Binary acquisition cannot be
  made complete by a normal public crawler, so the archive should explicitly
  report those IPA rows as missing rather than equating metadata with payloads.

Finally, availability does not imply usability. A historical controller may
require matching player firmware, may be forced to update, or may depend on
services that no longer accept it. This report assesses whether old app bytes
can be found and downloaded, not whether a downgrade will still control a
current Sonos system.
