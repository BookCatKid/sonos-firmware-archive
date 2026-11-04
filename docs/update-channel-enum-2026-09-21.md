# Update-channel enumeration — 2026-09-21

Scope: enumerate every live Sonos software-delivery channel and determine
whether any can serve packages the catalog lacks (notably `96.0-79160-1-25`
and the legacy-model UPDs absent from recent RC dirs).

## `update.sonos.com/firmware/latest/default-1-1.ups` (live)

The device-facing bootstrap endpoint documented in public research (HITB
2023, blasty/sonos) is still live. A request with a dummy querystring
(`cmaj=4&cmin=1&cbld=1&subm=100&rev=1&reg=2&serial=1&sonosid=111111111&
householdid=X`) returns HTTP 200 with a 790-byte TLV stream (blob magic
`35167f49`, type 7 = base URI, type 14 = manifest URI):

- base: `http://update-firmware.sonos.com/firmware/Prod/57.23-74170-v11.16.1-DILU1Q33hC-GA-1/^57.23-74170`
- manifest: `.../57.23-80060-v11.16.2-9tmT45qD4t-SP-1/update_1787082718.upm`

The response is byte-identical across all tested `cmaj/cmin/cbld/subm/rev/
reg/serial/sonosid/householdid` values, `swgen`/`gen`/`model` params, and
`X-Sonos-SWGen`/`X-Sonos-LatestSWGen` headers — it is a static bootstrap
manifest, not a per-device profile. The referenced manifest
(`update_1787082718.upm`, system_version `57.23-80060`, `swgen="1"`) is the
S1 catch-all; every package URL it names was already cataloged and its
GA dir (`57.23-74170`, 18 model packages) is fully preserved. No other
`.ups` path variant (`default-N-M`, `beta-`, `rc-`, `s1-`, `s2-`,
`player-`, `zp-`, `system-`) exists — all 404.

## S2 channel: corrected 2026-11-04

The earlier conclusion that this channel required device certificates was
wrong. A 404 from an incomplete request does not establish authentication
requirements. No client certificate, real device ID or account is needed for
the public request tested on November 4:

```
https://update.sonos.com/firmware/swgen/2/latest/update.upm?sonosid=Anonymous&householdid=nohhid&cmaj=81&cmin=0&cbld=52
```

Both HTTP and HTTPS returned the same 13,853-byte XML manifest. Omitting the
whole query returned 404. These tests establish the working query as a group,
not which individual parameter is necessary. The response's Content-Location
identified the canonical manifest:

```
http://update.sonos.com/firmware/Prod/2026-Sonos-22-vd4jBpORK9-GA-1/update.upm
```

It names 98.0-82170 and legacy 86.11-82170 packages under RC-1 directories.
Four files downloaded from those exact official URLs match the contributor's
issue14 samples byte-for-byte. The contributor's RC-STABLE URLs returning404
does not establish campaign rotation or unavailability of the same files.

The working anonymous request was documented publicly in
[Homebrew issue285208](https://github.com/Homebrew/homebrew-cask/issues/285208).
The binary path template `/firmware/swgen/%u/latest/` is also present in
archived Sonos firmware. Authentication-related strings elsewhere in the
binaries do not prove that this update route requires authentication.

The automated monitor now reads this modern manifest alongside the old S1
UPS response. The canonical Content-Location is restricted to the two official
firmware hosts, recorded separately from the anonymous request URL, and used
by the existing manifest/package preservation flow. A missing or unexpected
canonical location fails the check instead of silently reporting full coverage.

## Re-probe of `missing-cdn` URLs on `update.sonos.com`

`update.sonos.com` serves the same `firmware/Prod/...` tree as
`update-firmware.sonos.com` (known package: 206; missing: 404). A sampled
re-probe of missing URLs on the second host found no recoveries — the two
hosts share one backend.

## Archival services

- Wayback CDX and replay: **site-wide "Temporarily Offline"** as of
  2026-09-21 — retry later for `-5.upd` captures.
- Arquivo.pt: zero captures for `update.sonos.com`.
- Common Crawl: root + robots.txt only, no firmware objects.
- Software Heritage: none of the relevant repos (`darkarnium/sonor`,
  `blasty/sonos`, `systemcrash/sonos-firmware`) are archived.
- archive.org item search: no Sonos firmware items.
- APK mirrors (APKMirror/APKPure/apk.support): bot-protected; and no
  evidence any Sonos app ever bundled speaker `.upd` payloads — updates
  are always device-fetched.

## GitHub

Authenticated code/repo search for `.upd` payloads, `mdp.h`, envelope
magics, `sonos_blob_encdec`, and firmware-URL strings found only the
already-known repositories (`systemcrash/sonos-firmware`, `blasty/sonos`,
`darkarnium/sonor`, `trulyspinach/sonos-fenway`, blocklists, and this
archive). No new binary sources.
