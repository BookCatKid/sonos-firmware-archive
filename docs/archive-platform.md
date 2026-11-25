# Archive platform

[The scheduled workflow](../.github/workflows/archive.yml) runs targets declared
in [`config/targets.json`](../config/targets.json). Git holds observations and
publication receipts; GitHub Releases holds payloads and extraction bundles.

## Adding a target

For another source handled by an existing provider, add its target declaration
and provider options in `config/targets.json`. For a new source type:

1. Implement an adapter in
   [`platform/providers.py`](../src/sonos_firmware/platform/providers.py), or use
   the `sonos_archive.providers` entry-point group. An adapter accepts a `Context`
   and returns `Step` declarations. Planning performs no acquisition.
2. Declare its provider, description, `interval_hours`, and owned `metadata`
   paths. Targets writing shared state use the same `lock`. Declare required
   and optional capabilities such as `java`, `apkeep`, and `goopdl`.
3. Inspect `sonos-archive run TARGET --plan`. Exercise the adapter with offline
   fixtures for the behavior it introduces.

The workflow builds its matrix from those declarations. Public protocol roots
and product identities are configuration; discovered release versions, URLs,
and hashes are generated observations. No local payload collection is needed.
Script entrypoints are organized by purpose under [`scripts/`](../scripts/README.md).

## Starting with no archive

Install the project and plan a target:

```bash
pip install -e '.[test]'
sonos-archive targets
sonos-archive run firmware --plan
```

Prior `data/` records, Git history, cached observations, private keys, and Releases
are optional. Workers initialize their own state and create destination Releases
when needed. Public model-26 recovery inputs are tracked under
[`data/key-material/model26/`](../data/key-material/model26/README.md).

Execution uses `--execute` inside GitHub Actions. To request it, select
**discover and archive** in Actions and choose a target or `all`. Normal scheduled
runs continue discovery and pending work automatically. A new source or worker
change on `main` starts the automatic targets immediately.

## Scheduling and retries

The hourly workflow selects due targets. Successful targets follow their cadence;
incomplete targets become due again within an hour. Shared locks serialize
metadata writers while independent targets proceed. `gpl-indexes` is an explicit
maintenance target; normal GPL discovery does not require running it first.

Each target writes outcomes, errors, pending counts, and its next due time under
`data/platform/targets/`. The workflow retains its run report and metadata
checkpoint even when a step or metadata commit fails.

Queues retain cursors, unattempted leads, retry deadlines, and a rotation between
new and failed work. Per-run budgets control batch size, not lifetime discovery
coverage. HTTP throttling honors Retry-After across runs; a paused host does not
pause unrelated sources. Changed recovery methods, inspectors, or source hashes
can make older work eligible again.

Provider options can be set in the target's `options`, or overridden for a plan:

```bash
sonos-archive run firmware --plan --set limit=8 --set index_pages=2
```

See [`providers.py`](../src/sonos_firmware/platform/providers.py) for the options
each provider accepts. Credentials belong in the worker environment, not options.

## Discovery sources

| Category | Discovery |
| --- | --- |
| Firmware | Public UPS protocols, manifest links and redirects, observed namespaces, release-note hints, and historical indexes |
| Desktop | Official redirects, WinGet and Homebrew metadata/history, firmware observations, and historical indexes |
| Official Android | Sonos-hosted APK redirects and historical captures |
| Android stores | APKPure asset/version/SDK metadata and apkeep selectors; anonymous Play device/profile discovery with goopdl acquisition |
| Web | Public controller/Pro deployments and referenced scripts, styles, and assets |
| Apple | Public listing metadata and changes; this does not acquire IPA binaries |
| GPL | Sonos sites and sitemaps, paths found in verified binaries, and expanding numeric version exploration |

Wayback and Common Crawl searches cover Sonos domains and directories learned
from our own observations. They retain original capture URLs, query variants,
and provider-specific continuation state. When broader Wayback queries fail,
exact queries over our own observed URLs retain a separate continuation and retry
state. Index requests allow 45 seconds for slower successful queries. Common Crawl collections come from
its public registry; replay uses recorded WARC/ARC byte ranges. Malformed pages
retain valid leads and remain retryable.

Manifest traversal follows native links and resolves relative images against
the original vendor URL. Historical manifests and direct payload captures enter
the same queues as live discoveries. Namespace model ranges expand after each
completed pass; synthetic protocol profiles advance a saved exploration cursor.
WinGet history follows changed and parent snapshots, including removed metadata.
The firmware worker also replays its own hash-verified preserved XML snapshots,
so a changed or dead origin cannot hide links in an older revision. Parsed
payload leads queue sibling manifests even when the payload is unavailable.
Staging/test manifests also supply payload leads on the host that served them.
Synthetic profiles are explored against both manifest generations as well as UPS.
First-party payload URLs in other targets' declared metadata feed firmware
discovery too, including app-worker discoveries that are not yet downloaded.

External firmware collections are research references, not runtime release lists.
See [discovery methods](sonos-discovery-methods-research.md) for the protocol
research. An upstream outage or opaque historical path can still leave gaps;
queue completion is not proof that every past release has been found.

## Acquisition and publication

Workers download payloads into temporary storage, calculate size and SHA-256,
and verify discovery checksums when supplied. A successful HEAD request is a
lead, not a preservation receipt. Sources are published independently of
extraction, so an unsupported decoder cannot block preservation of the UPD.

Shared publishers verify the remote asset before recording it as preserved.
They retain distinct contents at mutable URLs and preserve separate origins.
Existing exact uploads can finish interrupted publication without downloading
again. Full Releases continue in numbered partitions; conflicting assets are
not overwritten.

Installer providers share [`AppSource`](../src/sonos_firmware/app_archive.py):
`acquire` fetches a candidate, `release_for` selects its destination, and optional
`inspect` validates its contents. `inspection_revision` schedules reinspection
when validation changes. Android inspection records actual package/version,
splits, and signers; Play deliveries must match the configured signer pins.
Missing optional tools defer affected acquisition while discovery continues.

Only a target's declared metadata paths and checkpoint are committed. Payloads
and private keys stay out of Git and out of the metadata cache. Temporary worker
storage is removed on completion or failure.

## Extraction and key recovery

All preserved UPDs share extraction and inventory queues. Workers inspect all
available sections, supported filesystems, tar members, and kernel streams
without executing firmware. Encrypted or undecodable sections retain their
source spans and pending status. Full inventories and bundles are published to
Releases; Git keeps verified references rather than duplicate file listings.

`inventory_limit` bounds a worker batch. `inventory_limits` controls expanded
bytes, file count, depth, and decoder timeout. Changed source bytes, newly
available keys, and decoder revisions make older packages eligible again.

Key preparation examines verified updater binaries, other plaintext or decrypted
components, and decoded inventory members. Supported methods include legacy
wrapped-key derivation and unencrypted PEM/DER key scanning. Bundle and component
proofs must bind to the parent UPD. Only exact recipient matches are accepted;
public ledgers record provenance and fingerprints, never private key bytes.

Keys remain ephemeral. Optional configured keys use `SONOS_PRIVATE_KEYS`
(recipient-to-PEM JSON) or the workflow's per-model secrets. See
[key material](key-material.md), [model-26 recovery](model26-recovery.md), and
[ConnectX preservation](connectx-anacapad.md) for technical evidence.

## Reconciliation and maintenance

`release-audit` compares recorded assets with GitHub's paginated release metadata.
It also recovers interrupted source-document receipts when verified bytes and a
matching origin establish provenance. Origins can come from any target's owned
JSON observations or this repository's own optional metadata history. Unknown
origins stay unresolved; verified non-Sonos publications remain outside the
Sonos catalog.

`gpl-audit` verifies published compressed streams against original source bytes,
checkpointing successive batches within its time budget. Invalid archives and
source failures retain their evidence and retry state. See the
[GPL guide](gpl-direct-archive.md) and [maintenance tools](maintenance-tools.md).

Run `python scripts/run_tests.py` for existing offline checks. Its network guard
also covers child Python processes. Once workers have generated a catalog,
`sonos-fw verify` checks its consistency without acquiring payloads.
