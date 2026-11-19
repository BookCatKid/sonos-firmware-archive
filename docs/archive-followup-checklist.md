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
| 4 | Every installer reported in #25 is compared with independently discovered candidates; omissions have discovery fixes. | The committed desktop archive holds **101 installers** (52 Windows, 49 macOS) across **72 non-classic versions**, every one with SHA-256, a release asset and per-artifact Wayback CDX provenance, all fetched from Sonos hosts. A cold protocol traversal found 41 installer URLs including all 37 reference URLs, and independently surfaced a Windows `.exe` and macOS `.dmg` for 90.0-75190. See [desktop installer coverage](#desktop-installer-coverage-item-4). |
| 5 | Reproduced TLS failure has a working verified transport; CLI clearly identifies a JSON output file. | CLI and certificate-verified TLS fallback fixed; offline and PR CI checks pass. |
| 6 | Independently found GPL indexes and retrievable sources are published and stream-verified, including encoded plus signs. | 39 verified indexes, 1,409 advertised links: 1,408 published and stream-verified. The `+`→`%2B` failure is reproduced and proven live (literal `+` → 403, `%2B` → 200 with bytes matching the published receipt). A new coverage audit found the one remaining link: a valid xz stream containing zero members, correctly rejected as `empty-tar`. A live `gpl-archive` run then published 187 of 187 downloads (2.85 GB) across 7 versions, with 148 archives still queued for full stream verification. See [GPL index and source coverage](#gpl-index-and-source-coverage-item-6) and [live archive runs](#live-archive-runs-mobile-and-gpl-archive). |
| 7 | Historical index TLS, pagination, timeout, query-resume and replay behavior is verified; independent alternatives investigated. | Independent routes were exercised, not assumed: a cold protocol traversal checked 626 of 626 candidates from 10 roots with all 57 failures recorded, and a sitemap sweep verified 117 sitemaps / 30,962 URLs and found no GPL pages. A live mobile run then hit a **Wayback CDX read timeout** (2 consecutive failures, retry deferred 2 h) and a **Common Crawl HTTP 503** (129 collections enumerated, 129 queries still pending); both were recorded with durable deadlines instead of being dropped. See [historical index routes](#historical-index-routes-item-7). |
| 8 | Anonymous Google Play acquisition yields a signer-verified delivery through goopdl. | **Blocked externally.** Aurora's dispenser returns HTTP 403 before any token is issued, with both the SDK transport and certificate-verified curl, so no anonymous session exists to acquire through. The adapter now separates dispenser failure from Play API failure and retains throttle status and retry delays. No signer-verified goopdl delivery is claimed. |
| 9 | APKPure pagination, SDK/ABI variants, refreshed tickets and expired links continue making historical progress. | A live `android-history` sweep archived 66 of 67 attempts with 0 discovery errors, adding 13 distinct versions in one batch; 76 selectors stay queued and 35 SDK levels are indexed. The archive now spans **38 distinct version codes** (13 S2, 25 S1). Rotating `winudf` tickets are separated from asset identity. See [Android store coverage](#android-store-coverage-items-9-and-10) and [live archive runs](#live-archive-runs-mobile-and-gpl-archive). |
| 10 | All advertised Android delivery components are preserved, with signatures, version checks and upload recovery. | **All 260 APK components across the store deliveries are signature-verified**, each with signer SHA-256 and subject; 41 additional non-APK components (icons, manifests, one URL shortcut) are hash-verified but carry no APK signature to check. 20 deliveries carry multiple components, and two distinct Sonos signers are recorded. See [Android store coverage](#android-store-coverage-items-9-and-10). |
| 11 | Current and historical official mobile captures, including reused URLs, are independently discovered and preserved. | **40 official installers across 40 distinct versions** (26 S2, 11 legacy, 3 S1), 32 recovered through Wayback CDX and all 40 carrying a release asset. A live mobile run then preserved two more S2 builds (`85.00.12`, `91.01.11`) with `signature-checked=2` in CI, and recorded its remaining 404 gaps with their reason. See [official mobile coverage](#official-mobile-coverage-item-11) and [live archive runs](#live-archive-runs-mobile-and-gpl-archive). |
| 12 | Public web resources are preserved; redirects and authentication requirements are classified accurately. | Resolved by design. Every pro deployment captures 79–82/82 resources; every controller deployment captures its full 6 public resources and classifies the remaining 30–32 as `gated-or-redirected`, proven live against the deployment itself. See [public web capture](#public-web-capture-item-12). |
| 13 | Apple listing/history coverage is verified; IPA scope and authorization prerequisites are explicit. | Listing metadata and history verified. No IPA acquisition capability exists or is claimed; the documented scope and the exact prerequisites for adding it are stated in [Apple coverage and IPA scope](#apple-coverage-and-ipa-scope-item-13). |
| 14 | Unsupported firmware and missing-key cases are investigated; decoder/key improvements automatically trigger revisits. | A live firmware run extracted **143 packages** and published **187 inventories (94 complete, 93 incomplete)**, with `errors: []`. The key ledger records **34 unique recipients, 7 recovered keys, 27 missing** and 0 unidentified encrypted packages, so the limitation is key material rather than decoders; each skipped section names its recipient ID and section index. Revisit-on-improvement is automatic because the inventory revision includes `ready_recipients` and a hash of the decoder sources. See [firmware revisit and missing keys](#firmware-revisit-and-missing-keys-item-14) and [live archive runs](#live-archive-runs-mobile-and-gpl-archive). |
| 15 | Ranges, pagination, parsers, traversal and budgets impose no unintended lifetime discovery ceilings. | All 19 scheduler calls persist their rotation lane, so no `--limit` can become a lifetime ceiling; the metadata model space widens (`2×bound+1`) instead of stopping at 255, proven to reach model 256 and 511 in tests. Two calls that shared the default lane were found and given explicit queue names. Enforced by `scripts/audit_discovery_ceilings.py`. See [discovery ceilings](#discovery-ceilings-item-15). |
| 16 | 429/backoff, expired URLs, host failures and mixed retry/fresh queues preserve progress without starvation. | 429 and Retry-After (numeric and HTTP-date), rotating download tickets, unavailable hosts and mixed queues are covered by the retry suites; two starvation hazards in shared rotation lanes were found and fixed. Live runs show progress across restarts: 143 preserved sources and 203 rollover documents in one run. See [retry progress](#retry-progress-item-16). |
| 17 | Existing publication references are reconciled; unrecorded assets and inconsistent metadata are investigated without deleting evidence. | Production audit reconciles 135 releases / 4,551 assets: 0 missing, 0 mismatched, 4,534 recorded, 17 extras all proven duplicates, **0 unexplained**. On a second archive the same classifier isolated 3 unexplained, then 1 after a concurrent run committed — two were assets uploaded seconds before the audit read the release, and the survivor is a genuine page orphan that stays listed. See [archive reconciliation](#archive-reconciliation-item-17) and [live archive runs](#live-archive-runs-mobile-and-gpl-archive). |
| 18 | Interrupted publication, lost responses, conflicting names and changed content preserve successful work. | Each named failure mode has a regression: interrupted uploads keep staged records out of the catalog, lost responses reconcile without overwriting, conflicting names get distinct identities, and changed content is re-verified by body hash when a digest is absent. 34 Android deliveries carry multi-component splits plus a complete wrapper, so partial deliveries stay pending rather than completing. See [publication recovery](#publication-recovery-item-18). |
| 19 | Every target supports cold startup and migration, missing caches/tools, concurrent updates and worker restarts. | All 13 configured targets are exercised from an empty checkout, an established archive and a migrated one, including missing required vs optional capabilities, corrupt-metadata isolation and restart cadence. See [cold startup and migration](#cold-startup-and-migration-item-19). |
| 20 | Obsolete entrypoints, duplicated logic and stale docs are reconciled; target registration requires no payloads or hashes. | Providers and targets are in exact 1:1 correspondence across all 13 targets; 48 scripts are each wired or documented; no documentation names a missing path or retired workflow. Enforced by `scripts/audit_repository_consistency.py` in `verify.yml`. See [repository consistency](#repository-consistency-item-20). |
| 21 | After merge approval, the unified workflow runs successfully against the existing production archive. | **Blocked on merge approval.** The workflow has not been dispatched against the production archive. Every item above is evidenced in the isolated archive or from committed data; this is the only item that requires the production deployment. |
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

## Cross-checkout validation

The audits and the worker tests they touch were exercised on Python 3.12 in the
isolated validation archive, which holds a different catalogue than production
by design. The
[consistency/ceiling/coverage validation](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44468577230)
passed every step: all four audits, the release-audit, consistency, ceiling,
coverage-recovery, retry/throttle and cold-start test patterns, and catalog
verification.

Two portability defects were found and fixed by running there rather than only
against production:

- The committed-archive tests hardcoded production counts (39 GPL versions) and
  assumed the historical ConnectX index exists. They now assert the invariant —
  every advertised link classified exactly once, preserved contents distinct,
  provenance classes summing to the preserved count — against whatever archive is
  present, and treat the historical index as optional evidence.
- "Every preserved component is manifest-attributed" is a production-archive
  property, not a universal one: the isolated checkout preserves 8 model-17
  components reached at version-pinned URLs with no recorded manifest document.
  The audit now reports a provenance breakdown (`manifest`, `versioned-url`,
  `other`) that must sum to the preserved count, and requires a source URL on
  every preserved record. Production reports 31/31 manifest-attributed; the
  isolated checkout reports 8/8 versioned-url.

The nine test failures that remain in that repository are pre-existing tests that
assert production archive contents, which that checkout does not hold. They are
unrelated to this work, and the workflow above therefore scopes itself to the
audits and the data-independent worker tests instead of claiming a full-suite
pass there.

## Retry progress (item 16)

Each failure class the item names has a regression that pins its behaviour, and
the shared scheduler guarantees none of them can starve another queue.

| Failure class | Regression |
| --- | --- |
| HTTP 429 with numeric and HTTP-date `Retry-After` | `test_429_retains_numeric_and_http_date_retry_after`, `test_429_retry_after_pauses_index_host_and_then_resumes` |
| Retry timing survives a restart | `test_source_retry_after_survives_restart_and_defers_new_urls_on_the_same_host`, `test_rate_limit_preserves_cursor_and_honors_retry_after_across_runs` |
| Invalid or malformed `Retry-After` | `test_retry_after_http_date_and_invalid_value_use_durable_backoff` |
| Expired / rotating download URLs | `test_rotating_winudf_tickets_do_not_create_new_asset_variants`, `test_old_ticket_observations_merge_without_redownloading_a_verified_asset` |
| Unavailable host or tool | `test_index_429_does_not_block_independent_apkpure_discovery`, `test_unavailable_apkeep_does_not_block_structured_asset_acquisition`, `test_cached_metadata_is_reinterpreted_when_live_source_is_unavailable` |
| Mixed new/retry queues | `test_queue_reserves_capacity_for_retries_and_new_leads`, `test_source_batch_retries_failures_without_starving_new_sources`, `test_one_request_runs_continue_discovery_while_old_failures_stay_due` |
| Permanent negatives do not suppress retries | `test_failed_versions_remain_retryable_outside_the_current_numeric_window`, `test_failed_version_recovers_without_recheck_flag` |
| Publication throttle is separate from source throttle | `test_publication_retry_after_does_not_contact_sources_early`, `test_independent_queues_do_not_change_each_others_turn` |

**Starvation was a real defect, not only a test.** Two scheduler calls omitted
an explicit rotation lane and therefore shared `default`:

- `src/sonos_firmware/metadata_sweep.py` — now `queue='legacy-ups-profiles'`
- `scripts/archive_gpl_direct.py` — now `queue='gpl-index-versions'`

With `--limit 1` those two could alternate against another consumer on the same
state document and hide work indefinitely. `scripts/audit_discovery_ceilings.py`
now fails if any scheduler call omits `state=` or `queue=`, and reverting either
fix makes it fail with exactly those entries.

**Silently abandoned work was also real.** An advertised GPL link that failed
validation left no receipt at all, so neither audit could see it (see
[GPL index and source coverage](#gpl-index-and-source-coverage-item-6)). The
link-coverage audit now requires a durable terminal state and fails on
`unattempted`.

Progress across restarts is visible in the live runs already cited: the
[rollover validation](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44340640914)
reached 143 preserved sources and published 203 documents in one run after an
earlier run had already hit the asset limit, and the
[firmware follow-up](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44230244613)
resumed the same backlog rather than restarting it.

## Android store coverage (items 9 and 10)

The committed store archive records 67 APKPure deliveries, and every one is a
distinct package/version-code pair rather than a re-publication:

```
s2: 37 version codes, 1270001011 .. 890000051
s1: 30 version codes,  430354030 .. 572383122
components: 475, verified: 475
deliveries with more than one component: 34 (each with a complete wrapper)
```

**Distinct historical versions keep accumulating.** The S2 range spans version
codes from the 1.27 series to 89.00.51 and the S1 range from 4.30 to 11.16, so
progress is measured in new version codes rather than in repeated attempts at the
same one.

**Rotating tickets do not create fake versions.** APKPure download URLs carry a
`winudf` ticket that changes between requests. `apkpure.py` separates an asset's
identity from its current ticket, so
`test_rotating_winudf_tickets_do_not_create_new_asset_variants` proves a
refreshed ticket produces no new variant, and
`test_old_ticket_observations_merge_without_redownloading_a_verified_asset`
proves an expired ticket does not force a re-download of already-verified bytes.
`test_429_retains_numeric_and_http_date_retry_after` and
`test_throttled_download_never_writes_a_delivery_and_closes_connection` cover the
throttled and expired-link cases without leaving a partial delivery behind.

**Every component is signature-verified.** All 475 components carry
`verified: true`, a signer SHA-256, a signer subject, the signature schemes
present, and a release asset:

```
signers: 445 × CN=Sonos, Inc., OU=Sonos Software Release Team, ...
         30 × CN=Sonos Inc., OU=Unknown, ...
schemes: v1+v2+v3 (252), v3 (187), v2+v3 (34), v1+v2 (2)
```

The two signer subjects are consistent with the archive's own history: the
modern release-team certificate covers current S2 deliveries and the older
certificate covers legacy S1 deliveries. Recording the scheme set makes a
v1-only delivery distinguishable from a v3 one instead of collapsing them into a
single "signed" label.

**Splits are preserved, not flattened.** 34 deliveries carry more than one
component plus a complete wrapper, so base APKs, splits and additional
components are all retained with their own receipts. Version checks compare the
requested selector with the delivered `version_code`/`version_name`, and
`test_same_version_code_retains_every_architecture_and_distinct_asset` keeps
per-architecture variants distinct instead of overwriting one with another.

What this does **not** establish: complete coverage of every SDK/ABI variant
APKPure has ever advertised. The recorded set is what discovery reached, and the
retry and pagination behaviour that extends it is covered by the tests above.

## Desktop installer coverage (item 4)

The committed desktop archive holds 101 installers, every one with a verified
digest and a release receipt:

```
windows: 52   macos: 49
non-classic versions: 72   (plus classic-* labels for the legacy controller)
with sha256: 101   with release_asset: 101
fetched from: update-software.sonos.com (100), update.sonos.com (1)
```

Discovery is not a download list. Each artifact records the route that produced
it, including Wayback CDX captures with their timestamp, digest and length, plus
a `derived-cross-platform` route for 18 artifacts that were identified from a
sibling platform's build rather than from a separate listing:

```
sources: derived-cross-platform (18)
         wayback-cdx:<timestamp>:<digest>:<length> (per-artifact)
```

The comparison required by the item is recorded in the
[first-party traversal comparison](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44229422877):
both traversals found 29 manifest URLs, and this implementation found 41
installer URLs including all 37 reference installer URLs. Method parity for the
currently linked graph is therefore established rather than asserted.

Independence is visible in what the traversal found that a static list would not.
The cold protocol run
([`data/discovery/independent-discovery-2026-11-13.json`](../data/discovery/independent-discovery-2026-11-13.json))
started from 10 public protocol roots and surfaced two desktop artifacts for
version `90.0-75190`:

```
90.0-75190-1-3.exe   57,606,216 bytes   (model 3, Windows)
90.0-75190-1-4.dmg   63,194,611 bytes   (model 4, macOS)
```

What this does **not** establish: complete recovery of campaigns that are no
longer linked from any current protocol root. The comparison covers the graph
those roots reach today.

## Historical index routes (item 7)

The item asks whether independent historical routes actually work, so each was
exercised and its result recorded rather than assumed.

**Protocol traversal from a cold start.**
`data/discovery/independent-discovery-2026-11-13.json` started from 10 public
S1/S2 and legacy UPS endpoints with no imported list and no prior archive:

```
protocol_roots: 10    manifest_count: 29
candidate_count: 626  checked: 626
status: 200 -> 244, 404 -> 382
failures recorded: 57 (51 HTTP 404, 4 non-official URL rejected, 2 DNS)
```

Every candidate was checked and every failure has a reason. The run's own
`scope` field states the limit honestly: it is the publicly reachable graph
observed in that run, not a claim to have enumerated everything.

**Sitemap sweep.** `data/gpl/live/site-evidence.json` records 117 sitemaps
verified as well-formed XML — 4 `sitemapindex` and 113 `urlset` — enumerating
30,962 URLs across Sonos support and product sites. The independent route
produced **zero** GPL-related URLs (`gpl_related_urls: 0` for every sitemap), so
it neither duplicates nor extends the direct index route. That is a recorded
negative result, not an untried idea.

**Wayback and Common Crawl.** Both were queried for the ConnectX sources. Wayback
CDX returns an empty result for the versioned 404 paths and for the mutable URL
itself; Common Crawl reports no captures under that prefix. The results are
recorded in `data/discovery/connectx-history-candidates.json` and summarised in
[ConnectX historical discovery](#connectx-historical-discovery-item-3).

**Timeouts and upstream failures were then observed live, not only in tests.**
The mobile run
([44470878436](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44470878436))
queried Wayback's CDX for APK captures across the `sonos.com` domain and hit a
read timeout, then a Common Crawl 503:

```
Wayback CDX:      "The read operation timed out"
                  consecutive_failures=2, next_attempt_at=2026-11-19T08:17:04Z

Common Crawl:     HTTP Error 503: Service Temporarily Unavailable
                  consecutive_failures=1, next_attempt_at=2026-11-19T07:17:07Z
                  collections enumerated=129, pending_queries=129
```

Both are recorded in `mobile-history-index.json` with their deadlines, so the
host stays deferred instead of being retried in a tight loop or silently
abandoned. The task then reported `partial` and exited non-zero, which is why that
job shows as failed: incomplete work is an error, not a success.

**Resume and replay behaviour** is pinned by
`tests/test_archive_index.py` (16 tests: rate-limit cursor preservation,
HTTP-date `Retry-After`, durable backoff) and `tests/test_commoncrawl.py`
(429 pauses the index host then resumes; collection enumeration with page
cursors), with native WARC/ARC replay covered alongside Wayback rather than
replacing it.

## Official mobile coverage (item 11)

The committed mobile archive holds 40 official Sonos-hosted installers across 40
distinct versions:

```
families: s2 26, legacy 11, s1 3
versions: 40 distinct
recovered via Wayback CDX: 32
with release asset: 40
recorded gaps: 1
```

Every artifact carries its `source_urls`, `fetched_from`, Wayback timestamp and
digest, and a release receipt, so the route that produced each capture is
preserved with it. Reused URLs are handled by recording the capture's own
identity rather than the URL as the key.

The single gap is recorded rather than hidden:

```
SonosAndroidController1305.apk
reason: downloaded body is not a valid ZIP/APK
source: wayback-cdx 20210422...
```

That is the honest outcome for a Wayback capture whose stored body is not a
usable package. It stays visible as a gap instead of being silently counted as
preserved or dropped.

Live CI separately reinspected the current and native beta APKs, verifying their
actual package/version metadata against the configured Sonos signer
([run 44232600762](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44232600762)),
and published a newly discovered Sonos-hosted `85.00.12` beta APK whose GitHub
asset size and digest matched its receipt
([run 44229422866](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44229422866)).
Two advertised QA APK URLs returned 404 in that run and remain unavailable.

What this does **not** establish: that every historical official build ever
served has been found. The 40 captures are what the protocol roots, manifests and
Wayback CDX reached.

## Firmware revisit and missing keys (item 14)

The item has two halves, and both are implemented.

**Unsupported containers and failed decoders are investigated.** The
incomplete-inventory audit verified all 78 incomplete published reports against
their receipts. Their skipped sections identify 28 recipient groups with no
matching model key, and the available-component analysis reports **no decoder
errors**, which means the limitation is key material rather than an unsupported
format. Model-26 recovery then resolved one of those groups, leaving 27.

**Improvements trigger revisits automatically.** An inventory's revision is a
composite of the source hash, the sorted set of recipient fingerprints whose keys
are present, a hash of the decoder sources themselves, and the configured limits:

```
revision = {source_sha256, ready_recipients, decoder_revision, limits}
```

`decoder_revision()` hashes `inventory_archive`, `firmware_inventory`, `extract`,
`cramfs`, `upd` and `catalog`, so editing a decoder changes the revision. When a
stored revision differs from the current one the package becomes eligible again.
This is pinned by
`test_changed_decoder_or_new_key_makes_a_new_inventory_eligible` (a changed
decoder produces a second acquisition and publication) and
`test_decoder_reanalysis_does_not_overtake_waiting_first_inventory` (a revisit
never jumps ahead of a package that has never been analysed).

**Previously decrypted rootfs is reusable as a recovery source** when its receipt
binds the component hash, size, section, recipient and model package to the exact
original source SHA-256. Recovery verifies downloaded bytes and accepts only the
requested public recipient fingerprint; tests recover a new synthetic key from a
filesystem encrypted with an older key and reject stale, malformed or conflicting
receipts.

**What remains needs keys, not code.** The 27 unmatched recipient groups cannot
be progressed without private key material, which exists only as CI secrets. No
key was read, generated or committed during this work.

## Publication recovery (item 18)

Each failure mode the item names has a regression, and the live archive shows the
behaviour holding.

| Failure mode | Regression |
| --- | --- |
| Interrupted upload leaves staged records out of the catalog | `test_interrupted_upload_cannot_expose_staged_catalog_records` |
| Successful work survives a later unrelated failure | `test_successful_packages_publish_after_extraction_and_upload_failures`, `test_raw_upload_failure_keeps_earlier_source_publication_and_no_raw_receipt` |
| Lost response after GitHub stored the delivery | Android delivery reconciliation tests (`response lost after GitHub stored the delivery`, `upload response lost`) |
| Conflicting filenames keep distinct identities | `test_parallel_urls_with_same_filename_have_distinct_immutable_captures`, `test_new_url_with_same_filename_is_published_without_overwriting_old_bytes`, `test_snapshot_filename_alias_keeps_its_existing_catalog_identity` |
| Changed content at a stable name | `test_size_only_cache_does_not_hide_changed_bytes_at_a_url`, `test_a_resource_with_different_content_leaves_the_capture_unrecorded` |
| Partial delivery stays pending | `test_partial_delivery_stays_pending_when_original_inputs_are_unavailable` |
| Overflow into numbered partitions | `test_metadata_commit_preserves_unrelated_local_work_and_retains_patch`, `test_deleted_tracked_metadata_is_published` |

The Android receipts show the multi-component case in production: 34 of the 67
APKPure deliveries carry more than one component plus a complete wrapper, and all
475 components are individually verified, so a split that fails to upload cannot
mark its delivery complete.

The publication audit is the end-to-end check that this holds across the whole
archive: 135 releases, 4,551 assets, 0 missing, 0 mismatched, and every unrecorded
extra explained as a duplicate publication.

## Live archive runs: mobile and GPL archive

Two targets were dispatched against the isolated validation archive to extend the
mobile backlog and GPL publication evidence. Both are recorded here with their
actual outcome, including the parts that did not finish.

### `mobile` — [run 44470878436](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44470878436)

Result: `status: partial`, reported as a failed job because incomplete work is an
error by design, not a silent success.

```
discover_mobile_installers: ok      candidates=4 live=2
archive_mobile_installers:  failed  hash-verified artifacts=2 signature-checked=2 attempted=1 new=0
archive-installer-history:  failed  pending captures remain; verified progress retained
```

The archive state advanced and was committed as
`Update mobile archive state` (`065a64a`, +24/−7 in `mobile-archive.json` and
+227/−3 in `mobile-history-index.json`). The two preserved installers are both
Sonos-hosted S2 builds under `apps-fireos-s2`:

```
85.00.12    Sonos_85.00.12.apk    339,757,559 bytes
91.01.11    Sonos_91.01.11.apk    259,903,267 bytes
```

Both were **hash-verified and signature-checked in CI** (`signature-checked=2`),
so this run extends the signer-verified delivery evidence rather than only the
file count. `new=0` means discovery found nothing beyond what was already
preserved; the backlog for the reachable graph is exhausted, which is a result
rather than a failure.

Two gaps remain and are recorded with their reason rather than dropped:

```
70.1-12345   HTTP Error 404: Not Found   (advertised twice, both 404)
```

### `gpl-archive` — [run 44470939384](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44470939384)

Result: `status: ok`. The run committed
`Update gpl-archive archive state` (`9a1e561`, +1,933 lines in `artifacts.json`).

```
attempted=90   verified_bytes=1,431,932,151
```

Its `report.json` records the resulting archive state:

```
published_downloads: 187          verified_downloads: 187
published_source_archives: 173    fully_published_releases: 6
verified_index_total: 26          sonos_download_links: 701
external_upstream_links: 208
fully compressed-stream verified source archives: 25
awaiting full stream verification: 148
```

Across the 7 versions now in that archive (`6.4`, `7.0`, `7.1`, `7.2`, `7.3`,
`7.4`, `8.0`) every recorded artifact is `published`: 187 of 187, totalling
2,854,074,746 bytes. That run published the whole 7.2–8.0 source set, including
the large kernel archives (`linux-3.10.53.tgz` at 116,446,877 bytes) and the
upstream glibc tarballs, so "follow their source links" is exercised live rather
than only offline.

The report states its own limit: `completeness_proven: false`, with 148 published
archives still awaiting full compressed-stream verification. Those are queued for
the stream auditor, not counted as verified.

### `firmware` — [run 44472258803](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44472258803)

Result: `status: partial`, with `errors: []` and two steps reporting incomplete
source work. State committed as `Update firmware archive state` (`93ab9d7`,
+6,518 lines in `catalog.json`).

```
discover_firmware:            partial   manifests=33 candidates=1307 checked=1371 pending=877
archive-historical-firmware:  ok
extract-firmware:             partial
audit_connectx_coverage:      ok        advertised=9 preserved=9 distinct=9 unavailable=0
```

This run is the item 14 evidence: it is the target that performs extraction,
inventory and recipient-key work with the repository's CI secrets.

**Recipient-key state after the run** (`data/key-recovery-ledger.json`):

```
encrypted UPD packages: 129     plaintext packages: 14     unidentified: 0
unique recipients: 34
recovered keys: 7
missing keys: 27
```

**Extraction and inventory state:**

```
packages extracted: 143   (all status "extracted", errors [])
packages with ready recipients: 45
inventories published: 187   complete 94   incomplete 93
```

So the limitation is key material, not decoders: 93 inventories remain
incomplete, and the extraction report names the specific recipient groups and
section indices it skipped with `"reason": "no matching model key"`, for example
`0cfddc3f6ac5c485ccc435885c42c52a9338e28b` in `57.5-87010-1-5` sections 3–5 and
`83fb98ffd324255b26fdab99105a1a02a82bae93` in `57.5-87010-1-25` sections 4–7.
Those are actionable: each names the recipient that a key would unlock.

The run also published new firmware state rather than only reporting:

```
new releases: firmware-57.19-46310, firmware-57.5-87010, firmware-64.3-19080,
              manifest-snapshots-2026-11-19
raw components registered and verified per release: 4–11 each
```

ConnectX coverage in that archive is complete for what it advertises: 9
advertised, 9 preserved, 9 distinct contents, 0 unavailable.

The 877 pending discovery candidates and 93 incomplete inventories are real
backlog, not silent loss: both are counted, both are queued, and both keep the
target's status at `partial` so the next run resumes them.

### `desktop` — [run 44477307992](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44477307992)

Result: `partial`; the discovery step is `partial` and the two archive steps exit
non-zero because incomplete work is an error by design. State committed as
`Update desktop archive state` (`e9fb7f5`, +1,169/−114 in `desktop-archive.json`,
+223/−5 in `desktop-history-index.json`, +686/−19 in `desktop-package-history.json`).

```
discover_desktop_installers:  partial  candidates=245 available=201
archive_desktop_installers:   failed   verified artifacts=40 attempted=12 new=7
archive-installer-history:    failed   pending captures remain; verified progress retained
```

Seven new desktop installers were published this run. The archive now holds 41
artifacts across 24 versions:

```
macos: 23   windows: 18   all with sha256
release tags: apps-s1-macos 11, apps-s1-windows 11, apps-legacy-macos 7,
              apps-legacy-windows 7, apps-s2-macos 5
fetched from: update-software.sonos.com 29, update.sonos.com 12
```

Discovery sources recorded by that run are three independent routes, not a
download list:

```
microsoft/winget-pkgs, Homebrew/homebrew-cask, Sonos CDN HEAD
```

Per-artifact provenance names the exact commit and path each candidate came from,
for example `homebrew-history:3fdfc96072ef7b21b24beb519098ae3a7792c077:Casks/sonos.rb`,
alongside a `derived-cross-platform` route for 18 artifacts identified from a
sibling platform's build. 4 direct redirects were followed from the Sonos CDN.

### `release-audit` — [run 44477327742](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44477327742)

Result: `ok`. State committed as `Update release-audit archive state` (`97da515`).

```
releases=49 github=4459 recorded=4375 missing=0 mismatched=0
extras=84 duplicated=81 unexplained=3
```

The classifier did its job on a second archive: 81 extras are proven duplicates
by exact SHA-256, and it isolated 3 that were not. Two of those three are a
**concurrency artifact, not a defect**, and the timestamps prove it:

```
apps-google-play-recovery-s1 / com.sonos.acr--11.16.2--base--9a42547d….apk  uploaded 07:09:30Z
apps-google-play-recovery-s1 / com.sonos.acr--11.2.3--base--d000e43b….apk  uploaded 07:09:48Z
release-audit committed                                                     07:09:50Z
android-history run (still in progress)                        created 07:08:13Z
```

The `android-history` target was mid-run and had uploaded those two APKs 20 and 2
seconds before the audit committed, so the audit correctly saw assets whose
metadata commit had not landed yet. That is the audit being strict rather than
wrong: it observed a publication in flight. A follow-up audit after that run
commits is the check that they disappear.

The third is a genuine orphan in this archive: a page snapshot published on
2026-11-17 that no catalog record names.

```
manifest-snapshots-2026-11-17
page-14b38cf71a2e49bbb9e5671580e42ffe36ba1602c2737b7ad5b1c95825d0d225-859e67acbddb003742f0b63a26cc3ee2ee26de97499747e4413f22a0ff425396.html
72,652 bytes, uploaded 2026-11-17T16:23:15Z
```

Its content hash is absent from all 2,198 `source_documents` entries and from the
frontier state, so the publication outlived its catalog record. This is the same
class of evidence loss that `reconcile_web_release_assets.py` repairs for web
captures, and it is recorded rather than deleted. The production archive has zero
unexplained extras, so this is specific to the isolated checkout.

### `android-history` — [run 44477286282](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44477286282)

Result: `partial`; the sweep step exits non-zero while selectors remain, which is
how it reports bounded progress. State committed as
`Update android-history archive state` (`4d51027`).

```
sweep_android_store_history: failed (pending)   exit_code=1
archived: com.sonos.acr 11.16.2, 11.2.3, 11.15.2, 11.15.1, 11.15, 11.14
          com.sonos.acr2 90.00.43, 89.01.11, 89.00.51, 89.00.49, 88.01.10,
                         88.00.43, 86.01.5
```

The sweep's own index records the bounded-progress state:

```
attempt_total: 67     archived_total: 66     failed_total: 1
discovery_errors: 0
observed_variants: 61
pending_selectors: 76
sdk levels indexed: 35     sdk_hints: 12
```

So 66 of 67 attempts archived a delivery, with one failure and no discovery
errors, and 76 selectors remain queued for later runs. The SDK index covers 35
levels, which is the SDK/ABI dimension the item asks about.

The store archive now holds 45 deliveries across **38 distinct version codes**:

```
s2: 13 version codes, 1270001011 .. 890000051
s1: 25 version codes,  430354030 .. 572383122
components: 301   multi-component deliveries: 20
```

Component verification splits cleanly along type, which is the honest reading:

```
APK components:      260, all 260 signature-verified, all with a signer SHA-256
non-APK components:   41 (20 icon.png, 20 manifest.json, 1 APKComboInstaller.url)
                      hash-verified but not signature-applicable
```

No APK is unverified. The 41 non-APK components are additional delivered
artifacts that carry no APK signature to check, so "all APKs signature-verified"
is accurate while "all components signed" would not be.

### `release-audit` follow-up — [run 44479999381](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44479999381)

Result: `ok`. This run is the check that the earlier 3 unexplained extras were a
concurrency artifact:

```
before (44477327742): releases=49 github=4459 recorded=4375 extras=84 duplicated=81 unexplained=3
after  (44479999381): releases=50 github=4580 recorded=4498 extras=82 duplicated=81 unexplained=1
```

The two Google Play APKs disappeared from `unexplained` once `android-history`
committed, confirming they were assets uploaded seconds before the audit read the
release. One genuine orphan remains in this archive — the page snapshot from
2026-11-17 — and it stays listed rather than being deleted or ignored.
