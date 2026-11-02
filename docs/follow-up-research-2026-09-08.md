# Public-source follow-up research — 2026-09-08

Scope: metadata-only checks of official Sonos endpoints and Internet Archive
records. No proprietary firmware was downloaded. Per the requested scope, this
pass excludes Move `96.0-79160` and legal/publication work.

## Results

### 1. The 88 `missing-cdn` candidates

No package was recovered. The catalog already contains an exact official URL
for every candidate; the missing information is a surviving response or
archived capture, not a path guess.

The 88 URLs occupy 15 exact signed-manifest directories:

| Official directory | Missing candidates |
|---|---:|
| `34.16-37101-v7.1-nzhemz-LR-1` | 2 |
| `38.9-46251-v8.1.1-mvckho-LR-1` | 1 |
| `57.14-37030-v11.7-oxmdyztc-GA-1` | 7 |
| `57.22-59130-v11.15-72ynP0v9lu-GA-2` | 7 |
| `57.22-63071-v11.15.3-pkAzZSYXYB-SP-4` | 7 |
| `57.23-74170-v11.16.1-DILU1Q33hC-GA-1` | 7 |
| `62.1-86220-v13.0-mtbvge-GA-1` | 4 |
| `65.1-21040-v13.3-maswnk-GA-1` | 6 |
| `66.4-23090-v13.4-xgqmdq-GA-1` | 6 |
| `67.1-27100-v14.4-bjaiyg-GA-1` | 4 |
| `80.1-56190-v16.3.3-5g1N0fceQI-GA-1` | 1 |
| `90.0-67171-v17.2-ZethjbGivZ-GA-1` | 9 |
| `91.0-68261-v17.4-uG1Czb1YW1-RC-1` | 9 |
| `96.0-78270-v18.6-xhPUjlijVZ-RC-1` | 9 |
| `96.1-79270-v18.7-4b07MiHfnw-RC-2` | 9 |

Exhaustive checks performed:

- `HEAD` was issued for all 88 exact paths on both
  `update-firmware.sonos.com` and the alternate official host
  `update.sonos.com`: **176/176 returned 404** on 2026-09-08. This independently
  confirms and extends the repository's 2026-09-03 88/88 primary-host result.
- The Internet Archive's exact-URL
  [Wayback Availability API](https://archive.org/wayback/available) was queried
  for all 88 canonical `update-firmware.sonos.com` URLs: **0 snapshots, 0 query
  errors**. This exact-URL check avoids false positives from directory-prefix
  captures.
- A fresh [Wayback CDX](https://web.archive.org/cdx/search/cdx) directory-prefix
  pass ultimately completed all 15 directory queries with zero errors and no
  exact package match (`data/discovery/missing-wayback-audit-2026-09-08.json`).
  Two rows were returned, both already-cataloged `.upm` manifests. An earlier
  pass encountered rate limits on seven directories; the completed repository
  receipt supersedes that partial run.
- The alternate-host Wayback check completed for 20 URLs with zero snapshots;
  the remaining 68 requests were rate-limited. This is residual uncertainty
  only for historical aliases on `update.sonos.com`; every such live alias was
  checked and is currently 404.

Actionable lead: the official desktop-download CDN uses a different namespace.
`https://update-software.sonos.com/software/72ynP0v9lu/Sonos_57.22-59130.dmg`
still answers `200` with `Content-Type: application/octet-stream` and
`Content-Length: 49,055,471` (metadata-only `HEAD`, 2026-09-08). This is not one
of the 88 missing artifacts, but the token matches the `57.22-59130` firmware
directory and demonstrates a concrete alternate official path convention worth
checking when future signed metadata exposes desktop artifacts. It does not
justify guessing tokens for the two missing historical desktop files.

### 2. Official version/date evidence cataloged

Sonos's live [system release notes](https://support.sonos.com/en-us/article/release-notes-sonos-system-updates)
contain 34 dated release entries representing 35 explicit build numbers. Seven
builds already occur among catalog packages; the following **30 build/date
facts were absent at the start of this pass** and are now retained as official
`evidenced-version` records:

| Date | System build(s) | Legacy/product-specific build(s) |
|---|---|---|
| 2026-09-08 | `97.1-80312` | — |
| 2026-06-09 | `95.1-78010` | — |
| 2026-05-12 | `95.0-77060` | `86.7-77050` |
| 2026-04-14 | `94.1-76070` | — |
| 2026-03-31 | `94.1-75140` | — |
| 2026-03-10 | `94.1-75011` | `86.6-75010` |
| 2026-02-18 | `93.1-74010` | — |
| 2026-01-27 | `93.1-73190` | `86.4-73190`; Play:1/Play:3 `86.4-73220` |
| 2026-01-06 | `92.0-71170` | selected products `92.0-72171` |
| 2025-12-16 | `92.0-71170` | selected products `92.0-72090` |
| 2025-12-03 | `92.0-71170` | — |
| 2025-11-04 | `92.0-70280` | `86.2-70230` |
| 2025-10-08 | `91.0-70011` | — |
| 2025-09-02 | `90.0-68140` | — |
| 2025-07-08 | `85.0-66270` | — |
| 2025-06-10 | `85.0-65270` | — |
| 2025-05-06 | `85.0-64200` | — |
| 2025-04-15 | `84.1-64070` | — |
| 2025-03-17 | `84.1-63110` | — |
| 2025-02-19 | `83.1-62052` | — |
| 2025-02-04 | `83.1-61240` | — |
| 2025-01-28 | `83.1-61130` | — |
| 2025-01-07 | `82.3-60160` | — |

The same import adds official date provenance for these seven already
cataloged builds: `96.1-79270` (2026-08-11), `96.0-78270` and legacy
`86.8-78270` (2026-07-14), `91.0-68261` (2025-09-16), `90.0-67171`
(2025-08-05), `82.2-59204` (2024-12-10), and `81.1-58210` (2024-10-29).

Important limitation: Sonos states on that page that it lists major system
updates and may omit product-specific minor updates. These entries are strong
version/date evidence, but the page is not a completeness oracle and provides
no package directory tokens.

### 3. GPL 7.3 digest mismatches

No alternate exact capture was found in this pass. The 16 affected official
paths are the attribution PDF plus `bridge-utils.tgz`, `busybox1.tgz`,
`busybox2.tgz`, `busybox3.tgz`, `fscrypt.tgz`, the six cataloged Linux source
archives, `mpg123.tgz`, `rng-tools.tgz`, `salsa-lib-0.0.13.tgz`, and
`udhcp-0.9.8.tgz`.

The repository's CDX audit identifies one successful capture timestamp for
each source archive, all on 2017-08-22. The attribution PDF has two additional
captures (`20240518020433` and `20241222124844`), but both share a different
digest and neither matches the expected 2017 CDX SHA-1. Exact per-file queries
and the release-prefix query completed successfully; searches across exact
HTTP/HTTPS forms, wildcard Sonos hostnames, query-string variants, and
filename-wide Sonos paths did not expose another matching capture. The query
template was
`https://web.archive.org/cdx/search/cdx?url=www.sonos.com/documents/gpl/7.3/FILENAME&matchType=exact&output=json&fl=timestamp,original,statuscode,mimetype,digest,length&filter=statuscode:200&limit=10000`.
The live HTTPS paths currently return a Sonos/Akamai `403`
for both `HEAD` and a one-byte range request, so live-byte comparison was not
possible without bypassing the site's access controls. The current files
should therefore remain labeled `replay-transformed`: they are usable replay
outputs, but their bytes do not equal the payload identified by the archived
CDX SHA-1 digest.

Actionable recovery paths are now narrow: obtain a WARC/CDX payload from an
Internet Archive data export or locate a byte-identical copy from the original
2017 Sonos publication. A normal Wayback replay of the already known timestamp
cannot establish an exact match.

## Bottom line

- Recovered missing proprietary packages: **0/88**.
- Exact canonical URLs checked against live Sonos and Wayback: **88/88**.
- Official version/date evidence cataloged: **37 facts**—30 newly represented
  build/date facts plus dates for seven builds already represented by packages.
- GPL 7.3 mismatches resolved: **0/16**; no alternate public capture surfaced.
