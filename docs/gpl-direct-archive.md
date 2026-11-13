# Direct Sonos GPL/LGPL source preservation

The objective is to identify and preserve **every publicly obtainable** original Sonos GPL/LGPL index and its published sources. Do not interpret the absence of a result from a bounded sweep as proof that no other index exists. This archive is independent of Sonos.

## Existing material and provenance

- `data/gpl/wayback-catalog.json` and the nine historical `gpl-<version>` Releases are **legacy captures**. They are not silently rewritten or represented as direct Sonos downloads. Sixteen recorded 7.3 Wayback CDX-vs-local SHA-1 mismatches remain separate audit evidence, but **all 16 historical local files have since been proved SHA-256/size-identical to current direct-Sonos downloads**. Of the 22 historical 7.3 records, 21 files match direct Sonos bytes; only `gpl.html` differs (the older page is preserved alongside the direct copy). The mismatches are not evidence of source-file corruption, and their cause in CDX digest derivation remains undetermined.
- `data/gpl/seed-versions.json` contains the union of the versions in issue #23 and the previous archive. This is a discovery seed, not a completeness certificate.
- `data/gpl/live/catalog.json` and `data/gpl/live/indexes/` contain directly fetched, validated Sonos GPL index bodies and exact SHA-256 checksums. The 39 initial index bodies were fetched straight from Sonos on November 12, 2026; none came from Wayback.
- `data/gpl/live/discovery.json` records probes, including denied and inconclusive responses. A 403 XML `AccessDenied` response is **not** treated as confirmation that a version never existed.
- `data/gpl/live/artifacts.json` records verified sources with size, SHA-256, exact original URL, encoded fetch URL, release asset and publication state. `data/gpl/live/report.json` is a generated coverage summary.
- `data/gpl/live/binary-analysis.json` records what can actually be inferred from disassembled/extracted firmware components versus available sources, without asserting that a matching upstream version proves complete corresponding source.
- `data/gpl/live/legacy-crosscheck.json` is an offline, reproducible comparison of earlier preservation receipts with *independently acquired direct Sonos* source hashes. It never downloads from Wayback or changes the historical CDX metadata. Historical differences and pending comparisons remain explicit.

## Discovery: what gets checked

1. Inspect the archive's existing GPL source indexes and retained firmware metadata first. Collect exact GPL links from docs and JSON manifests, and optionally from supplied firmware/app binary files (`discover --binary FILE`).
2. Sweep a version grammar with **string versions**: 1.0 through 120.99 by default, prioritizing evidence-derived, adjacent and already-observed major releases before speculative distant versions. `14.12` is *not* `14.2`. Run in capped, checkpointed batches, not concurrent requests. Increase grammar bounds if credible evidence warrants it.
3. Enumerate Sonos's actual published website and support sitemap indexes using `scripts/discover_gpl_sitemaps.py` (bounded, recursive, checkpointed, no Wayback). `data/gpl/live/site-evidence.json` contains HTTP/status/sha256 receipts, exact candidate URLs, and related pages. The seeds cover both public robots.txt sitemap indexes and the extra blog/Pro sitemap indexes advertised by Sonos. `discover` prioritizes versions independently linked from these sitemaps for index validation, but never automatically trusts a sitemap link as a verified GPL page. Search-engine indexing is only a hint, not validation.
4. Verify every discovered page by HTTP response **and** expected Sonos GPL HTML and anchor links. Preserve the actual index bytes and SHA-256, not challenge pages or synthetic indexes. Never assume a 200 proves a valid GPL index.
5. Compare the release-specific sources and notices against extracted firmware binaries and build IDs. A binary/source component-version match is a candidate; exact corresponding source must be established independently.

No Wayback requests are made by the new archiver. Only the legacy catalog loader references earlier Wayback metadata for seed versions.

## Downloader

`python scripts/archive_gpl_direct.py indexes --all-known` fetches every known page from Sonos, with at least 2 seconds between requests. To reproduce this archive's November 12 checked snapshot using saved direct-Sonos HTTP 200 bodies (no new network requests), use `--cache-dir /tmp` only if those captured bodies are still available. A fresh run should normally omit `--cache-dir`.

`python scripts/discover_gpl_sitemaps.py --limit 20 --delay 2.5` independently traverses Sonos's public website, blog, Pro, and support XML sitemap indexes using strict host allowlists, SHA-256 receipts, and a resumable checkpoint. It never treats a sitemap link as a verified GPL index. The coverage report includes checked sitemap counts and any newly referenced version leads.

`python scripts/archive_gpl_direct.py discover --major-max 120 --minor-max 99 --limit 48 --delay 2.5` continues unfinished version probes. Responses are saved after *each* request so interruptions do not discard prior evidence. Stop on rate limiting or access challenges. Do not parallelize probes or evade bot controls.

`python scripts/archive_gpl_direct.py archive --version 18.6 --store /path/with/disk-space --include-upstream --publish` downloads source and PDF links, streams data into resumable `.part` files, checks tar integrity or PDF headers, and content-addresses verified assets by SHA-256. A minimum-free-space reserve protects the host. The `+` in Sonos filenames is percent-encoded as `%2B`; on November 12 five 18.6 hyperlinks with literal plus signs returned 403 while percent-encoded URLs served complete source archives. The `--filename NAME` selector permits a bounded targeted analysis.

The `archive --next --limit N` mode advances through **multiple** incomplete releases in version order until the bounded attempt limit is reached; it does not stop after the first partially archived version. A bounded limit is mandatory for `--next`. Failed sources remain explicitly recorded and may be retried with `--retry-errors`.

A GitHub Actions workflow performs twice-daily bounded discovery and four daily staged archival batches (up to 90 attempts each) from a runner with adequate scratch disk space. It uses GitHub Releases tagged `gpl-<version>`, verifies server-side asset SHA-256 after upload, and commits receipts only after verification. If an older Wayback asset has the same name but different SHA-256, the direct-source copy is saved under a **distinct immutable asset name**, not overwritten; the original URL and filename remain in metadata. Sources linked to `ftp.gnu.org` and `www.uclibc.org` are handled separately from Sonos-hosted files.

## Revalidation of already-published source archives

The original preservation passes performed tar-member traversal, but Python's tar reader can stop at the tar end marker *before* checking a compressed stream's final checksum and end marker. A truncated XZ, gzip, or bzip2 archive can therefore have a valid tar member listing but an invalid compressor footer. This was reproduced with three independent failing regression fixtures before strengthening the downloader's verifier.

`python scripts/audit_gpl_published_streams.py --limit 25 --store /path/with-space` re-downloads the **original Sonos or explicitly linked upstream source archive**, validates the *entire* compressed stream and tar members, and requires its size and SHA-256 to equal the immutable GitHub publication receipt. It saves a separate `stream_integrity` record (verified/mismatch/error, time, source method, observed SHA-256) after each attempt; it never changes the original `sha256`, byte count, or publication URL and never overwrites GitHub assets. Checked temporary blobs are removed only after their hashes have been verified. Identical already-published SHA-256 blobs may reuse a proven full-stream check, but only where a previous `tar-and-compression-integrity` receipt with matching observed SHA-256, size, and member count provides an anchor; ungrounded `verified` flags are insufficient.

This check runs daily at a bounded 25 records on a GitHub runner, separate from the four daily source-preservation batches. Use `--version VERSION` to audit a specific release and `--retry-failed` to revisit recorded failures/content discrepancies deliberately. The coverage report explicitly distinguishes source publication from full compression verification and tracks completed releases and pending verification by version. These checks prove the preserved archive is intact relative to the published source bytes, **not** that it is all the corresponding source required by the GPL for a particular firmware binary.

The initial proof, `7.0/bridge-utils.tgz`, was independently re-downloaded from Sonos on November 13, 2026, passed all gzip and tar checks (42 members), and matched SHA-256 `4dc35c8da98a5d5cfa3eb76fcdd7765c86dafc48d89e7e93c184218cefa7b90f` and 63,099 bytes.

## Observed completeness and outstanding questions

At the initial November 12, 2026 audit, all 39 identified index pages were live, containing 1,112 Sonos-hosted links and 297 upstream links, with 13 unique upstream URLs. The expanded audit directly fetched and parsed all 117 XML sitemap documents reachable from the official Sonos website, blog, Pro, and support sitemap roots (including locale-specific child sitemaps), recording SHA-256 and HTTP 200 receipts. None contained a GPL/LGPL index URL or an open-source-specific link, although this **does not** rule out unindexed Sonos-hosted GPL pages. An additional bounded sweep of 206 candidate paths returned 403 `AccessDenied` and did **not** establish new verified GPL pages. The sweep stopped when local disk space became unavailable; GitHub Actions can resume using the checkpoint. **No claim of absolute completeness is justified yet.**

Inspect the 18.6 attribution PDF and compare exact distributed binaries with their released corresponding source, including build scripts and Sonos patches. The five broken published hyperlinks and the retired `/support/downloads/gpl.html` URL are confirmed documentation/accessibility defects. All five source archives were downloaded successfully after URL normalization and passed tar integrity checks; those link failures alone do not establish a GPL violation.

## Firmware source evidence

The [18.6 binary-to-source correspondence audit](gpl-source-correspondence-18.6.md) records actual PowerPC ELF build IDs, matching source versions, and potential missing build configuration without claiming a proven license violation.

The extracted `86.10-80260-1-17` Connect root filesystem contains FFmpeg `5.0.3` and a compiled source debug path `/usr/src/debug/ffmpeg/5.0.3-r0`; the 18.6 GPL page supplies `ffmpeg-5.0.3-r0-patched.tar.xz`. Its BusyBox binary contains `BusyBox v1.35.0`, matching the component version of `busybox-1.35.0-r0-patched.tar.xz`. These are version matches, not proof of byte-for-byte correspondence. An independently preserved `86.8-78270-1-8` kernel has a CRC-valid U-Boot image name `Linux-2.6.35-yocto-standard`, while multiple historical GPL pages list related kernel source variants. The firmware binary checks must be repeated against exact matching product/release artifacts before any compliance conclusion.
