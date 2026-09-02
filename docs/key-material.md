# Model-key handling

This archive records two locally verified Sonos model-key identities used to
decrypt historical firmware containers:

| Package family | Recipient fingerprint | Coverage in this archive |
|---|---|---|
| Model 8 / Play:1 family | `346ce6e38225ca8177024fbebfaad7043344b3bd` | Archived model-8 packages |
| Model 9 / Playbar family | `12e82a182af27801eba0ff3c94e8e649ed962dbb` | Archived model-9 packages |

The fingerprints are SHA-1 values of the public-key representation carried in
the package envelope. They allow an independent reviewer to identify which
key family was used.

## How the key identities were recovered

### Model 8 / Play:1

The starting artifact was the public historical Fenway/Play:1 root filesystem
for `34.16-37101`, specifically its PowerPC `/bin/upgrade` program. That
updater contains an encrypted PKCS#8 model-key wrapper. Local reverse
engineering of its updater path established the legacy model-8 wrapper format
and reproduced the updater's deterministic decryptor inputs from the updater's
own manufacturing-structure constants and documented system-word behavior.

Decrypting the wrapper produced an RSA-2048 private key. Its public-key
fingerprint is `346ce6e38225ca8177024fbebfaad7043344b3bd`, exactly matching the
recipient ID in every encrypted archived model-8 OTA. The result was validated
against the independently retained `86.8-78270-1-8` package: decrypting its
preinstall, kernel, and rootfs reproduced these known SHA-256 values:

| Component | SHA-256 |
|---|---|
| Preinstall | `feddb7ad14034b06c316ccbed244a11925093a882a9d7bd81ea64a61364cbe70` |
| Kernel | `e7e92b4d79f2a3bfef22c165b2bacdda226fb72a31039d77145d06e308fe9254` |
| Rootfs | `f6fdba2289d1d99cf622c5dd83f6907d749c7edb874a5213bde6d5d3a54c0b54` |

That same recipient ID remains stable across all 14 archived encrypted
model-8 packages, which is why the resulting key can decrypt the historical
model-8 batch.

### Model 9 / Playbar

The starting artifact was the public historical Playbar `34.16-37101` updater
chain. Its updater and model-specific support material expose an encrypted
model-key wrapper. Local analysis recovered the associated wrapper, public-key
material, and password input, then decrypted the wrapper into an RSA-2048 key.

The recovered key's public-key fingerprint is
`12e82a182af27801eba0ff3c94e8e649ed962dbb`, exactly matching every encrypted
archived model-9 OTA envelope. It was validated first on the model-9
`34.16-37101` package and then on `86.8-78270-1-9`; the latter produced the
cataloged Playbar kernel, rootfs, preinstall script, and device-payload hashes.
The same recipient ID is stable across all 12 archived model-9 versions.

## Archive evidence

For every extracted component, the repository stores its source package,
envelope recipient fingerprint, byte size, and SHA-256 hash in `data/raw/`
and `data/catalog.json`. The raw component is hosted beside its encrypted OTA
in the corresponding private GitHub Release.
