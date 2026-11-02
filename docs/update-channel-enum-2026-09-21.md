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

## S2 channel is certificate-gated

The desktop app (Sonos 90.0-79210, `update-software.sonos.com/software/
HBVQeC4A1l/Sonos_90.0-79210.dmg`) embeds the modern request templates:

- `/firmware/swgen/%u/latest/` — per-generation manifest base
- `%supdate.upm?cmaj=%u&cmin=%u&cbld=%u&subm=%u&rev=%u&reg=%u&serial=%s`
- `%s%s-%u-%u.upd?cmaj=%u&cmin=%u&cbld=%u&subm=%u&rev=%u&reg=%u&serial=%s`
- `http://%s:%s/updatefw/%s-%d-%d.upd` — speaker-served UPD relay during
  household updates (transient; nothing cached on idle devices)
- headers `X-Sonos-SWGen` / `X-Sonos-LatestSWGen`

Public probes of `/firmware/swgen/{1,2}/latest/update.upm` (with query) on
`update.sonos.com`, `update-firmware.sonos.com`, `update-origin.sonos.com`,
and `update-software.sonos.com` all return 404 — the swgen path is not
served anonymously. App strings reference `ca.ws.sonos.com/certs/device/
crl0` and `sve.sslauth.sonos.com` (403 without a device client cert), so the
S2 manifest channel is almost certainly mTLS-gated. This explains why the
catalog's recent releases come only from RC dirs: GA dirs for 90.0+/96.x/
97.x exist behind device authentication, carrying the legacy-model packages
(8, 9, 12, 13, 14, 17, 20, 22, 44, 53) that RC dirs omit.

Implication for `96.0-79160-1-25` (the Move build observed on real
hardware): the GA dir token is only disclosed inside the authenticated
channel. A device that legitimately holds that build can reveal the dir —
either via the speaker's `/updatefw/` relay during a real update or via a
manifest fetched with valid device credentials. No unauthenticated path
remains.

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
