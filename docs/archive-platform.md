# Archive platform

## Adding a target

1. Implement an adapter in `src/sonos_firmware/platform/providers.py`, or expose
   a callable through the `sonos_archive.providers` Python entry-point group.
   An adapter accepts a `Context` and returns `Step` declarations. Constructing
   a plan must not perform network requests, downloads, or filesystem writes.
2. Declare its name, provider, description, cadence, and owned metadata paths
   in `config/targets.json`. Declare worker capabilities such as `java` or
   `apkeep` or `goopdl` when required. Targets that share mutable metadata use the same
   `lock`. Public protocol roots and product identities belong in the adapter;
   discovered release URLs belong in its observations.
   Tools used by only part of a provider can be declared in
   `optional_capabilities`; their absence must produce a partial source report
   while supported discovery and acquisition continue.
   Android listing discovery continues without Java; new payload downloads
   defer until the APK inspection toolchain is available.
3. Add offline fixtures that exercise discovery from an empty state, failed
   acquisition, and retry after partial publication. Plan it with
   `sonos-archive run TARGET --plan`. No binary inputs or precomputed hashes
   are required to register, plan, or schedule a target.

The workflow creates its matrix from these declarations. A new target does
not require a workflow, release URL list, or local archive. Source-specific
parsing and validation remain in their adapters; scheduling, temporary storage,
reporting, and metadata commits use the shared runner.
Publication receipts in declared metadata paths feed binary inspection
automatically; a new adapter needs no separate inspection URL list or workflow.

Desktop package history walks WinGet commit pages and both changed and parent
snapshots. Removed manifests and pre-alphabetical vendor layouts therefore
remain discoverable. Page cursors and unattempted Git objects persist in
`data/apps/desktop-package-history.json`; immutable metadata is cached in the
worker's observation directory. A parser change replays cached objects. The
`metadata_limit` option bounds each worker's graph work without a lifetime
history limit. Current installers continue while historical work is pending.

For installer sources, `sonos_firmware.app_archive.AppSource` supplies a shared
acquisition loop. Its `acquire(candidate, destination)` callback streams one
payload into worker storage and returns its fetch URL. `release_for(candidate)`
returns a release tag and title. The common loop computes size and SHA-256,
checks any discovery checksum, publishes without overwriting, and reconciles
GitHub's size and digest before adding a preserved artifact. Source adapters
do not need their own hashing, retry ledger, or publication implementation.

## Starting with no archive

The configured public protocol, redirect, sitemap, and store-discovery roots
are sufficient to start with no `data/` state and no GitHub Releases. Workers
create their catalogs, retry ledgers, and destination releases as they discover
and preserve sources. Adding a target requires neither locally downloaded
firmware nor an inventory of release assets or hashes. Configure repository
write credentials for CI; authenticated stores retain their declared capability
requirements.

Earlier receipts and releases improve historical coverage and permit exact
publication reconciliation. They are optional inputs: binary-derived GPL leads
supplement independent sitemap and version-grammar discovery. Firmware source
preservation runs before extraction and key recovery. An empty extraction queue
needs no keys or release downloads; unavailable keys leave encrypted captures
preserved with their extraction gap recorded.

Installer candidates may carry an `acquisition_id` for an individual historical
capture. Their retries and interrupted uploads remain independent even when the
source URL is identical. Equal bytes share an immutable asset while retaining
each acquisition context in the receipt.

Offline fixtures exercise protocol discovery through the first verified firmware
publication into an empty destination, installer and web release creation,
GPL discovery without prior versions, and the actual empty extraction worker.
Run the cross-platform bootstrap checks with
`python scripts/run_tests.py --pattern 'test_empty_platform.py'`.
They do not claim that a source which has disappeared upstream can be recreated
from nothing; unavailable historical bytes remain a documented discovery gap.

Live validation on 2026-11-17 used a separate
[empty validation repository](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44121253300),
with no archive metadata, existing releases, cached observations or supplied
private keys. Its initial run preserved 24 firmware/installer sources, registered
181 raw components and published four inventories. A
[subsequent firmware run](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44136098248)
raised those totals to 48 sources, 333 raw components and eight published
inventories. Recovery from verified legacy updater sources found six recipient
keys for models 1, 8, 9, 12, 16 and 17. Twelve recipient groups still lacked keys.

The subsequent run also reclassified the older navigation-heavy discovery
queue: pending document URLs fell from 34,010 to 340, while retained manifests
increased from 27 to 29 and firmware candidates from 1,054 to 1,185. Publication
errors were zero; 458 candidate captures remained pending. This is evidence of
cold-start acquisition and continued discovery, not exhaustive historical
coverage. Google Play HTTP 403 responses, unavailable historical indexes and
encrypted sections without keys remain explicit, retryable gaps.

The [expanded archive's release audit](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44178000955)
checked 1,693 recorded assets across 27 releases against GitHub's sizes and
digests: none were missing or mismatched. It separately reported 82 unrecorded
copies. Of these, 81 were older web resource publications overwritten in
receipts when identical deployment content was captured again. The receipt
merge now retains every verified publication without counting the same resource
as additional deployment content; replaying the two saved live receipts retains
all 81 references. The remaining extra is a page snapshot without a completed
catalog record; an extra asset alone is not treated as a preservation receipt.

## Worker lifecycle

Historical index discovery queries both manifests and `.upd` payloads across
`sonos.com` and all its subdomains, using the CDX
[domain match scope](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md#url-match-scope).
Installer history uses that same domain scope with its own file types. Newly
observed Sonos hosts and directories require no host-list or path-prefix changes.
Direct payload leads enter the same acquisition
queue even when no manifest remains available. Filename metadata can identify
a version and model; an unrecognized filename stays unclassified and can still
be preserved and inventoried. Its source evidence remains separate from its
verified publication receipt.

The index retains timestamped capture variants rather than one row per URL,
including variants behind static filenames. Query upgrades reset incompatible
resume tokens while keeping earlier observations. Pending index pages and
unvisited hosts remain explicit discovery work. The [CDX protocol documentation](https://github.com/internetarchive/wayback/blob/master/wayback-cdx-server/README.md#collapsing)
describes digest-based collapsing; an index digest is evidence of a capture,
not a replacement for the SHA-256 computed from acquired bytes.

Live metadata starts at HTTPS Sonos domains. Manifest traversal, firmware probes,
installer redirects and acquisition also follow public HTTPS CDN locations
explicitly advertised by those sources or the Sonos package manifests in
WinGet/Homebrew. Each external lead retains its originating Sonos URL or package
repository, path and Git object identity through saved state and acquisition.
New CDN hosts need no allowlist edits. Unattested external seeds, credentials,
nonstandard ports and private network destinations are rejected; redirect
destinations are checked before following and cross-host credentials are removed.
Manifest images pointing
to landing pages enter the same resumable traversal. HTML attributes, base URLs,
refresh redirects, script references and literal download URLs use the shared
web link discoverer. Page redirects to known payload formats become download
leads without reading payloads into the page cache. Unvisited pages survive the
crawl budget, and source challenges pause their host along with manifests.
Firmware-page traversal follows payload/manifest links, scripts and module
imports, browser redirects, software download/release links and adjacent pages
in the same directory. Site navigation, privacy links and schema namespaces
remain in raw snapshot evidence without becoming a recursive firmware crawl.
Earlier broad queues are reclassified from protocol roots and document edges;
the worker records excluded queue/document counts and retains firmware leads.

Empty pages, malformed links and unsupported download locations remain explicit
coverage gaps. Static links cannot silently turn a landing page into a successful
firmware capture.

Exact page and script bytes publish through the same source-document publisher
as update manifests. They use immutable content-and-origin asset names and
verified publication receipts in `data/catalog.json` (`source_documents` for
pages; the existing `source_manifests` collection retains update manifests).
The page's parsed links stay with its receipt so discoveries retain provenance.
Reprocessing an already-published body can refresh its parsed metadata without
another upload. The shared link parser also serves web deployment captures.
Replayed manifests use the same document-versus-payload routing. Saved replay
metadata is reinterpreted on later runs without another payload acquisition;
unresolved links remain in its metadata and coverage report. Previously queued
HTML pages move to document traversal rather than being published as firmware.

The firmware worker replays indexed manifests and payloads automatically, using
separate bounded queues (`history_manifest_limit` and `history_limit`, both eight
per run). Each timestamped payload has its own acquisition identity even when
the live URL still serves a newer version. Replay redirects must retain the
requested timestamp, original source, and unmodified response mode. Access
challenges pause further replay requests for that run. Saved index challenges
use the shared retry cooldown; an expired or undated error cannot pause replay
indefinitely.

Historical manifests use the common manifest publisher and parser, retaining
their original bytes, including gzip representations. Relative image links
resolve against the vendor URL and join live discovery without assuming current
availability. Undecodable manifests remain preserved with a discovery gap.
Publication journals reconcile interrupted uploads without downloading source
bytes again when GitHub already holds the exact asset. Neither historical queue
requires an existing catalog, release, source checksum, or local firmware file.

The Actions worker discovers candidates and records their provenance. It
acquires actual bytes in temporary storage and hashes them there. A hash
identifies the captured content and allows verification against GitHub's
published bytes; a URL or a successful HEAD request alone cannot establish
that a payload was preserved. Source-specific publishers verify publication
before importing preserved records into their inventories.

Firmware acquisition computes hashes from the received bytes without requiring
a supplied checksum. Distinct captures at the same advertised filename receive
separate immutable assets. Verified source publication is independent of
extraction: new packages enter the catalog with extraction pending, and a parser
failure is recorded without blocking source preservation. Extraction uses the
exact recorded asset and package identity, saves all available section types,
and records encrypted sections still awaiting a matching key.

The runner cleans its temporary workspace on completion or failure. Small
manifest observations are cached separately in the ephemeral worker so that
publication can retry an exact observed body after a source URL changes.
Payload binaries and private keys are excluded from that cache. Only metadata
is committed to Git. The workflow retains a run report and metadata checkpoint
as Actions artifacts even when execution or its metadata commit fails.

Each target has a report under `data/platform/targets/`, containing step
outcomes, errors, pending work, and its next due time. Incomplete runs retry on
the next hourly tick. The CLI emits redacted phase start/finish messages to
stderr as work proceeds; stdout remains the final JSON report. Successful targets
follow their declared cadence. Targets
with a common metadata lock execute in order. Independent sources can proceed
when another target fails. Android history uses a bounded, rotating queue and
retries failures rather than permanently suppressing them.

The shared scheduler saves a retry/fresh rotation in each worker checkpoint.
With a one-request budget, eligible retries and unattempted leads alternate
across runs; neither needs the other queue to empty first. Larger batches keep
their reserved retry capacity. Rotation is scoped to the repository or filtered
queue and survives worker restarts. It does not bypass source cooldowns or count
an unattempted journal entry as a retry.

App and firmware publication schedule interrupted-capture recovery and source
acquisition together under the same attempt limit. A missing older upload can
retry without monopolizing a one-attempt worker. Source hosts already paused
for the run are excluded from acquisition selection, while publication
reconciliation remains possible without fetching their payloads. Profile,
model inference, GPL, key recovery, extraction, inventory and delivery queues
use the same saved rotation policy.

Desktop and official APK acquisition share per-repository URL observations.
Desktop discovery walks nonrecursive Git trees under WinGet's Sonos vendor
directory, discovering package names and retained version directories from the
repository's current default branch. This avoids the GitHub Contents
[1,000-entry directory limit](https://docs.github.com/en/rest/repos/contents#get-repository-content).
Truncated tree responses and failures in individual versions remain explicit
gaps while other entries continue. Installer YAML is parsed as data without
constructing objects, retaining URL/hash associations and usable URL leads
when a checksum is absent or malformed. Distinct advertised checksums for one
URL have separate acquisition identities; observed hashes come from source
metadata, and the worker computes and verifies actual payload hashes in CI.

Homebrew history follows all commit pages for its source paths. Failed listing
or file requests remain discovery errors, and later paths and commits continue.
GitHub API access challenges pause further API requests for that worker run;
raw-source challenges also stop the Homebrew sweep. Their incomplete reports
remain eligible for the next scheduled retry.
Observed version substitutions resolve the URL actually declared in each cask;
the adapter does not rebuild a distribution hostname and filename from a version
pair. Unrecognized Ruby interpolation stays an explicit gap, with no recipe
execution. Historical HTTP Sonos URLs upgrade to HTTPS while retaining their
advertised aliases. Neither package source requires a supplied local payload or
checksum before discovery or archival can begin.

Current download URLs refresh at the target cadence; historical URLs refresh
weekly by default. Changed checksums, ETags, versions, or HTTP availability
become eligible immediately. Attempts include failures and are bounded; prior
leads remain available when a new discovery report omits them. An unavailable
or partial discovery report remains explicit even when earlier captures can
finish publication.

Desktop and official APK history use the same paginated archive index and exact
replay validation as firmware. Every indexed capture has a separate acquisition
identity, even when its live URL still serves another version. `history_pages`
defaults to one index page per run and `history_limit` to eight acquisitions.
The `desktop-history-index.json` and `mobile-history-index.json` observations
retain cursors and captures under `data/apps/`. Both stages run automatically
after current discovery, including when that discovery is incomplete. Current
and historical installer acquisition share one implementation and publisher;
previously published historical captures refresh against destination metadata
without requiring their upstream bytes again.

Captured content is staged as unpublished metadata before upload. If an upload
response is lost, a later worker can reconcile the exact recorded asset without
re-fetching its original URL. Changed bytes at one URL produce separate assets.
Older unresolved capture receipts remain visible and are retried independently;
they never become preserved solely because a newer capture succeeded. Metadata
checkpoints use the same atomic JSON writer across discovery, app acquisition,
GPL acquisition, and orchestration.

Android store deliveries checkpoint their verified component identities before
release creation or upload. A later worker can reconcile a complete stored
delivery without the original download. Changed bytes at the same version code
remain separate captures; selector aliases for identical bytes are retained.
Failed captures are never promoted when only part of the delivery was uploaded.
Each package can make progress independently, and previously observed selectors
remain queued when a later store listing omits them or fails.

Saving an unattempted lead does not turn it into a retry. Shared bounded queues
reserve capacity for due retries alongside new leads, including when both are
already recorded in a durable journal. Explicit cooldowns remain in force.

Web deployment capture runs each public site independently without a separate
probe prerequisite. Web bodies, raw firmware images, and signer-verified Android
store deliveries use the same collection publisher. It checks every intended
name before upload, streams missing captures, and reconciles size and SHA-256
before promotion. Web capture budgets, source
errors, and interrupted publications remain in the receipt for later workers.
Adding a web deployment to the archive needs no existing destination release.
Later batches prioritize saved pending URLs. Receipts retain all verified bodies
and each capture-batch bundle as coverage accumulates; a smaller later batch
cannot replace earlier captures. A fixed probe that did not complete has no
asserted deployment fingerprint. When original worker files remain available,
publication retries reuse them; otherwise they reconcile any exact captures
already present at the destination without requiring those files.

GPL discovery also scans this archive's published app, web and firmware assets
automatically. It verifies their recorded size and hash in temporary CI storage,
then inspects decompressed program/text resources and extracted firmware bundles
without executing code. `binary_limit`, `binary_scan_bytes` and
`binary_source_bytes` bound each run, while `binary_entry_bytes` limits a single
archive member. Source failures and incomplete decoding
retry independently; changed decoder code or limits make prior captures eligible
again. Previously found references survive later partial scans. Negative scans
retain small hash/status checkpoints rather than full inventories.

Binary evidence feeds the existing GPL index queue under the same metadata lock.
Only a separately validated Sonos index establishes an available GPL release.
Sitemap and index discovery continue when binary inspection is incomplete, so
an unavailable archived asset cannot block independent discovery. Numeric
exploration uses a saved ordinal rather than fixed major/minor bounds.
`discovery_window` bounds speculative lookahead, while `limit` bounds requests.
Observed versions, adjacent hypotheses and retained failed versions remain
eligible independently of that window. Fresh evidence and numeric exploration
rotate even with a one-request budget. Access-challenge cooldowns apply to the
whole source, so a new version cannot bypass them. No downloaded files or
precomputed hashes are required to add or plan a target.

`google-play` runs on the normal schedule using the pinned goopdl Python extra
and Aurora's anonymous token dispenser. Personal accounts and repository Google
credentials are unnecessary. Its complete upstream device profile inventory
rotates across runs, interleaving architectures. Each observed numeric version
becomes a retained acquisition lead. Discovered APKPure metadata and existing
receipts contribute historical numeric codes. An empty archive starts from
live discovery; APKPure metadata can supply codes without acquiring any APKs.
Google Play does not expose an exhaustive historical version list: accepting a
requested code does not prove Google still serves that version.

Both Android stores use `sweep_android_store_history.py`, with source-specific
ledgers and a shared metadata lock. Discovery observations, unattempted leads,
failed attempts and cooldowns survive worker exit. A 429 pauses the whole source
until `Retry-After`, or an exponential delay if none is supplied, then retries
the same operation while the worker's time budget permits. A longer cooldown
stays pending for a subsequent worker. There is no terminal retry count. Ordinary
failures also remain retryable, with shared queue fairness and backoff.
Package identities, archive families and signer trust anchors live once in
`config/android-apps.json`; adding an Android app requires that declaration,
not a list of versions, release URLs, predownloaded APKs or artifact hashes.

Google Play acquisition checks the returned delivery code before downloading,
then checks the actual APK manifest before publishing. Every returned base APK,
split, dex metadata file, OBB and asset pack is requested. Every APK must pass
signature verification against the configured Sonos signer. Identical deliveries
are deduplicated while retaining all observed device profiles. Signed URLs,
cookies and dispenser tokens stay in ephemeral worker storage outside metadata
caches and uploads. Apple listing preservation remains a separate scope.

APKPure discovery combines its structured version API with apkeep's older
selector listing. The API protocol and public client headers follow
[Obtainium's APKPure adapter](https://github.com/ImranR98/Obtainium/blob/main/lib/app_sources/apkpure.dart).
Every advertised asset and architecture is retained separately, even when
multiple assets share a numeric version code. Malformed rows report partial
discovery while valid rows continue. Unknown formats remain pending leads.
SDK context also affects APKPure history. Contexts come from Google's public
[Android SDK repository index](https://dl.google.com/android/repository/repository2-1.xml)
and observed app minimum/target SDK fields, rather than a maintained SDK list
or a maximum SDK constant. The documented client SDK is only a bootstrap.
Contexts rotate within each worker's discovery budget; a successful empty
context is refreshed periodically and does not declare the app unavailable.
An SDK-index outage does not pause independent APKPure requests.

Variants are normalized once with references from each SDK context, keeping
duplicate API responses out of committed state. Rotating Winudf download tickets
refresh the usable URL without creating another asset identity. Version, ABI,
format, path and content-selecting parameters still distinguish assets; unknown
CDN query parameters are retained. Earlier ticket-based observations and verified
attempts migrate without downloading those deliveries again. Raw responses stay in the
worker's observation cache, separated by SDK, and are reinterpreted on later
runs, including when the live API is unavailable. No SDK packages are acquired.

Structured asset acquisition checks the actual APK version against the metadata
code, verifies every APK signer, and uses the same delivery publication journal
as other Android acquisitions. Identical bytes retain all observed variants and
source URLs. APK asset names use manifest base/split roles. When identical
content already has publication names or a partial-upload journal, those names
are reused so a naming change can finish the original capture. Google Play reads numeric hints from the discovery ledger; this
does not require a prior payload acquisition or an existing release.

Older APKPure selector discovery and acquisition use the upstream apkeep
release binary, pinned by SHA-256 in `tools/install-apkeep`. CI downloads the
binary for its Linux architecture instead of compiling Rust. Neither an API
response nor a selector listing proves exhaustive historical coverage. Failed
or malformed listings stay retryable and cannot become a complete result.

## Validation and publication

Web discovery preserves a public login shell served by the deployment host and
follows its static script/style references. The shell retains its actual login
URL and evidence type; the authenticated application remains an incomplete
capture. Login HTML returned for a JavaScript asset is never substituted for
that script.

Local plans and tests do not acquire live artifacts. `sonos-archive run TARGET`
is a plan unless `--execute` is supplied, and execution rejects an environment
outside GitHub Actions before starting commands or creating files.
Run `python scripts/run_tests.py` for the offline fixture suite. Its guard
rejects unmocked Python sockets, native HTTP requests, and external network
tools; an attempted request fails the run even if source code catches it.
Child Python tests inherit the guard, and Git fixtures use only file transport.

The shared metadata commit command validates JSON and the firmware catalog,
then stages only the target's declared data paths and its checkpoint. It refuses
to include pre-staged unrelated changes.

Release auditing also follows target metadata declarations. Completed legacy
collections and verified publication proofs establish expected assets; pending
upload journals and the auditor's own output do not. Child assets inherit their
collection's tag, repository-specific proofs stay scoped to that destination,
and conflicting identities fail validation. The audit uses the shared paginated
release reader rather than relying on assets embedded in a release listing.

GPL compressed-stream audits use the same durable retry queue as discovery.
Earlier 403/404 responses, invalid sources and content mismatches remain pending
with backoff; no manual retry flag is needed. New sources and failed checks share
worker capacity. `stream-audit.json` reports outstanding work to the runner so
an incomplete audit is scheduled again promptly. `--retry-failed` only bypasses
the saved cooldown for an explicit run.

Firmware extraction uses a bounded queue with durable retry timing. Failed
packages back off; newly available keys and changed source hashes become
eligible immediately. A failure does not stop extraction of other packages.
Each extracted package has its own publication transaction. Catalog changes
stay in worker staging until upload verification succeeds, including when a
worker is interrupted. Failed publication retains earlier verified sources
and extracted packages. New raw receipts are installed atomically after every
intended asset has been reconciled, including assets missing from local staging.
Receipt updates merge coverage by section index, retaining earlier preserved
sections when the current worker cannot decrypt them.

The firmware target declares mutable protocol artifacts in `artifact_roots`,
alongside public manifest discovery. These roots carry product identity and
refresh cadence, not release versions or hashes. Their captures use embedded
versions when no version was advertised, retaining distinct bytes and historical
catalog identities. Warm discovery also rechecks this archive's own observed
URLs; cold discovery excludes that history.

UPS responses feed that same frontier. Observed caret base URLs expand directly
over the configured package-model range, even when their sibling manifest is
missing. The first pass defaults to 0–255. Each completed pass doubles the range
for the next pass, revisiting earlier IDs while seeking higher ones. `model_max`
seeds the initial range; it is not a permanent ceiling, and increasing it does
not erase a saved cursor or failure cooldown. Explicit models found in manifests
remain eligible immediately. Response hashes bind these candidates
to their source observations, and distinct raw UPS responses remain in the
worker's metadata cache under content-derived names.

The firmware worker also explores synthetic metadata profiles automatically:
`profile_limit` defaults to eight requests per run, with `profile_count` setting
a planning window of 1,200 rather than a lifetime ceiling. A saved ordinal
advances through unbounded field-axis, scaled-build and full-combination probes.
Observed versions take priority. Unattempted profiles are checkpointed separately
from completed observations, so changing hints, budgets or deadlines cannot
discard pending work. Fresh hint and broad-search requests rotate independently
of retries, so a stream of new version hints cannot starve exploration. Cold
discovery needs no version inventory. Failed
profiles back off, successful and negative profiles refresh, and endpoint access
challenges have a shared cooldown that new profiles cannot bypass. Reports expose
the cursor, current window, negative results and remaining pending profiles.
`model_max` configures
base-URL expansion. These options use the usual target configuration or `--set`
interface and require no workstation downloads. Deferred manifest roots retain
FIFO priority across runs so a small crawl budget progresses through the queue.
A rotating reservation of at most one quarter of the crawl budget refreshes
current protocol feeds; a one-request budget goes entirely to the FIFO.

The same worker expands directories evidenced by manifests and this archive's
own UPD or installer observations, and checks their public production-host
counterparts. `inference_limit` defaults to 128 generated candidates per run.
Each namespace retains a model cursor and retry timing instead of storing a
URL row for every negative probe. Available discoveries remain ordinary source
observations. Incomplete namespaces resume automatically; completed ranges
widen and revisit earlier IDs. Transient failures retry the same model without blocking other
namespaces. Completed legacy checkpoints automatically enter expanding passes;
the report distinguishes pending work in the current ranges from the continuing
exploration policy. Cached available images retain cursor order when revisited.

Public release-note versions are additional profile-query hints. Their source
hash and observed versions are retained, including across source outages; they
do not supply campaign URLs. The former monitor, directory sweep and separate
monitor publisher have been retired. The sole firmware path uses the frontier
and shared verified publisher. Historical public URL observations used by
replay tests live under `tests/fixtures/`, outside runtime configuration.

All preserved UPDs share one inventory queue. Available sections, tar members,
CRAMFS/SquashFS filesystems and supported kernel streams are analyzed without
executing firmware. Decoder failures retain the original payload and remain
explicit. Full JSON inventories and extraction bundles go to Releases, with
verified references in the catalog; large filesystem listings are not duplicated
in Git. Stored ciphertext is referenced by its span in the preserved source,
while the optional analysis command can also carve it into separate files.
Inventory work resumes from `data/discovery/firmware-inventories.json`, including
interrupted uploads. New keys, changed sources and decoder code changes make
prior inventories eligible automatically. `inventory_limit` bounds each worker
batch independently of primary component extraction.
When an interrupted upload is already present remotely, the next worker verifies
it without source acquisition. If an asset never reached GitHub, the worker
rebuilds from the preserved UPD; the unfinished capture's hashes and successful
receipts remain in the journal. Recovery checks have their own bounded allowance
so missing uploads cannot consume the only acquisition slot. An inventory is
referenced in the catalog only after both its manifest and bundle are verified.
`inventory_limits` configures expanded bytes, file count, directory depth and
decoder timeout. Compressed tar metadata is bounded before parsing, and legacy
kernel scans have separate work and output bounds.

The firmware worker refreshes recipient coverage before preparing keys. Legacy
recovery selects hash-verified plaintext rootfs assets, with the original UPD
as a fallback. It detects CRAMFS/SquashFS from content and reads `bin/upgrade`
without executing firmware. Bounded wrapper discovery tries the supported
derivations and validates the requested recipient; it needs no fixed release
list, model-specific binary offset, or pre-downloaded local files. Failed
sources back off in `data/discovery/key-recovery.json`, while later candidates
continue. Successful sources are replayed to reproduce keys in a fresh worker.

Private keys remain in temporary worker storage. The public ledger retains
recipient fingerprints and recovery provenance, including source and updater
hashes. Recipient-specific key files preserve rotations and allow different
encrypted sections to use different keys. Optional configured keys can use
`SONOS_PRIVATE_KEYS`, a JSON object mapping recipient fingerprints to PEM
strings, or the existing per-model environment variables. Model-26 recovery
continues to require its hash-bound public MDP3 and OTP evidence. A failed or
unsupported recovery remains explicit and does not block plaintext extraction.

Use the Actions run report to inspect errors and remaining work. Bounded
completion is not proof that all historical releases have been discovered;
coverage records must retain unresolved gaps and source failures.

The workflow uses GitHub's documented [`queue: max` concurrency option](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency)
so waiting jobs sharing a metadata lock are retained. Older local versions of
`actionlint` may not recognize this option.
