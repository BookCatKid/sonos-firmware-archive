# ConnectX firmware preservation

[Issue #24](https://github.com/BookCatKid/sonos-firmware-archive/issues/24)
identified the official mutable distribution URL
`https://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd`.
It serves package-model 17 firmware containing the Anacapa application and other
sections. The same URL can return different contents over time.

## Discovery and preservation

The normal `firmware` target declares this URL in `artifact_roots` in
[`config/targets.json`](../config/targets.json). Its worker also walks Sonos
protocol manifests and historical indexes for versioned model-17 packages.
The static URL is a protocol root, not a fixed version or precomputed checksum.

Each capture is hashed in Actions and published before it is marked preserved.
Embedded versions supply identity when the source advertises none. Distinct
contents at the same URL retain distinct immutable assets; existing catalog
identities remain intact.

There is no separate ConnectX acquisition workflow or committed historical URL
list. External collections can help explain discovery algorithms, but the
runtime must find its own leads from protocols and public indexes.

## Extraction

ConnectX uses the same extraction and inventory queues as other UPDs. Workers
retain every available section and supported filesystem member, including
`bin/anacapad`, rather than extracting just that executable. Unknown or encrypted
sections remain attributable to spans of the exact preserved source.

Verified extraction bundles and full inventories go to GitHub Releases. Catalog
references and generated queue state record progress under `data/catalog.json`
and `data/discovery/`. Those records are created by Actions, not supplied as
prerequisites to deployment. Failed decoders and publication attempts remain
retryable; new keys and decoder revisions revisit older captures.

## Keys and historical coverage

Model-17 legacy key recovery follows the shared updater-recovery path described
in [key material](key-material.md). A model number alone does not establish the
key for every encrypted section; the worker verifies the exact recipient.
Plaintext extraction continues when another section has no matching key.

The static URL is not a historical version index. A manifest reference or inferred
filename is a discovery lead, and an unavailable URL does not establish possession
of its contents. Current coverage comes from verified catalog receipts and the
worker's pending queues, not dated research totals. See the
[platform guide](archive-platform.md) for scheduling and retry behavior.
