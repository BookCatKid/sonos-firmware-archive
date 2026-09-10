# Sonos firmware source and discovery research

Research date: 2026-08-31. This is an evidence map and collection plan, not a claim of completeness. It deliberately contains no device identifiers, household identifiers, private keys, or firmware binaries.

Update 2026-09-03: full re-verification pass (receipt
`data/discovery/reverify-2026-09-03.json`). All 88 `missing-cdn`
candidates returned HTTP 404 via metadata-only `HEAD` on 2026-09-03,
while preserved controls (for example `57.22-59130-1-8`) still return
`200`, confirming the probe path. This includes the 18 remaining
exact-version candidates from the three diagnostic manifests; the rest
are conditional milestone/desktop artifacts. Wayback CDX on both
`update-firmware.sonos.com` and `update.sonos.com` returns no captures
matching `96.0-79160`, and none of the 19 source manifests contains that
string, so `96.0-79160` remains `missing-exact-package` (version
evidenced, package URL unknown; opaque tokens are not brute-forced per
section 4). Fifteen mismatched GPL `7.3` source archives have only a single
2017-08-22 capture each; the mismatched attribution PDF has two later captures,
but both carry a different digest. No alternate matching capture exists. All
20 local `.tgz` files open as valid tar archives and remain labeled
`replay-transformed` until exact captures are recovered.

The live discovery feed is now reproducibly covered too. A raw synthetic
`default-1-1.ups` response and its redacted parsed receipt are retained under
`data/metadata/`. The request uses only the public `sonostool` placeholder
identifiers, and the receipt omits even those placeholder values. The captured
response validates as 11 size-delimited records and contains the S1 base URI
and manifest URI described below. Sonos serves this binary response with the
misleading `text/html` content type, so validation relies on the record framing,
magic value, byte count, and SHA-256 rather than MIME type alone.

Update 2026-09-08: every one of the 88 `missing-cdn` URLs was checked again
against both official update hosts. All 176 host/path combinations return
`404`, while all 13 preserved same-directory controls return `200`
(`data/discovery/missing-live-audit-2026-09-08.json`).
The 15 exact opaque release directories were also queried through Wayback CDX
without errors (`data/discovery/missing-wayback-audit-2026-09-08.json`). CDX
contains two already-cataloged `.upm` captures in those directories but no
exact capture for any of the 88 missing packages. This closes the currently
known live-CDN and exact-directory Wayback leads without pretending that an
uncrawled private copy cannot exist.

Update 2026-09-02: the three exact diagnostic leads from Home Assistant issue
reports were recovered, validated against official live URLs, and archived:
`67.1-27100`, `57.22-59130`, and `57.14-37030`. Only redacted
`AvailableSoftwareUpdate` fields and public issue permalinks were retained;
no issue bodies, device identifiers, household IDs, serials, room names, or LAN
addresses were persisted. Discovery observed 111 live candidate responses
across the three manifests; after deduplication against earlier campaign
coverage, 63 artifacts from the three exact versions are preserved and 18
remaining exact-version candidates are cataloged as `missing-cdn`. The
63 packages are organized under one `firmware-<system-version>` Release per
update. Exact `.upm` snapshots remain in a separate manifest snapshot Release.

Update 2026-09-01: historical local task logs recovered the retired official
campaign identifier `2026-Sonos-16-oTC1hrxrvi-GA-1`. The URL remains live, but
its response body was replaced in place with a signed `96.1-79270` manifest
(revision `22ed0620-ee2c-4b83-811e-9d54cf6840db`). The archive records that
body as a mutable-campaign observation, not as evidence for a `96.0-79160`
package path. This demonstrates why every manifest body needs an immediate
content hash rather than provenance based on URL alone.

The same history also recovered two more live official manifest URLs. The
`57.22-68080-v11.15.6-NUPz7mPXln-SP-8` URL still serves a signed S1 manifest
with system version `57.22-68080` and default version `57.22-63071`. The retired
`2026-Sonos-14-WyD18XznPL-GA-1` campaign URL currently serves a signed S2
manifest with system/default version `96.0-78270` and revision
`e8411407-9584-4831-a637-2fde268c67a4`. Both exact response bodies and their
expanded discovery receipts are cataloged. The `96.0-78270` campaign is useful
historical coverage but is not evidence for the distinct Move `96.0-79160`
build.

## Bottom line

The largest defensible archive will need to combine five veins:

1. live Sonos update metadata for modern S2, legacy S2, and S1;
2. every URL and conditional upgrade rule in each signed Sonos `.upm` manifest;
3. historical manifests and package mappings already preserved in public source repositories and bug reports;
4. direct Internet Archive CDX evidence; and
5. Sonos's separately published GPL/LGPL source releases.

A version number alone is not enough to reconstruct a download URL. Sonos CDN directories include opaque release/channel tokens, for example `96.1-79270-v18.7-4b07MiHfnw-RC-2`. Preserve each manifest and every complete URL as soon as it is observed.

## 1. Live Sonos metadata

### Discovery feed

An update client requests:

```text
GET https://update.sonos.com/firmware/latest/default-1-1.ups
    ?cmaj=...&cmin=...&cbld=...&subm=...&rev=...&reg=...
    &serial=...&sonosid=...&householdid=...
```

This endpoint is not documented in Sonos's public API documentation, but it is implemented in the public `sonostool` source. The code defines TLV type `7` as the base firmware URI and type `14` as the manifest URI, sends the request, and expands the returned caret URL into a model package ([constants and TLV types](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L47-L56), [request and extraction](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L182-L216)). A live request with dummy values on the research date returned a valid S1 response, demonstrating that synthetic probes do not necessarily select the modern S2 branch.

The binary `.ups` records use a 16-byte little-endian header `(magic, type, size, unknown)` with magic `0x35167f49`. Archive the raw response and parsed fields, but never persist a real query string: `serial`, `sonosid`, and `householdid` are user/device identifiers. Store only a redacted request profile such as controller version, region, and software generation.

The best source for the exact active branch is a real device's read-only `AvailableSoftwareUpdate` / `R_AvailableSoftwareUpdate` state. Public Home Assistant diagnostics demonstrate that this field contains `Version`, `UpdateURL`, `ManifestURL`, `Swgen`, and `ManifestRevision` for both [S1 57.14-37030](https://github.com/home-assistant/core/issues/88486) and [S1 57.22-59130 plus S2 81.1-58210](https://github.com/home-assistant/core/issues/131381). Extract only those update fields; public diagnostics often contain room names, LAN addresses, UUIDs, and household IDs that must not enter this archive.

### Signed manifests and URL expansion

Sonos `.upm` files are signed XML. Preserve the exact bytes, including the trailing `SIGNATURE` comment. Important root fields are `revision`, `system_version`, `default_version`, `base_url`, and `swgen`. `supported_models` contains numeric model/submodel selectors. Each `<image>` rule may add `submodel_min`, `submodel_max`, `fromver_min`, `fromver_max`, `flags`, and `milestone_index`; those conditions are part of the historical record and must not be flattened away.

For the common caret form:

```text
.../RELEASE-DIRECTORY/^VERSION
```

the observed package expansion is:

```text
.../RELEASE-DIRECTORY/VERSION-1-MODEL.upd
```

This is directly implemented by `sonostool` for variant/submodel `1` and a numeric model ([source](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L207-L216)). Treat this as an observed pattern, not a timeless guarantee: retain the original manifest expression and validate every expanded candidate before cataloguing it.

Use a metadata-only `HEAD` first, falling back to a small ranged `GET` if a CDN does not implement `HEAD`. Require a successful status, `application/octet-stream`, a plausible length, and then hash the full artifact after download. On 2026-08-31 these official examples still answered `200`:

| Version/model | Official URL | Content length |
|---|---|---:|
| 57.19-46310 / 8 | [57.19-46310-1-8.upd](https://update-firmware.sonos.com/firmware/Prod/57.19-46310-v11.12-rozcvdwa-GA-1/57.19-46310-1-8.upd) | 9,059,978 |
| 73.0-42060 / 8 | [73.0-42060-1-8.upd](https://update-firmware.sonos.com/firmware/Prod/73.0-42060-v15.5-eqsrsbpq-GA-1/73.0-42060-1-8.upd) | 11,872,089 |
| 86.8-78270 / 9 | [86.8-78270-1-9.upd](https://update-firmware.sonos.com/firmware/Prod/86.8-78270-v17.2.6-HlGdHczqmy-RC-1/86.8-78270-1-9.upd) | 15,886,259 |

### Three active preservation tracks

- **Modern S2:** the current signed [96.1-79270 manifest](https://update.sonos.com/firmware/Prod/2026-Sonos-17-aiVIZ66IGK-GA-1/update.upm) has default base `96.1-79270-v18.7-4b07MiHfnw-RC-2` and a large S2 model list.
- **Legacy S2:** the same manifest routes selected older S2 models to `86.8-78270`. Sonos's [system release notes](https://support.sonos.com/en-us/article/release-notes-sonos-system-updates) explicitly distinguish the current system and current legacy system versions, while warning that product-specific minor updates may not be listed.
- **S1:** a live metadata probe returned base `57.23-74170-v11.16.1-DILU1Q33hC-GA-1` and signed manifest [57.23-80060](https://update-firmware.sonos.com/firmware/Prod/57.23-80060-v11.16.2-9tmT45qD4t-SP-1/update_1787082718.upm). This is a separate line from what Sonos now labels “legacy” in the S2 release notes.

The current modern manifest is also a historical gold mine. Its conditional rules expose the exact opaque directories for upgrade milestones `34.16-37101`, `55.1-74250`, `57.5-87010`, `57.19-46310`, `73.0-42060`, and `86.8-78270`, in addition to `96.1-79270`. The current S1 manifest adds `25.2-50130`, `36.5-50160`, and `45.1-56150`, while its default points to `57.23-74170`. Expand each rule only across the models and version ranges to which it applies.

Sonos's [app compatibility matrix](https://support.sonos.com/en-ca/article/sonos-app-version-compatibility) is authoritative for product names and S1/S2 eligibility, but it does not map those names to numeric manifest model IDs. Keep numeric-model mappings evidence-scoped. For example, a preserved 2021 manifest/package collection explicitly maps model `26` to Sonos One, `32` to Sub Gen 3, and `20` to IKEA Bookshelf ([13.4 index](https://github.com/systemcrash/sonos-firmware/blob/47eb96ae5415de7088659bb3cb8afeaeec68415f/README.md#L7-L24)); the Fenway research repository says model `0x08` is a hardware family including Play:1 and other products, not a one-product identity ([source](https://github.com/trulyspinach/sonos-fenway#what)).

## 2. Historical manifests and exact-version leads

### Public repositories with actual artifacts or mappings

The public [systemcrash/sonos-firmware](https://github.com/systemcrash/sonos-firmware) repository preserves signed manifests and selected packages for S2 `13.0`, `13.3.2`, and `13.4`. Its committed manifests contain exact Sonos CDN paths and conditional milestone URLs:

- [62.1-87200 / S2 13.0.2 manifest](https://github.com/systemcrash/sonos-firmware/blob/47eb96ae5415de7088659bb3cb8afeaeec68415f/62.1-87200-v13.0.2-vqwxip-GA-1/update.upm)
- [65.1-21040 / S2 13.3 manifest](https://github.com/systemcrash/sonos-firmware/blob/47eb96ae5415de7088659bb3cb8afeaeec68415f/65.1-21040-v13.3-maswnk-GA-1/update.upm)
- [66.4-23130 / S2 13.4 manifest](https://github.com/systemcrash/sonos-firmware/blob/47eb96ae5415de7088659bb3cb8afeaeec68415f/66.4-23130-v13.4-ezhbor-GA-3/update.upm)

Treat third-party repositories as provenance leads. Independently hash every blob, preserve the commit SHA and original path, and prefer the official Sonos URL whenever it is still live. The repository has no declared license, so its presence on GitHub is not itself permission to republish.

Public issue diagnostics reveal additional exact directories that are otherwise difficult to guess, including [67.1-27100](https://github.com/home-assistant/core/issues/69759), [57.14-37030](https://github.com/home-assistant/core/issues/88486), and [57.22-59130 / 81.1-58210](https://github.com/home-assistant/core/issues/131381). Search issue bodies for `ManifestURL`, `UpdateURL`, and `AvailableSoftwareUpdate`, but retain only firmware metadata and a source permalink.

First-party Sonos staff announcements can supply version evidence even when the package token is missing. For example, the [28 July 2026 announcement](https://en.community.sonos.com/product-updates/28th-july-2026-new-sonos-app-player-updates-now-available-6934395) identifies modern `96.0-79160` and legacy `86.8-78270`. Record `96.0-79160` as “version evidenced, package URL unknown” until a signed manifest, complete CDN URL, or matching blob is found; do not guess an opaque directory token.

The public [trulyspinach/sonos-fenway](https://github.com/trulyspinach/sonos-fenway) tree contains historical Fenway dumps/raw images labeled `16.6-00002-diag`, `18.0-50150`, `20.2-01616-diag`, and `57.10-25140`. These are useful hash/version leads but include modified/custom material too. Catalog `stock`, `diagnostic`, `custom`, and `unknown` as separate artifact classes, and never treat a custom image as an official Sonos release.

### Internet Archive CDX evidence

Query CDX by prefix and extension rather than relying on normal web search. On 2026-08-31, this direct [CDX query for `.upd` captures](https://web.archive.org/cdx/search/cdx?url=update-firmware.sonos.com/firmware/Prod/&matchType=prefix&output=json&fl=timestamp,original,statuscode,mimetype,digest,length&filter=statuscode:200&filter=original:.*%5C.upd%24&collapse=urlkey&limit=10000) returned only two distinct successful package URLs:

- `34.16-37101-1-16.upd`, captured 2022-02-14, archived length 4,693,821;
- `57.15-39070-1-26.upd`, captured 2023-06-19, archived length 39,866,595.

The analogous [CDX query for `.upm` captures](https://web.archive.org/cdx/search/cdx?url=update-firmware.sonos.com/firmware/Prod/&matchType=prefix&output=json&fl=timestamp,original,statuscode,mimetype,digest,length&filter=statuscode:200&filter=original:.*%5C.upm%24&collapse=urlkey&limit=10000) returned manifests for `80.1-56190`, `81.1-58074`, `81.1-58210`, `82.2-59204`, and `90.0-67171`; a separate query against `update.sonos.com` returned one year-token manifest. Re-run both hosts because manifests have appeared under both `update.sonos.com` and `update-firmware.sonos.com`.

CDX results are direct evidence of a capture, not a completeness oracle. Missing rows can mean no crawl, access restrictions, deduplication, robots policy, or a capture stored under a different hostname/path. Validate replay content and record both CDX digest and a locally computed SHA-256. Do not treat a Wayback redirect or error page as firmware merely because its status is `200`.

Historical snapshots of the [Sonos system release-notes page](https://support.sonos.com/en-us/article/release-notes-sonos-system-updates) and Sonos staff update posts should be mined for version/date evidence, then correlated with manifests and CDX. Sonos explicitly says the live release page omits some product-specific minor updates, so it cannot define completeness by itself.

## 3. Official GPL/LGPL source releases

Archive these as a separate, clearly licensed collection. Sonos's current [14.18 GPL/LGPL index](https://www.sonos.com/documents/gpl/14.18/gpl.html) publishes attribution documents and patched sources including BusyBox, FFmpeg, drivers, and `linux-sonos` trees for 2.6.35, 2.6.39.4, 3.10.53-nxp, 4.4.24-mtk, and 4.9.99. Historical official indexes still available include:

- [7.2](https://www.sonos.com/documents/gpl/7.2/gpl.html) and [7.3](https://www.sonos.com/documents/gpl/7.3/gpl.html)
- [9.2](https://www.sonos.com/documents/gpl/9.2/gpl.html)
- [10.2](https://www.sonos.com/documents/gpl/10.2/gpl.html) and [10.6](https://www.sonos.com/documents/gpl/10.6/gpl.html)
- [12.0](https://www.sonos.com/documents/gpl/12.0/gpl.html)
- [13.2](https://www.sonos.com/documents/gpl/13.2/gpl.html)
- [14.4](https://www.sonos.com/documents/gpl/14.4/gpl.html)

For each index, preserve the HTML, attribution PDF(s), every first-party source archive, original filename/URL, retrieval time, response headers, and SHA-256. Deduplicate storage by hash while retaining every release-page alias. Do not mix GPL source bundles with proprietary `.upd` licensing or imply that a GPL component archive is a complete buildable firmware image.

Update 2026-09-02: the Sonos-hosted `7.2`, `7.3`, `9.2`, `10.2`, `10.6`, `12.0`,
`13.2`, `14.4`, and `14.18` indexes were archived through Wayback with 167
first-party artifact records in `data/gpl/`. The importer records each artifact's
original URL, Wayback timestamp, local SHA-256, and CDX SHA-1 digest. Wayback
currently reports 151 exact CDX digest matches; the 16 remaining `7.3` records
are valid downloadable files but are labeled as replay-transformed because
Wayback returned different bytes and lengths from its CDX payload digest. Keep
this distinction explicit until exact captures are recovered.

In practical terms, `replay-transformed` does not mean the files are broken or
missing: all 20 release-7.3 `.tgz` files open as tar archives. It means 16
downloaded files (15 source archives and one attribution PDF) are not
byte-for-byte identical to the payload hash recorded in Wayback's CDX index.
Metadata alone cannot establish whether replay transformation or a changed
payload caused the mismatch. The files are retained as usable, best-available
copies but are not mislabeled as exact historical bytes.

Update 2026-09-02: all 167 archived GPL/LGPL files were published to the
separate `gpl-7.2` through `gpl-14.18` releases and were re-audited by exact
asset filename and byte count against the local capture catalog.

## 4. Recommended collection order

1. **Snapshot metadata immediately:** fetch and hash the modern S2 and S1 `.ups` responses and every referenced `.upm`; parse without discarding signatures or conditional fields.
2. **Enumerate manifest candidates:** expand each caret URL only for its matching numeric models/submodels and version ranges. Include default bases and every milestone rule.
3. **Validate, then fetch:** `HEAD`/ranged-GET candidates, reject non-binary/error responses, download accepted assets once, hash with SHA-256, and preserve HTTP metadata.
4. **Backfill known historical manifests:** import the three signed manifests from `systemcrash/sonos-firmware`, the five-plus CDX manifest captures, and exact URLs from public diagnostics. Attempt official CDN retrieval before using a third-party blob.
5. **Mine evidence-only versions:** Sonos release notes, historical snapshots, and staff announcements. Mark these `evidenced-version` until a package or manifest is found.
6. **Crawl GPL indexes separately:** archive all official source bundles and attribution documents under their own licenses.
7. **Poll conservatively:** a daily metadata check is sufficient. Cache by manifest revision/URL, use conditional requests, and avoid brute-forcing opaque directory tokens or sending device-specific requests at scale.

As of 2026-09-08, steps 1–6 have been executed for every currently known
source: the raw synthetic `.ups` snapshot is preserved, all 19 source manifests
have been expanded, live candidates and available artifacts have receipts,
known repository/diagnostic/CDX leads have been backfilled, 37 official
release-note build/date facts have been cataloged, and all nine listed GPL
release families are archived. No Codex heartbeat is used and automatic
monitoring is not currently running; step 7 is manual unless a repository-
native scheduled workflow is added later. The remaining `missing-cdn` and
`replay-transformed` labels describe external evidence limits, not unexecuted
collection steps.

Suggested provenance states are `official-live`, `official-wayback-replay`, `third-party-byte-identical-to-official`, `third-party-unverified`, `device-observed-version`, `first-party-announcement-only`, `diagnostic`, `custom`, and `missing`. “Missing” should always state exactly which sources and dates were checked.

## 5. Legal and publication considerations

This is not legal advice. Sonos's [U.S. terms](https://www.sonos.com/en-us/legal/terms-of-use#software) state that product software is licensed rather than sold and restrict transfer, copying, and reverse engineering except where applicable law overrides. GPL/LGPL archives are different: Sonos publishes them specifically under the component licenses and attribution terms.

For now:

- keep proprietary `.upd` files and extracted images in the private repository/release storage;
- keep MIT licensing scoped to archive tooling and original metadata, not firmware blobs;
- retain Sonos copyright/license notices and each artifact's provenance;
- never commit model private keys, device OTP/MDP dumps, serials, Sonos IDs, household IDs, room names, or LAN addresses;
- add a contact/takedown policy before wider sharing; and
- obtain legal review before making proprietary packages or extracted files public.
