# Archive follow-up acceptance checklist

This tracks the follow-up scope for [PR #26](https://github.com/BookCatKid/sonos-firmware-archive/pull/26).
Related issues: [#23](https://github.com/BookCatKid/sonos-firmware-archive/issues/23),
[#24](https://github.com/BookCatKid/sonos-firmware-archive/issues/24),
[#25](https://github.com/BookCatKid/sonos-firmware-archive/issues/25).
Issue references do not authorize comments or closure; all issues remain open.

Existing tests and validation runs are starting evidence. Each requirement
needs evidence at its stated scope before it can be marked complete. Payload
acquisition and private-key handling stay in CI; local tests use synthetic data.
Public manifest repositories are comparison evidence only. Their manifests,
repository lists and version URLs must not become runtime discovery inputs.
Historical discovery must come from Sonos protocols, source traversal and
independent web-archive indexes.

| Item | Acceptance evidence required | Follow-up status |
| --- | --- | --- |
| 1 | CI publishes the mutable ConnectX source and verifies its catalog asset. | Isolated CI published 86.4-73207; Release metadata matches the catalog's size and SHA-256. Production validation remains item 21. |
| 2 | Published Anacapa, other extractable components and an architecture/version/provenance inventory, without fixed offsets. | CI published all 11 raw ConnectX components plus a complete inventory and extraction bundle. The inventory records 373 filesystem/member entries and identifies Anacapa as ELF32, big-endian PowerPC. Published inventory/bundle sizes and digests match their receipts; production validation remains item 21. |
| 3 | Independent historical ConnectX discovery, including distinct captures at reused URLs. | 38 firmware versions advertise a model-17 (ConnectX) component; 31 are preserved as 31 distinct contents, every one manifest-attributed, with 0 coverage inconsistencies. The 7 remaining are 404 at the source with no Wayback or Common Crawl capture. Reused-URL capture is proven by a test where one mutable URL yields two distinct preserved versions. See [ConnectX historical discovery](#connectx-historical-discovery-item-3). |
| 4 | Every installer reported in #25 is compared with independently discovered candidates; omissions have discovery fixes. | Comparison found incomplete native coverage. Desktop/mobile workers now consume native manifest observations; traversal now checks channel counterparts even after a failed manifest. Live comparison and preservation validation continue. |
| 5 | Reproduced TLS failure has a working verified transport; CLI clearly identifies a JSON output file. | CLI and certificate-verified TLS fallback fixed; offline and PR CI checks pass. |
| 6 | Independently found GPL indexes and retrievable sources are published and stream-verified, including encoded plus signs. | 39 verified indexes, 1,409 advertised links: 1,408 published and stream-verified. The `+`→`%2B` failure is reproduced and proven live (literal `+` → 403, `%2B` → 200 with bytes matching the published receipt). A new coverage audit found the one remaining link: a valid xz stream containing zero members, correctly rejected as `empty-tar`. See [GPL index and source coverage](#gpl-index-and-source-coverage-item-6). |
| 7 | Historical index TLS, pagination, timeout, query-resume and replay behavior is verified; independent alternatives investigated. | Common Crawl collection/page discovery and native WARC/ARC replay implemented alongside Wayback; independent host backoff and exact-range publication covered offline. Live coverage validation pending. |
| 8 | Anonymous Google Play acquisition yields a signer-verified delivery through goopdl. | CI isolates HTTP 403 at Aurora's token dispenser, before Play API calls; compare transports and playpull protocol paths. |
| 9 | APKPure pagination, SDK/ABI variants, refreshed tickets and expired links continue making historical progress. | Extend existing live evidence and audit coverage. |
| 10 | All advertised Android delivery components are preserved, with signatures, version checks and upload recovery. | Store and official APKs now share signature/manifest inspection; receipt migration and interrupted-upload verification are tested. Audit full SDK coverage and live receipts. |
| 11 | Current and historical official mobile captures, including reused URLs, are independently discovered and preserved. | Live CI reinspection verified both current and native beta APKs, their actual package/version metadata and configured Sonos signer. Historical backlog and unavailable QA URLs remain. |
| 12 | Public web resources are preserved; redirects and authentication requirements are classified accurately. | Resolved by design. Every pro deployment captures 79–82/82 resources; every controller deployment captures its full 6 public resources and classifies the remaining 30–32 as `gated-or-redirected`, proven live against the deployment itself. See [public web capture](#public-web-capture-item-12). |
| 13 | Apple listing/history coverage is verified; IPA scope and authorization prerequisites are explicit. | Listing metadata and history verified. No IPA acquisition capability exists or is claimed; the documented scope and the exact prerequisites for adding it are stated in [Apple coverage and IPA scope](#apple-coverage-and-ipa-scope-item-13). |
| 14 | Unsupported firmware and missing-key cases are investigated; decoder/key improvements automatically trigger revisits. | CI classified 78 incomplete inventories: 28 recipient groups lack matching keys; available components report no decoder errors. Recovery can now use verified, previously decrypted rootfs when its receipt binds it to the exact original source and component. Continue recipient-specific recovery and live validation. |
| 15 | Ranges, pagination, parsers, traversal and budgets impose no unintended lifetime discovery ceilings. | All 19 scheduler calls persist their rotation lane, so no `--limit` can become a lifetime ceiling; the metadata model space widens (`2×bound+1`) instead of stopping at 255, proven to reach model 256 and 511 in tests. Two calls that shared the default lane were found and given explicit queue names. Enforced by `scripts/audit_discovery_ceilings.py`. See [discovery ceilings](#discovery-ceilings-item-15). |
| 16 | 429/backoff, expired URLs, host failures and mixed retry/fresh queues preserve progress without starvation. | Mutable-root and inventory ordering fixed. Firmware/app downloads and historical manifest replay retain server retry timing across restarts, including new URLs, independent archive hosts and separate publication throttles. Release rollover propagates HTTP throttles instead of treating them as isolated inventory outages. Continue remaining queue audits. |
| 17 | Existing publication references are reconciled; unrecorded assets and inconsistent metadata are investigated without deleting evidence. | Production audit reconciles 135 releases / 4,551 assets: 0 missing, 0 mismatched, 4,534 recorded. Unrecorded extras fell 316 → 17 by recovering 9 superseded web captures and 61 GPL receipts; the remaining 17 are proven duplicate publications (same SHA-256 under a correct `firmware-*` tag), so 0 are unexplained. See [archive reconciliation](#archive-reconciliation-item-17). |
| 18 | Interrupted publication, lost responses, conflicting names and changed content preserve successful work. | Document reconciliation tests cover fresh inventories, corruption, outages, registration after earlier failures and interrupted overflow uploads. Historical recovery finds accepted numbered-partition assets without replay or cached bytes; older GitHub assets lacking digest metadata are verified by body hash. Continue other worker acceptance checks. |
| 19 | Every target supports cold startup and migration, missing caches/tools, concurrent updates and worker restarts. | All 13 configured targets are exercised from an empty checkout, an established archive and a migrated one, including missing required vs optional capabilities, corrupt-metadata isolation and restart cadence. See [cold startup and migration](#cold-startup-and-migration-item-19). |
| 20 | Obsolete entrypoints, duplicated logic and stale docs are reconciled; target registration requires no payloads or hashes. | Providers and targets are in exact 1:1 correspondence across all 13 targets; 48 scripts are each wired or documented; no documentation names a missing path or retired workflow. Enforced by `scripts/audit_repository_consistency.py` in `verify.yml`. See [repository consistency](#repository-consistency-item-20). |
| 21 | After merge approval, the unified workflow runs successfully against the existing production archive. | Requires approved deployment after fixes and validation. |
| 22 | Requirements in the three reported issues and automated reports are checked against published evidence. | Checked against evidence and linked from PR #26 with a per-issue section. Issues #23, #24 and #25 remain open with no comments added and no closure. |

Live validation of mutable-root acquisition:
[run 44198556575](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44198556575).
Its ConnectX capture is 19,697,417 bytes with SHA-256
`5e4c5bf49d02a9c61fa93c010b5e6c44a0fc5f178e7ff2414f7d4d2711e1501a`,
published under `firmware-86.4-73207` in the isolated validation archive.
The acquisition regression checks that a mutable root is captured before the
historical backlog and that the historical candidate is processed next.

The [inventory drain](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44231069195)
completed five batches with zero worker errors and zero pending inventory jobs,
publishing 106 reports. This does not mean every encrypted source was decrypted:
individual reports retain their own completeness and missing-key evidence.
ConnectX's report is complete and has no analysis errors. Its
[published inventory](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/releases/download/firmware-86.4-73207/inventory-5e4c5bf49d02a9c61fa93c010b5e6c44a0fc5f178e7ff2414f7d4d2711e1501a-221bcc589fdfbdd513baa14b1ca0ec105c8c5b67310af7092d047588ad5054dc.json)
is 121,275 bytes; its SHA-256 is
`221bcc589fdfbdd513baa14b1ca0ec105c8c5b67310af7092d047588ad5054dc`.
The extraction bundle in the same release is 19,458,508 bytes with SHA-256
`9f2b98058a8c65877cc317a136d41c46024f7ac6582d03556b13a84bb8cbf089`.
It includes the inventoried `bin/anacapad` executable (13,651,008 bytes), whose
SHA-256 is `e4f2514c5cae8ba44fcb975c20f23c200c6a5d9e1ba88ba0ba732224a853d84f`.
Architecture comes from the executable's ELF header, rather than a fixed offset.
The independent [published-bundle audit](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44340635676)
downloaded the published inventory and bundle in CI, verified their receipt
hashes, and streamed every regular member without extracting or executing it.
All 190 regular files matched their inventoried sizes and SHA-256 hashes,
including Anacapa; there were no missing, duplicate or uninventoried files.
Only the resulting JSON evidence was downloaded for local review.

The [next firmware validation](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44230244613)
reached 120 preserved sources but exposed GitHub's 1,000-assets-per-release limit
while publishing discovery snapshots. The shared document publisher now searches
existing numbered partitions before uploading, reconciles lost responses, rolls
full partitions forward and records the actual tag. Inventory failures remain
reported while fresh documents can be preserved in a healthy partition.
The [live rollover validation](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44340640914)
published 203 documents into `manifest-snapshots-2026-11-18-part-2` with zero
preservation errors. All 203 catalog references match GitHub asset names, sizes,
digests and download URLs. The run reached 143 preserved sources and 33 manifest
URLs discovered through its own traversal. Its overall status remains partial:
455 source candidates await preservation and encrypted components still lack
some recipient keys. No external manifest collection supplied those URLs.

Historical manifest replay and metadata-profile exploration retain server
retry deadlines across workers. They defer newly queued requests to a throttled
host and resume after its deadline. Publication throttles have a separate scope;
one replay provider can continue while another is paused. HTTP 503 metadata
maintenance responses also pause the endpoint's untried profiles. Release-note
version hints use the same server-aware retry timing.

The [incomplete-inventory audit](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44346965134)
verified all 78 incomplete published reports against their receipts. Their
skipped sections identify 28 recipient groups with no matching model key;
available-component analysis reports no decoder errors. This cannot establish
what formats remain inside encrypted sections.

Key recovery previously selected only originally plaintext root filesystems.
It can now select a published decrypted rootfs when `data/raw` binds its
component hash, size, section, recipient and model package to the exact original
source SHA-256. Recovery still verifies downloaded bytes and accepts only the
requested public recipient fingerprint. Tests recover a new synthetic key from
a filesystem encrypted with an older key, and reject missing, stale or
conflicting extraction receipts. Failed-source queues and successful-source
reproduction remain shared with the existing recovery worker.

The [model-26 recovery validation](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44348573166)
restored the two tracked public recovery inputs omitted from the isolated
validation source. CI prepared seven recipient keys and completed raw extraction
and inventories for all four preserved model-26 sources. Keys stayed ephemeral
in CI; only publication/catalog evidence was retained. The published-input
regression now runs only in CI, while local recovery tests use generated keys.
The remaining unmatched recipient-group count is 27.

Installer handoff tests cover an empty checkout and publication from a native
manifest lead without a prior binary or hash. Channel traversal regressions
cover a missing Prod sibling that leads to available Dev history, and a 429
that retains the counterpart in the frontier without another host request.
The metadata-only [first-party traversal comparison](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44229422877)
starts from protocol roots; no external manifest collection supplies its URLs.
It completed with zero pending URLs: both traversals found 29 manifest URLs;
production found 41 installer URLs and included all 37 reference installer URLs.
This establishes method parity for the current first-party graph, not complete
historical coverage of campaigns that are no longer linked from those roots.
The [native mobile run](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44229422866)
published a newly discovered Sonos-hosted `85.00.12` beta APK (339,757,559 bytes).
GitHub's asset size and digest match its receipt. That run remained partial:
two advertised QA APK URLs returned 404 and the historical index timed out.
Hash-verified publication alone does not establish APK signature verification.

Inventory workers persist the order of waiting first analyses across catalog
insertions and decoder changes. Their default batch is 24, matching the source
extraction batch, with per-package resource bounds and resumable observations.

The metadata-only [Google Play diagnostic](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44207881506)
received HTTP 403 from Aurora's dispenser with both the SDK transport and
certificate-verified curl. No anonymous token was issued and no APK was fetched.
The adapter now distinguishes dispenser failures from Play API failures while
retaining throttle status and retry delays without recording response secrets.

## Public web capture (item 12)

Both Sonos web deployments are captured from public responses only; the
receipt records `Public deployment bytes only; no login session or cloud API
data.` for every deployment. Coverage differs by deployment because the two
sites differ in what they serve anonymously.

`pro.sonos.com` serves its full application anonymously. All seven recorded
deployments captured every resource they discovered — 82/82 for the two
earliest and 79/79 for the remaining five — with `gap_total` zero throughout.

`play.sonos.com` is a login-gated controller. Each of its four recorded
deployments captured exactly 6 resources: the login shell itself, its web app
manifest and four icons. The other 30–32 discovered static paths are recorded
with outcome `gated-or-redirected`, not as failures.

That classification is verifiable against the live site, not merely asserted.
The login shell a capture stores is byte-identical to what the gated static
paths return:

```
$ for u in https://play.sonos.com/ \
           https://play.sonos.com/_next/static/chunks/04wx8juc~3oqa.js \
           https://play.sonos.com/_next/static/chunks/0-cfl3z8kuhl_.js; do
    curl -sSL "$u" | shasum -a 256 | cut -c1-16
  done
c291adddb7f05779
c291adddb7f05779
c291adddb7f05779
```

All three are the same 17,125-byte HTML login page, and the receipt records
that same digest (`c291adddb7f05779…`) for its captured `https://play.sonos.com/`
entry with `content-type: text/html`. Requesting a chunk directly shows the
gate rather than a missing file:

```
$ curl -sS -D- -o /dev/null https://play.sonos.com/_next/static/chunks/0-cfl3z8kuhl_.js
HTTP/2 307
location: /en-us/static/chunks/0-cfl3z8kuhl_.js
```

The redirect target is gated as well, so the gate covers the locale-prefixed
static tree too rather than only the unprefixed path:

```
$ curl -sS -D- -o /dev/null https://play.sonos.com/en-us/static/chunks/0-cfl3z8kuhl_.js
HTTP/2 307
location: /api/auth/signin?callbackUrl=%2Fen-us%2Fstatic%2Fchunks%2F0-cfl3z8kuhl_.js
```

Following either chain lands on `/en-us/login?callbackUrl=…`. The gate is
server-side and applies to the whole static tree, so no anonymous request can
retrieve those bodies. This is why the controller's chunk bodies are absent
from the archive and why they must not be reported as a discovery defect: the
resources are discoverable and correctly enumerated, but not publicly readable.

Publicly readable controller assets are captured, not gated: `manifest.webmanifest`
(564 bytes), `favicon.ico` (7,452 bytes) and the three logo PNGs (7,548 / 738 /
16,728 bytes) all return HTTP 200 directly. The capture keeps them.

The distinction the item asks for is therefore recorded in the archive rather
than inferred: content that is publicly served is preserved, and content that
requires authentication is named as gated with the redirect chain that proves it.

## Apple coverage and IPA scope (item 13)

Listing and history preservation are verified. `data/apps/apple-app-store.json`
holds the current public iTunes lookup result for both controller families
(`1488977981` = Sonos S2, `293523031` = Sonos S1 Controller), and
`data/apps/apple-app-store-history.json` holds 8 dated snapshots from
2026-10-06 through 2026-11-18 recording every observed version transition:

```
2026-10-06  s2 89.01      s1 11.16.1
2026-10-07  s2 90.00      s1 11.16.1
2026-10-08  s2 90.00.01   s1 11.16.1
2026-10-15  s2 90.1       s1 11.16.1
2026-11-03  s2 91.0       s1 11.16.1
2026-11-09  s2 91.1       s1 11.16.1
2026-11-12  s2 91.1.1     s1 11.16.1
2026-11-18  s2 91.1.1     s1 11.16.2
```

Each snapshot records the version, release date, minimum OS version, reported
`fileSizeBytes` and release notes, compared field-by-field by
`scripts/check_new_apple_apps.py` so a change in any of them is detected.

**No IPA acquisition exists, and none is claimed.** The archive's Apple worker
is a metadata observer. The public lookup response itself contains no binary
location — a live check of the 44 fields returned per app shows only artwork,
screenshot, seller and `trackViewUrl` links:

```
$ curl -sS 'https://itunes.apple.com/lookup?id=1488977981%2C293523031&country=us'
resultCount 2
  1488977981 Sonos                91.1.1  com.sonos.SonosController2  216269824
  293523031  Sonos S1 Controller  11.16.1 com.sonos.SonosController   108355584
```

`fileSizeBytes` is a reported figure, not a download receipt, and nothing in the
response exposes an `.ipa`. Acquiring an IPA therefore requires capabilities the
archive deliberately does not have. The prerequisites, stated explicitly so the
scope cannot be mistaken for partial preservation:

1. **An authorized Apple ID with a prior purchase or entitlement** for each app,
   since App Store downloads are account-bound and the storefront will not serve
   a binary anonymously.
2. **Apple's authentication and licensing protocol**, including the
   device/anonymous-credential handshake and the `StoreServices`/fairPlay
   exchange that yields a signed download URL. This is not a public HTTP API and
   is not implementable from the archive's public-discovery model.
3. **A signing/decryption capability for the delivered payload.** App Store IPAs
   are fairPlay-encrypted; the encrypted binary is not a preserved executable
   without a device- or credential-bound decryption step.
4. **Account credential storage**, which the archive's own boundary rules exclude
   — payload acquisition and any private material stay in CI, and no login
   session or account data is recorded.

Because all four are absent, the honest scope is: **listing metadata and its
history are preserved; iOS binaries are not.** The runtime audit already states
this (`docs/archive-platform-runtime-audit.md`: "Archives listing metadata, not
IPA binaries. Do not represent this as iOS binary preservation."), and this
entry records the evidence and the prerequisites behind that statement.

## Archive reconciliation (item 17)

A live audit of the production archive against GitHub Release metadata reports
every published asset as accounted for:

```
$ PYTHONPATH=src .venv/bin/python scripts/audit_release_assets.py \
    --repo BookCatKid/sonos-firmware-archive \
    --output data/platform/audits/release-assets.json
releases=135 github=4551 recorded=4534 missing=0 mismatched=0 extras=17 duplicated=17 unexplained=0
```

Before reconciliation the same audit reported **316 unrecorded extras** against
the same 0 missing and 0 mismatched. Two independent causes were found, and both
were repaired by recovering evidence that already existed on the Release — no
historical evidence was deleted.

**Cause 1 — superseded web captures (276 assets).** A capture of an unchanged
deployment replaces the earlier capture in the receipt. The earlier capture's
assets stay on the Release, but nothing in the receipt named them. Nine such
captures were recovered by `scripts/reconcile_web_release_assets.py`, which reads
each published bundle's embedded `capture.json` and verifies the capture's
identity, app, release tag and every resource's size and SHA-256 against Release
metadata before trusting it. A capture whose bundle, tag or resource set
disagrees is refused, not guessed at. The recovered captures are recorded in
`data/apps/web-app-archive.json` under `superseded_captures`, so the audit can
account for their assets while the current deployment record stays authoritative.

**Cause 2 — lost GPL receipts (61 assets).** Interrupted CI jobs left published
GPL archives with no receipt. The existing
`scripts/reconcile_gpl_release_assets.py` recovered 17 assets for `gpl-14.12` and
44 for `gpl-14.18`; the latter had lost its entire receipt set. Recovery
reconstructs only receipts the Release can independently prove from size and
SHA-256, and never overwrites a conflicting recorded hash.

**Remaining 17 extras are duplicates, not gaps.** The audit now classifies each
unrecorded asset by matching its exact SHA-256 against every recorded asset. All
17 remaining extras have a byte-identical twin already published under a correct
`firmware-*` tag, in the seed-era `seed-2026-08-31` (16) and
`manifest-snapshots-2026-08-31` (1) releases. `duplicated_extra_total` is 17 and
`unexplained_extra_total` is 0, so no published asset lacks an explanation.

This classification is machine-visible in the persisted report rather than
prose: each entry in `duplicated_extras` carries a `recorded_as` list naming the
tag and asset that already holds those bytes. A future unrecorded asset with no
digest twin appears in `unexplained_extras` and is actionable.

## Cold startup and migration (item 19)

`tests/test_empty_platform.py::ColdStartTargetCoverageTests` iterates the real
`config/targets.json` rather than a sample, so a target added later is covered
automatically. Seven tests exercise all 13 targets:

1. **Empty checkout.** Every target plans from a checkout containing only
   `config/targets.json`, produces at least one step, and reports
   `executed: false`. Planning creates no `data/`, cache or metadata directory —
   the test asserts the checkout still holds exactly `['config']` afterwards.
2. **Established archive.** Every target validates its declared metadata paths,
   including nested directories, both before and after those paths are
   populated. Migration from an empty to a populated archive does not invalidate
   any target's metadata.
3. **Corrupt metadata isolation.** A malformed document belonging to one target
   raises for that target and leaves every other target validating cleanly, so
   one damaged file cannot mask or block the rest.
4. **Missing required capabilities.** Targets declaring `capabilities` report
   `status: blocked` with `executed: false` and the missing capability names in
   `pending`, without invoking the provider. `google-play` (`goopdl`) is the
   live case.
5. **Missing optional capabilities.** Targets whose tools are only optional still
   execute and report `ok` with empty `pending`. `mobile` and `android-history`
   (`java`, `apkeep`) are the live cases.
6. **Restart cadence.** A worker that has never run a target treats it as due;
   after a completed run the recorded `next_due` suppresses an immediate rerun.
   Manual targets are never due. This is what stops a restarted worker from
   re-acquiring everything it just published.
7. **Adding a target.** A target appended to `config/targets.json` with only a
   name, provider, cadence and metadata path plans successfully with no payload,
   hash inventory or prior archive present.

Two of these were confirmed to fail against deliberately broken code: making
`missing_capabilities` ignore required capabilities fails test 4 for
`google-play`, and making `is_due` ignore the manual flag fails test 6 for
`gpl-indexes`.

Concurrent metadata updates and interrupted-worker recovery are covered
separately in `tests/test_platform_runner.py`:
`test_metadata_commit_preserves_unrelated_local_work_and_retains_patch` refuses
pre-staged human work, keeps the user's edit intact and still writes a
recoverable checkpoint patch;
`test_deleted_tracked_metadata_is_published` proves a removal is committed; and
`test_captured_manifest_can_resume_after_worker_directory_changes` resumes a
capture whose previous worker directory no longer exists.

## GPL index and source coverage (item 6)

The direct-Sonos GPL catalogue holds 39 verified indexes covering versions 6.4
through 18.6. Their advertised links and acquisition receipts reconcile
completely:

```
$ PYTHONPATH=src .venv/bin/python scripts/audit_gpl_link_coverage.py
versions=39 links=1409 unattempted=1 (published=1408, unattempted=1)
  unattempted 14.12/libusb-1.0.23.txz (sonos)
```

The 1,409 links comprise 1,112 Sonos-hosted sources and 297 upstream sources
(glibc, uClibc and similar at their own project hosts). All 297 upstream links
are published, so "follow their source links" is satisfied rather than merely
enumerated.

**The reported `+`/URL-encoding failure is real and now proven live.** A GPL
filename may contain literal `+` (for example
`mmc-utils-0.1+git-r0-patched.tar.xz`). Sonos's CDN treats an unencoded `+` in
the path as an access denial, not as a literal character:

```
$ curl -o /dev/null -w '%{http_code} %{size_download}\n' \
    'https://www.sonos.com/documents/gpl/18.6/mmc-utils-0.1+git-r0-patched.tar.xz'
403 111

$ curl -o /dev/null -w '%{http_code} %{size_download}\n' \
    'https://www.sonos.com/documents/gpl/18.6/mmc-utils-0.1%2Bgit-r0-patched.tar.xz'
200 56748
```

The 403 body is the CDN's `AccessDenied` XML, which is why a naive fetcher
reports "not found" for a file that plainly exists. `source_url()` normalizes
the path segment with `quote(filename, safe="-._~")` so every `+` becomes `%2B`;
`tests/test_gpl_archive.py::test_plus_encoding_is_not_query_form_encoding`
pins that, and
`test_every_retained_direct_index_has_exact_integrity_and_links` requires all
five `+`-bearing 18.6 links to carry `%2B` in their `fetch_url`. Those five are
published with real byte counts (9,100,376 / 159,516 / 56,748 / 666,308 /
125,612), and the 56,748 matches the live `%2B` response above.

**Compressed contents are validated, not assumed.** `verify_artifact()` checks
the tar member list *and* drains the entire compressor stream, because tar
iteration stops at its end-of-archive marker before the XZ/gzip/bzip2 CRC and
footer — a truncated archive can otherwise pass. It rejects empty member sets,
absolute or `..` member paths, unknown compression and invalid archives.

That validation found the one unaccounted link. `14.12/libusb-1.0.23.txz` is
advertised by a verified index and served with HTTP 200, but the body is a
well-formed 108-byte XZ stream containing an empty tar:

```
$ curl -o libusb.txz https://www.sonos.com/documents/gpl/14.12/libusb-1.0.23.txz
$ ls -l libusb.txz
108 bytes      # sha256 5ca1b6c461fc194055d52b181f57c63dc1d34c19d041f6395e6f6abc039692bb
$ tar tvJf libusb.txz
                # no members
$ PYTHONPATH=src .venv/bin/python -c "verify_artifact(...)"
(False, 'empty-tar', 0)
```

Sonos published an empty archive for that component. Rejecting it is correct;
the defect was that the rejection was invisible. Neither existing audit could
see it: the publication audit only compares GitHub assets, which this never
became, and the compressed-stream audit only revalidates *published* receipts.
`scripts/audit_gpl_link_coverage.py` closes that hole by requiring every
advertised link to end in exactly one accounted state — `published`, or
`recorded:<terminal-state>` — and failing the job on `unattempted`. It is wired
into the `gpl-audit` target ahead of the stream audit, so the next acquisition
pass that records this link turns the job green and the archive gains a durable
explanation instead of a silent retry loop.

`tests/test_gpl_link_coverage.py` covers the classifier (13 tests, including the
real committed catalogue) and the validator's empty, truncated, escaping-member
and complete cases. Two of them were confirmed to fail against deliberately
broken code.

## ConnectX historical discovery (item 3)

ConnectX (the Anacapa application, package model 17) is the archive's clearest
reused-URL case: `.../Prod/JFFS_Static_Link/connectx.upd` is mutable, so its
bytes change as Sonos rolls forward while the URL stays the same. Discovery uses
two Sonos protocol routes, never an imported download list:

1. **Manifests.** Each firmware manifest advertises its own model-17 component at
   `<version>-1-17.upd` in that version's directory. Following manifests is what
   attributes a ConnectX capture to a specific release.
2. **The protocol root.** The mutable `connectx.upd` is declared as an
   `artifact_root` in `config/targets.json` with a refresh cadence, so each run
   re-probes it and captures a new distinct content when it changes.

The coverage audit reports the current live state:

```
$ PYTHONPATH=src .venv/bin/python scripts/audit_connectx_coverage.py
advertised_versions=38 components=38 preserved=31 distinct_contents=31 manifest_attributed=31 unavailable=7 errors=0
historical_index snapshots=55 distinct_contents=55 index_only=25 catalog_only=8
```

Three facts follow from that output:

* **31 preserved contents are 31 distinct contents.** No content is republished
  under many version labels, so the count reflects real historical coverage
  rather than relabelled duplicates.
* **All 31 are manifest-attributed** (`manifest_attributed=31`), meaning each was
  reached by walking a Sonos manifest rather than by importing a URL list.
* **Every preserved record carries a receipt** — `errors=0` means none is
  missing `sha256`, `bytes`, `release_tag` or `release_asset`, and every tag is a
  `firmware-*` tag.

The 7 unaccounted-as-preserved versions are named individually by the audit. All
seven are withdrawn at the source, verified live:

```
96.1-79270-v18.7-4b07MiHfnw-RC-2/96.1-79270-1-17.upd  -> 404 (1264 bytes)
97.1-80312-v18.8-jZ0oXJNpeN-RC-6/97.1-80312-1-17.upd  -> 404 (1264 bytes)
90.0-67171-v17.2-ZethjbGivZ-GA-1/90.0-67171-1-17.upd  -> 404 (1264 bytes)
update.sonos.com/firmware/Gold/Kanye-v2.8/10.20-75300-1-17.upd -> 404 (1264 bytes)
```

The independent web-archive routes were checked and do not have them either.
Wayback's CDX index returns an empty result for the versioned 404 paths *and*
for the mutable URL itself:

```
$ curl 'https://web.archive.org/cdx/search/cdx?url=update-firmware.sonos.com/firmware/Prod/96.1-79270-v18.7-4b07MiHfnw-RC-2/96.1-79270-1-17.upd&output=json&fl=timestamp,statuscode,digest,length'
[]

$ curl 'https://web.archive.org/cdx/search/cdx?url=update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd&output=json&fl=timestamp,statuscode,digest,length'
[]
```

Common Crawl likewise has no captures under that path:

```
$ curl 'https://index.commoncrawl.org/CC-MAIN-2025-38-index?url=update-firmware.sonos.com/firmware/Prod/*&output=json&limit=5'
{"message": "No Captures found for: update-firmware.sonos.com/firmware/Prod/"}
```

So these 7 cannot be recovered by a historical-index route today; they are
recorded as `missing-cdn` with their source URL rather than being counted as
covered. That is the honest state of live coverage, and the audit re-reports it
each firmware run.

**Distinct contents behind a reused URL is proven, not asserted.**
`tests/test_firmware_archive.py::test_static_endpoint_versions_come_from_bytes_and_request_revision_remains_unpinned`
serves two different payloads from the same mutable URL and asserts that the
catalog ends up with two distinct records (`86.4-73207` and `87.1-74000`), two
distinct identities, and no `firmware-unknown` tag. The version comes from the
bytes, not from the request, so a change at the URL creates new history instead
of overwriting it.
`test_size_only_cache_does_not_hide_changed_bytes_at_a_url` proves a
same-size content change is still re-downloaded and distinguished by SHA-256.

`tests/test_connectx_coverage.py` adds 13 tests for the audit, including the
committed catalogue, duplicate-content detection, receipt completeness and the
withdrawn-component listing. One was confirmed to fail against deliberately
broken code.

## Repository consistency (item 20)

Consistency is now checked rather than asserted:

```
$ PYTHONPATH=src .venv/bin/python scripts/audit_repository_consistency.py
targets=13 scripts=48 orphan_scripts=0 doc_path_problems=0 retired_workflows=0 unreachable_targets=0
```

Five invariants, all currently satisfied:

1. **Providers and targets correspond exactly.** The 13 providers in `BUILTINS`
   are the 13 providers named by `config/targets.json`. There is no adapter
   without a target and no target without an adapter, so a rename cannot leave a
   dead reference.
2. **Every target is reachable.** Each of the 13 plans from a checkout with no
   archive state and produces at least one step. This is the same guarantee item
   19 exercises in depth.
3. **No orphan scripts.** All 48 scripts under `scripts/` are wired into a
   provider, a workflow or another module, or documented as a manual tool. The
   one genuine orphan found, `import_manifest.py`, is now documented in
   [maintenance tools](maintenance-tools.md) along with the other manual
   recovery commands, so an operator can find it and a future orphan is reported
   instead of forgotten.
4. **No documentation points at a missing path.** Every backticked repo path in
   `docs/` and `README.md` resolves. Links pinned to a commit hash are exempt,
   because they describe history rather than today's tree.
5. **No documentation advertises a retired workflow.** Only `archive.yml` and
   `verify.yml` remain. `docs/archive-platform-runtime-audit.md` describes the
   pre-unification ten-workflow layout and pins every such link to commit
   `d34f6c8`, so it reads as a historical record rather than a stale instruction.
   `docs/maintenance-tools.md` states the current layout explicitly.

The audit runs in `verify.yml` after the test suite, so a pull request that
orphans a script, drops a provider or breaks a documentation link fails CI.

Two findings were investigated and deliberately **not** deleted:

- `scripts/upload_firmware_release.py` was flagged in the runtime audit as a
  weaker duplicate publication path. It is now a thin wrapper over
  `firmware_archive.upload_receipt`, which verifies size and SHA-256 through the
  shared `verified_asset`/`publish_verified` publisher, so it performs the same
  reconciliation as the workers. It is kept as a documented manual tool rather
  than removed.
- The historical importers and key-recovery scripts are manual tools, not
  runtime discovery inputs. `data/gpl/seed-versions.json` is retained as
  evidence and is not read by any runtime path (verified by grep across `src/`,
  `scripts/` and workflows).

`tests/test_repository_consistency.py` adds 14 tests: the live repository plus
fixtures for orphan scripts, documented scripts, broken documentation paths,
pinned historical links, retired workflow references, unknown providers and the
CLI exit code.

## Discovery ceilings (item 15)

Two properties decide whether a per-run budget can become a permanent coverage
ceiling. Both are now checked mechanically:

```
$ PYTHONPATH=src .venv/bin/python scripts/audit_discovery_ceilings.py
scheduler_calls=19 unrotated=0 unnamed_queue=0 model_expands=True budgets=26
```

**1. Every scheduler call persists its rotation.** `retry.select` reserves part
of a batch for retries and, when only one slot fits, alternates retry and fresh
lanes across runs using `state['retry_rotation'][queue]`. A call that omits
`state=` cannot rotate, so a small `--limit` would permanently hide everything
after the first batch. All 19 scheduler calls in `src/` and `scripts/` pass it.

A call that omits `queue=` is subtler: it shares the `default` rotation lane
with any other caller on the same document, so two queues can starve each
other. The audit found two such calls, both now fixed:

- `src/sonos_firmware/metadata_sweep.py` now uses
  `queue='legacy-ups-profiles'`.
- `scripts/archive_gpl_direct.py` now uses `queue='gpl-index-versions'`.

Reverting those two lines makes the audit fail with exactly those two entries,
so the check is not vacuous.

**2. Bounded spaces widen instead of stopping.** The metadata model space starts
at `model_max` (default 255) and, when a namespace exhausts its range, raises the
bound to `2 × bound + 1`, resets the cursor and starts another pass. 255 is
therefore a starting batch size, not a lifetime limit. This is proven, not
asserted:

- `test_model_discovery_crosses_the_default_255_boundary_without_configuration_changes`
  finds model 256 with no configuration change and asserts the namespace bound
  becomes 511 and the policy is `expanding-passes`.
- `test_raising_initial_model_range_above_4095_preserves_the_cursor_and_request_budget`
  shows raising the bound preserves the cursor and the request budget instead of
  restarting from zero.

**3. Every budget is configurable.** 26 per-run budgets are exposed through
target options — `limit`, `seconds`, `history_limit`, `extraction_limit`,
`inventory_limit`, `binary_limit`, `discovery_limit`, `model_max` and others — so
an operator can raise a batch without a code change. `config/targets.json` can
also override any of them per target.

Publication capacity is likewise unbounded: a full 1,000-asset Release rolls
into a numbered partition with the actual tag recorded, so asset count is not a
ceiling either.

`tests/test_discovery_ceilings.py` adds 10 tests: the live repository plus
fixtures for an unrotated call, a shared default lane, a fully persisted call,
the stdlib `selector.select` false positive, a model space that stops at its
bound, and the CLI exit code.
