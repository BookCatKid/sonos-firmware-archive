# Direct Sonos GPL/LGPL source preservation

The scheduled `gpl`, `gpl-archive`, and `gpl-audit` targets discover source indexes,
preserve their linked archives, and verify published bytes. A fresh destination
needs neither imported version lists nor existing Releases.

## Discovery

The `gpl` target gathers version leads from Sonos sites and sitemaps, existing
observations, and literal GPL paths in verified firmware/application contents.
It also explores numeric major/minor pairs with a saved, expanding ordinal.
Version labels are strings: `14.12` and `14.2` remain different.

Each candidate index must have the expected Sonos GPL HTML and links. A 200
challenge page is not an index, and a 403 response does not prove that a version
never existed. Response hashes, provenance, pending leads, and retry deadlines
are retained in `data/gpl/live/`.

Binary inspection records the asset hash, member, and byte offset of each GPL
path. Resource limits and incomplete decoding remain explicit. A path is a lead;
independent retrieval and validation establish the index.

## Acquisition

`gpl-archive` resumes downloads from validated indexes using the original linked
URLs. It computes hashes in Actions, verifies publication, and records the
original URL, encoded fetch URL, size, SHA-256, release asset, and publication
state. Invalid archives are recorded with their source evidence rather than
marked preserved.

Transient failures and server Retry-After responses retain durable cooldowns.
New and failed sources share worker capacity; a failed source does not need a
manual retry flag to remain eligible. Batches checkpoint progress as they run.

## Verification and provenance

`gpl-audit` revalidates published compressed streams against original source
bytes and checks the advertised-link ledger. It processes successive batches
within its time budget; pending verification continues in later scheduled runs.
The `gpl-indexes` maintenance target can explicitly refresh known index bodies,
but is not a prerequisite for discovery or acquisition.

Legacy Wayback captures retain their own provenance under
`data/gpl/wayback-catalog.json`. They are not relabeled as direct Sonos downloads.
Historical comparisons remain in the existing archive records and Git history.
`data/gpl/seed-versions.json` is historical evidence, not a runtime input.

Matching a component's upstream version does not establish complete
corresponding source for a particular firmware binary. See the
[18.6 correspondence investigation](gpl-source-correspondence-18.6.md) for a
concrete analysis and its limits.

Use the [platform guide](archive-platform.md) for scheduling and target options,
and [maintenance tools](maintenance-tools.md) for repair commands. Current
coverage belongs in generated receipts and reports, not a manually maintained
list of completed versions.
