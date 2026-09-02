# Model-key handling

This archive records two locally verified Sonos model-key identities used to
decrypt historical firmware containers:

| Package family | Recipient fingerprint | Coverage in this archive |
|---|---|---|
| Model 8 / Play:1 family | `346ce6e38225ca8177024fbebfaad7043344b3bd` | Archived model-8 packages |
| Model 9 / Playbar family | `12e82a182af27801eba0ff3c94e8e649ed962dbb` | Archived model-9 packages |

The fingerprints are SHA-1 values of the public-key representation carried in
the package envelope. They allow an independent reviewer to identify which
key family was used without receiving secret material.

## Provenance and verification

The model-9 key was recovered during local research from the model-9 updater
chain. The model-8 key was independently reconstructed from the legacy
model-8 updater's encrypted key wrapper, then validated by matching its public
key fingerprint to model-8 OTA envelopes and by reproducing the published raw
component hashes for `86.8-78270-1-8`.

For every extracted component, the repository stores its source package,
envelope recipient fingerprint, byte size, and SHA-256 hash in `data/raw/`
and `data/catalog.json`. The raw component is hosted beside its encrypted OTA
in the corresponding private GitHub Release.

## Withheld material

Private keys, key wrappers, passwords, device data, and derivation inputs are
not included in this repository or in its Releases. They are reusable security
credentials for an entire package-model family, rather than archive metadata.
Their fingerprints, extraction receipts, source-package hashes, and raw-image
hashes are sufficient to audit the archive's claims without redistributing the
credentials themselves.
