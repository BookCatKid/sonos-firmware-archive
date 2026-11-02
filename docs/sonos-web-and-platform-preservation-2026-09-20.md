# Sonos web and application-platform preservation

Checked: **2026-09-20** (America/Los_Angeles)

## Executive answer

The Sonos Web app is worth preserving, but it should be described as a
**deployment snapshot**, not an offline Sonos controller. Sonos's canonical
marketing URL is [`https://www.sonos.com/web-app`](https://www.sonos.com/web-app),
which redirects to [`https://play.sonos.com`](https://play.sonos.com). The live
site is a hosted Next.js application on Vercel. An unauthenticated request to
the root is redirected through `/api/auth/signin` to a localized login page and
sets NextAuth CSRF and callback cookies. The public login response identifies
generated JavaScript/CSS chunks and exposes a web-app manifest, so its interface
bytes, routing shell, icons, headers, and dependency graph can be captured.

That capture cannot preserve the service behind the interface. Sonos says the
Web app works even outside the home and offers playback, grouping, and content
selection, while setup and system settings require the mobile app
([apps and downloads](https://support.sonos.com/en/downloads)). Sonos's public
Control API architecture likewise routes control through the Sonos cloud; its
LAN Control API is not generally available
([architecture](https://docs.sonos.com/docs/connected-home-architecture)). A
static replay therefore cannot authenticate a Sonos account, discover its
households, obtain live player state, browse cloud music catalogs, or send
working control commands after Sonos's account/control/content services are
gone.

The repository already covers the downloadable Windows, macOS, Sonos-hosted
Fire OS/Android, and recovered Google Play artifacts. The material platform
gaps are the two iOS/iPadOS IPA histories, complete Google Play histories, the
Amazon-distributed S1/Fire OS history, and older Web deployments. The current
public Web deployments are now captured by this repository. There is
no first-party Sonos controller distribution for watchOS, CarPlay, Android
Auto, tvOS, Fire TV, Roku, or ChromeOS in Sonos's current official app catalog.
Those surfaces are either unsupported or integrations rather than missing
controller binaries.

## The official Web app and what is observable without an account

Sonos publishes one modern Web controller and no S1 Web controller. Its
[downloads page](https://support.sonos.com/en/downloads) lists the Web app
between the modern mobile and desktop controllers, but the S1 section lists
only iOS, Android, Fire OS, Windows, and macOS. Supported browsers are Chrome,
Firefox, Safari, and Edge
([app requirements](https://support.sonos.com/en/article/sonos-app-requirements)).

The following was verified directly without signing in on 2026-09-20:

| Resource | Observed behavior |
|---|---|
| `https://www.sonos.com/web-app` | Official entry URL; redirects to `play.sonos.com` (ordinary command-line clients can receive Sonos edge `403`, while the support-page link and browser redirect establish the target). |
| `https://play.sonos.com/` | `307` to `/api/auth/signin?callbackUrl=%2F`, then `302` to `/login`, then localized `/en-us/login`; final response is Next.js HTML served by Vercel with `Cache-Control: private, no-cache, no-store`. |
| `https://play.sonos.com/api/auth/session` | Returns `{}` without a session, sets NextAuth CSRF/callback cookies, and is non-cacheable. |
| `https://play.sonos.com/manifest.webmanifest` | Public 564-byte manifest named “Sonos,” `start_url: /`, `display: standalone`, black theme/background, and three Sonos icon resources. It was last modified 2026-09-17 and had SHA-256 `2af76b22355a6bbcda10dde74d0c76e6dfb79607fa0642e8451c12c83b7764fa`. |
| Public login HTML | 16,524 bytes, SHA-256 `f1f2e2409e17028f9701e0ace4fb146e3543f4e3a8422c8745078b06b2ec0acb`, with one CSS and 29 distinct JavaScript references under `/_next/static/chunks/` plus the manifest and favicon. |

These hashes identify this one observation, not a stable release number.
Neither the HTML nor manifest exposes an application version. Each crawl must
therefore use capture time plus content hashes as the deployment identity.

### PWA and service-worker finding

The manifest makes the site installable in a standalone-looking browser
window, but a manifest is not an offline cache. The unauthenticated login HTML
contains no service-worker registration. Direct probes of the conventional
`/service-worker.js`, `/sw.js`, `/worker.js`, and
`/_next/static/service-worker.js` paths were redirected into authentication or
localization rather than returning JavaScript. Therefore **no publicly
available service worker was found** in the account-free surface.

This is deliberately narrower than claiming that no worker exists anywhere:
authenticated chunks may register one, and those chunks were not available to
the account-free inspection. Even if a worker later appears, authentication,
account data, player events, catalog/search responses, artwork, and control
requests remain network resources unless its code explicitly caches synthetic
responses. A service worker would improve shell/offline loading, not preserve
the Sonos cloud.

### Sonos Pro dashboard

Sonos also operates the distinct browser-based Sonos Pro dashboard at
[`https://pro.sonos.com`](https://pro.sonos.com). Sonos describes Pro as a Web
dashboard used alongside the ordinary Sonos mobile app, not as another App
Store or Play Store controller ([Sonos Pro](https://www.sonos.com/en-us/sonos-pro),
[getting started](https://www.sonos.com/en-us/guides/sonos-pro/gettingstarted)).
Its account-free Next.js surface is much more archiveable than the consumer
controller login: build `3df745c4` publicly exposes a build manifest with 38
routes and the referenced route chunks. The first complete repository capture
retained 82 public response bodies with no fetch gaps.

Those bundles identify dependencies on Sonos account, control, music-account,
service-catalog, settings, OAuth, WebSocket, media, and content services. This
makes the user-interface and client logic historically useful while reinforcing
the same preservation boundary: the static dashboard cannot operate without
the live Sonos backends.

## What a useful Web archive should contain

A scheduled, account-free collector can meaningfully preserve:

1. the official redirect chain, response status, headers, TLS host, and capture
   time;
2. the login HTML/RSC payload, manifest, icons, social images, fonts, CSS, and
   every recursively discovered static JavaScript/media asset;
3. original URLs, byte lengths, MIME types, ETags/Last-Modified values, and
   SHA-256 for every response;
4. a WARC (or equivalent request/response bundle) and a normalized dependency
   manifest; and
5. screenshots plus a browser-network log showing failures and redirects.

The crawler should retain each new root-HTML/manifest/dependency-graph hash as
a separate deployment, rather than overwriting “latest.” It should retry asset
fetches in a real browser context because the current locale/auth middleware
redirects direct requests for the advertised `/_next/static/chunks/` URLs. A
failed or authentication-gated resource must be recorded as a gap, not saved
under the expected `.js`/`.css` name when its body is actually login HTML.

An authenticated capture could enumerate more routes and code, but it is not
required for the first preservation pass and should not be automated casually.
If later authorized, use a dedicated archival account and redact cookies,
tokens, household/player identifiers, music-service credentials, history, and
personalized API bodies. Do not commit an authenticated browser profile or
replayable session.

### What cannot be made functional offline from a crawl

Sonos's documented authorization is OAuth-based: a user authorizes household
access, an access token accompanies every API call, tokens expire, and refresh
requires a server-held client secret
([Authorize](https://docs.sonos.com/docs/authorize)). Its Control API sends
commands to the cloud gateway and the cloud routes them to household players
([Control API](https://docs.sonos.com/reference/about-control-api)). These are
the public partner APIs, not proof of the Web app's private endpoint names, but
they establish the supported cloud-control architecture. The Web app's exact
post-login endpoints could not be responsibly observed without an account.

Consequently, the following are outside what static preservation can restore:

- Sonos login, session creation/refresh, account and household authorization;
- cloud discovery, player/group state, commands, subscriptions, and events;
- music-service authentication, search/browse results, recommendations,
  metadata, artwork, and playable stream URLs;
- server-rendered/authenticated Next.js routes and Sonos/Vercel API handlers;
- new setup, product addition, or system configuration (which Sonos does not
  offer in the Web app even while the service is live); and
- guaranteed compatibility with future or historical player firmware.

Restoring those functions would require a separately engineered compatible
backend and authorization/control implementation, not a better web crawler.

## Official controller distributions

Sonos's [downloads catalog](https://support.sonos.com/en/downloads) and
[requirements page](https://support.sonos.com/en/article/sonos-app-requirements)
define the first-party controller surface:

| Family | Platform / official identity | Role | Current archive state and gap |
|---|---|---|---|
| Sonos (modern) | iPhone and iPad, Apple app ID `1488977981` | Primary/full controller and required setup surface | Store metadata is known; IPA/build history is not archived. Apple's listing declares compatibility only with iPhone and iPad ([App Store](https://apps.apple.com/us/app/sonos/id1488977981)). |
| Sonos (modern) | Android, package `com.sonos.acr2` | Primary/full controller and required setup surface | Current recovered Play delivery set and many historical recovered builds are represented; arbitrary-version Play acquisition is not complete. Android x86 and Chromebooks are explicitly unsupported. |
| Sonos (modern) | Fire OS 7+ / Amazon Fire tablets; Sonos-hosted APK | Primary/full tablet controller | Sonos-hosted APK history is substantially preserved in [`mobile-archive.json`](../data/apps/mobile-archive.json); one known truncated historical capture remains a gap. This is not a Fire TV app. |
| Sonos (modern) | Windows 8+ | Basic desktop controller | Preserved candidates are recorded in [`desktop-archive.json`](../data/apps/desktop-archive.json); no official complete historical index exists. |
| Sonos (modern) | macOS 10.12+ | Basic desktop controller | Same boundary as Windows. |
| Sonos (modern) | Web: Chrome, Firefox, Safari, Edge | Basic remote/cloud controller; no setup/settings | Current account-free deployment snapshot is recorded in [`web-app-archive.json`](../data/apps/web-app-archive.json); authenticated code and cloud responses remain explicit gaps. |
| Sonos S1 Controller | iPhone/iPad, Apple app ID `293523031` | Full controller for S1/legacy systems | Store metadata is known; IPA/build history is not archived ([App Store](https://apps.apple.com/us/app/sonos-s1-controller/id293523031)). |
| Sonos S1 Controller | Android, package `com.sonos.acr` | Full S1 controller | Current and historical recovered APK work exists, but Google Play history remains incomplete. |
| Sonos S1 Controller | Fire OS / Amazon listing | Full S1 tablet controller | No complete Amazon binary history is present; Sonos's downloads page links this variant to Amazon rather than a Sonos version index. |
| Sonos S1 Controller | Windows 7+ | S1 desktop controller | Included in the desktop candidate/archive sweep, without a provably complete official history. |
| Sonos S1 Controller | macOS 10.12+ | S1 desktop controller | Included in the desktop candidate/archive sweep, with the same completeness limitation. |

The mobile apps are the only current surface Sonos describes as capable of
complete control and setup. Desktop and Web are intentionally reduced
controllers ([apps and downloads](https://support.sonos.com/en/downloads)).

## Controller-adjacent surfaces that are integrations, not Sonos apps

These surfaces matter historically, but they do not imply another first-party
controller binary to download:

- **iOS system surfaces:** the modern iOS app currently includes Shortcuts
  actions, widgets, Live Activity/Dynamic Island control, and iPad-specific UI.
  They are extensions/features inside the iOS package, not watchOS or separate
  apps; Sonos records them in its
  [app release notes](https://support.sonos.com/en-us/article/release-notes-sonos-app-updates).
- **AirPlay and Siri:** compatible speakers accept AirPlay, and Siri can start
  and control Apple Music after a speaker is added to Apple Home. Sonos
  explicitly says this does not add Siri to the speaker or direct control in
  Apple Home ([Siri](https://support.sonos.com/en/article/use-siri-to-control-sonos-speakers)).
- **Music-service direct control:** Amazon Music, Audible, IDAGIO, iHeartRadio,
  Pandora, Spotify, and TIDAL can hand playback to Sonos; this is a feature of
  those providers' apps and services
  ([direct control](https://support.sonos.com/en/article/stream-audio-to-sonos-from-another-app)).
- **Voice:** Sonos Voice Control is installed/configured through the mobile app
  and runs on supported voice-enabled products; Amazon Alexa uses the Sonos
  skill and Amazon's app/cloud; Google Assistant is another account-linked
  integration. These are firmware/cloud/skill surfaces, not standalone Sonos
  controller downloads
  ([Sonos Voice Control](https://support.sonos.com/en/article/set-up-sonos-voice-control),
  [Alexa](https://support.sonos.com/en/article/set-up-an-amazon-alexa-device-to-control-sonos),
  [Google Assistant](https://support.sonos.com/en/article/set-up-a-google-assistant-device-to-control-sonos)).
- **TV:** Fire TV control is Alexa linking performed in the Alexa mobile app,
  not a Fire TV Sonos app
  ([Fire TV setup](https://support.sonos.com/en/article/set-up-amazon-fire-tv-with-sonos)).
  Home-theater products also accept TV audio and volume control through
  HDMI-ARC/eARC, CEC, or IR/RF remotes; those are hardware protocols, not Roku,
  Apple TV, or television controller packages
  ([TV remote requirements](https://support.sonos.com/en/article/tv-remote-requirements-for-sonos-home-theater-products)).
- **Connected-home/partner control:** Control4, Lutron, Crestron, SmartThings,
  RTI, URC, IKEA and other certified products/apps can expose Sonos controls.
  They are partner artifacts built on integration interfaces, not Sonos app
  releases; the official partner catalog is the
  [Works with Sonos page](https://www.sonos.com/en-ie/works-with-sonos).
- **Third-party cloud controllers:** Sonos permits developers to release apps
  or devices using its cloud Control API without Works with Sonos
  certification
  ([Connected Home](https://docs.sonos.com/docs/connected-home-get-started)).
  Their binaries and services are outside a first-party Sonos archive.

No Sonos first-party controller for watchOS, CarPlay, Android Auto, tvOS,
Fire TV, Roku, or ChromeOS appears in the current Sonos downloads
catalog. The Apple listing limits the modern binary to iPhone/iPad, and Sonos
explicitly excludes Chromebooks from both Android controller families. This is
an evidence-based “not currently offered,” not proof that no experimental or
long-retired internal build ever existed.

## Prioritized archive gaps

1. **Retain each newly detected Web deployment.** The account-free snapshotter
   and daily issue monitor now exist; old deployments still disappear when
   Vercel/Sonos replaces them, so each alert must be followed by an archival
   run. Do not block the public capture on credentials.
2. **Finish and audit both Google Play package histories.** The local
   [`android-store-history-sweep.json`](../data/apps/android-store-history-sweep.json)
   was still partial at inspection time. Preserve complete split sets and
   signing identities, and label third-party recovery provenance accurately.
3. **Acquire/export authorized iOS and iPadOS builds for both app IDs.** This is
   the largest binary gap. Preserve extensions (widgets, Live Activities,
   Shortcuts) as part of each IPA and record FairPlay/account constraints.
4. **Investigate the Amazon S1 listing separately.** The Sonos-hosted modern
   APK sweep does not prove coverage of Amazon-delivered S1 builds or Amazon
   signing/packaging variants.
5. **Preserve integration metadata, not partner binaries, in this repository.**
   Periodically snapshot the Works with Sonos partner list, direct-control
   service list, supported voice integrations, and their public setup docs.
   Archive partner software only in a deliberately expanded, separately scoped
   collection.

The Web work is therefore sensible and urgent, but its success criterion is a
reproducible historical record of each deployment—not an offline clone that is
expected to keep controlling speakers after Sonos's cloud disappears.
