# Archive platform

## Adding a target

1. Implement an adapter in `src/sonos_firmware/platform/providers.py`, or expose
   a callable through the `sonos_archive.providers` Python entry-point group.
   An adapter accepts a `Context` and returns `Step` declarations. Constructing
   a plan must not perform network requests, downloads, or filesystem writes.
2. Declare its name, provider, description, cadence, and owned metadata paths
   in `config/targets.json`. Declare worker capabilities such as `java` or
   `apkeep` when required. Targets that share mutable metadata use the same
   `lock`. Public protocol roots and product identities belong in the adapter;
   discovered release URLs belong in its observations.
3. Add offline fixtures that exercise discovery from an empty state, failed
   acquisition, and retry after partial publication. Plan it with
   `sonos-archive run TARGET --plan`. No binary inputs or precomputed hashes
   are required to register, plan, or schedule a target.

The workflow creates its matrix from these declarations. A new target does
not require a workflow, release URL list, or local archive. Source-specific
parsing and validation remain in their adapters; scheduling, temporary storage,
reporting, and metadata commits use the shared runner.

For installer sources, `sonos_firmware.app_archive.AppSource` supplies a shared
acquisition loop. Its `acquire(candidate, destination)` callback streams one
payload into worker storage and returns its fetch URL. `release_for(candidate)`
returns a release tag and title. The common loop computes size and SHA-256,
checks any discovery checksum, publishes without overwriting, and reconciles
GitHub's size and digest before adding a preserved artifact. Source adapters
do not need their own hashing, retry ledger, or publication implementation.

## Worker lifecycle

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
the next hourly tick; successful targets follow their declared cadence. Targets
with a common metadata lock execute in order. Independent sources can proceed
when another target fails. Android history uses a bounded, rotating queue and
retries failures rather than permanently suppressing them.

Desktop and official APK acquisition share per-repository URL observations.
Current download URLs refresh at the target cadence; historical URLs refresh
weekly by default. Changed checksums, ETags, versions, or HTTP availability
become eligible immediately. Attempts include failures and are bounded; prior
leads remain available when a new discovery report omits them. An unavailable
or partial discovery report remains explicit even when earlier captures can
finish publication.

Captured content is staged as unpublished metadata before upload. If an upload
response is lost, a later worker can reconcile the exact recorded asset without
re-fetching its original URL. Changed bytes at one URL produce separate assets.
Older unresolved capture receipts remain visible and are retried independently;
they never become preserved solely because a newer capture succeeded. Metadata
checkpoints use the same atomic JSON writer across discovery, app acquisition,
GPL acquisition, and orchestration.

`google-play` is manual and requires both configured credentials and explicit
terms acceptance. Missing capabilities produce a blocked report. Apple metadata
and authenticated delivery are distinct scopes.

## Validation and publication

Local plans and tests do not acquire live artifacts. `sonos-archive run TARGET`
is a plan unless `--execute` is supplied, and execution rejects an environment
outside GitHub Actions before starting commands or creating files.
Run `python scripts/run_tests.py` for the offline fixture suite. Its guard
rejects unmocked Python sockets, native HTTP requests, and external network
tools; an attempted request fails the run even if source code catches it.
Child Python tests inherit the guard, and Git fixtures use only file transport.

The shared metadata commit command validates JSON and the firmware catalog,
then stages only the target's declared data paths and its checkpoint. It refuses
to include pre-staged unrelated changes. Firmware extraction restores the
catalog checkpoint from immediately before extraction if publication fails,
retaining earlier successfully published source records. New raw receipts are
installed only after upload verification.

Use the Actions run report to inspect errors and remaining work. Bounded
completion is not proof that all historical releases have been discovered;
coverage records must retain unresolved gaps and source failures.

The workflow uses GitHub's documented [`queue: max` concurrency option](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax#concurrency)
so waiting jobs sharing a metadata lock are retained. Older local versions of
`actionlint` may not recognize this option.
