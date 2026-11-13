# Direct Sonos GPL/LGPL source preservation

The objective is to identify and preserve **every publicly obtainable** original Sonos GPL/LGPL index and its published sources. Do not interpret the absence of a result from a bounded sweep as proof that no other index exists. This archive is independent of Sonos.

## Existing material and provenance

- `data/gpl/wayback-catalog.json` and the nine historical `gpl-<version>` Releases are **legacy captures**. They are not silently rewritten or represented as direct Sonos downloads. Sixteen recorded 7.3 Wayback-vs-local SHA-1 mismatches remain separate audit evidence.
- `data/gpl/seed-versions.json` contains the union of the versions in issue #23 and the previous archive. This is a discovery seed, not a completeness certificate.
- `data/gpl/live/catalog.json` and `data/gpl/live/indexes/` contain directly fetched, validated Sonos GPL index bodies and exact SHA-256 checksums. The 39 initial index bodies were fetched straight from Sonos on November 12, 2026; none came from Wayback.
- `data/gpl/live/discovery.json` records probes, including denied and inconclusive responses. A 403 XML `AccessDenied` response is **not** treated as confirmation that a version never existed.
- `data/gpl/live/artifacts.json` records verified sources with size, SHA-256, exact original URL, encoded fetch URL, release asset and publication state. `data/gpl/live/report.json` is a generated coverage summary.
- `data/gpl/live/binary-analysis.json` records what can actually be inferred from disassembled/extracted firmware components versus available sources, without asserting that a matching upstream version proves complete corresponding source.

## Discovery: what gets checked

1. Inspect the archive's existing GPL source indexes and retained firmware metadata first. Collect exact GPL links from docs and JSON manifests, and optionally from supplied firmware/app binary files (`discover --binary FILE`).
2. Sweep a version grammar with **string versions**: 1.0 through 120.99 by default, prioritizing evidence-derived, adjacent and already-observed major releases before speculative distant versions. `14.12` is *not* `14.2`. Run in capped, checkpointed batches, not concurrent requests. Increase grammar bounds if credible evidence warrants it.
3. Enumerate Sonos's official sitemaps, support pages and legal references for additional direct links. At the November 12 inspection, `https://www.sonos.com/en/sitemap.xml` contained no GPL URL. Search-engine indexing is only a hint, not validation.
4. Verify every discovered page by HTTP response **and** expected Sonos GPL HTML and anchor links. Preserve the actual index bytes and SHA-256, not challenge pages or synthetic indexes. Never assume a 200 proves a valid GPL index.
5. Compare the release-specific sources and notices against extracted firmware binaries and build IDs. A binary/source component-version match is a candidate; exact corresponding source must be established independently.

No Wayback requests are made by the new archiver. Only the legacy catalog loader references earlier Wayback metadata for seed versions.

## Downloader

`python scripts/archive_gpl_direct.py indexes --all-known` fetches every known page from Sonos, with at least 2 seconds between requests. To reproduce this archive's November 12 checked snapshot using saved direct-Sonos HTTP 200 bodies (no new network requests), use `--cache-dir /tmp` only if those captured bodies are still available. A fresh run should normally omit `--cache-dir`.

`python scripts/archive_gpl_direct.py discover --major-max 120 --minor-max 99 --limit 48 --delay 2.5` continues unfinished version probes. Responses are saved after *each* request so interruptions do not discard prior evidence. Stop on rate limiting or access challenges. Do not parallelize probes or evade bot controls.

`python scripts/archive_gpl_direct.py archive --version 18.6 --store /path/with/disk-space --include-upstream --publish` downloads source and PDF links, streams data into resumable `.part` files, checks tar integrity or PDF headers, and content-addresses verified assets by SHA-256. A minimum-free-space reserve protects the host. The `+` in Sonos filenames is percent-encoded as `%2B`; on November 12 five 18.6 hyperlinks with literal plus signs returned 403 while percent-encoded URLs served complete source archives. The `--filename NAME` selector permits a bounded targeted analysis.

A GitHub Actions workflow performs twice-daily bounded discovery and daily staged archival from a runner with adequate scratch disk space. It uses GitHub Releases tagged `gpl-<version>`, verifies server-side asset SHA-256 after upload, and commits receipts only after verification. If an older Wayback asset has the same name but different SHA-256, the direct-source copy is saved under a **distinct immutable asset name**, not overwritten; the original URL and filename remain in metadata. Sources linked to `ftp.gnu.org` and `www.uclibc.org` are handled separately from Sonos-hosted files.

## Observed completeness and outstanding questions

At the initial November 12, 2026 audit, all 39 identified index pages were live, containing 1,112 Sonos-hosted links and 297 upstream links, with 13 unique upstream URLs. An additional bounded sweep of 206 candidate paths returned 403 `AccessDenied` and did **not** establish new verified GPL pages. The sweep stopped when local disk space became unavailable; GitHub Actions can resume using the checkpoint. **No claim of absolute completeness is justified yet.**

Inspect the 18.6 attribution PDF and compare exact distributed binaries with their released corresponding source, including build scripts and Sonos patches. The five broken published hyperlinks and the retired `/support/downloads/gpl.html` URL are confirmed documentation/accessibility defects. All five source archives were downloaded successfully after URL normalization and passed tar integrity checks; those link failures alone do not establish a GPL violation.

## Firmware source evidence

The [18.6 binary-to-source correspondence audit](gpl-source-correspondence-18.6.md) records actual PowerPC ELF build IDs, matching source versions, and potential missing build configuration without claiming a proven license violation.

The extracted `86.10-80260-1-17` Connect root filesystem contains FFmpeg `5.0.3` and a compiled source debug path `/usr/src/debug/ffmpeg/5.0.3-r0`; the 18.6 GPL page supplies `ffmpeg-5.0.3-r0-patched.tar.xz`. Its BusyBox binary contains `BusyBox v1.35.0`, matching the component version of `busybox-1.35.0-r0-patched.tar.xz`. These are version matches, not proof of byte-for-byte correspondence. An independently preserved `86.8-78270-1-8` kernel has a CRC-valid U-Boot image name `Linux-2.6.35-yocto-standard`, while multiple historical GPL pages list related kernel source variants. The firmware binary checks must be repeated against exact matching product/release artifacts before any compliance conclusion.
