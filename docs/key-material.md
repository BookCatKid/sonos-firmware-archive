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

As of 2026-09-02, the full archived UPD catalog contains 399 encrypted
packages using 33 distinct RSA recipients. The two locally recovered keys cover
32 of those packages exactly. The remaining 31 recipient identities and their
affected package IDs are tracked in `data/key-recovery-ledger.json`. No claim is
made that a single firmware-specific key can decrypt other Sonos models.

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

## Where the missing keys may come from

Update 2026-09-03: each encrypted envelope names an RSA-2048 recipient, so
decryption requires the matching private key. The public `sonostool` source
(`blasty/sonos`) validates one concrete recovery layout for its hardcoded
`SONOS_MODEL = 26` target: a 16 KiB `MDP3` region with an RSA blob at offset
`0x0780`, encrypted under an OTP/eFUSE-derived AES key
(`SHA256(OTP[0xD0:0xE0])[:16]`). That layout is not evidence that every Sonos
package model stores its key the same way. No published private keys were
found for any missing recipient (Fenway repo is model-8 only;
`Sonoisseurs/sonor` covers S18-One/ZP120 research with no key dumps;
`blasty/sonos` ships tooling, not keys; `systemcrash/sonos-firmware` ships
packages/manifests only). Known public access techniques are useful leads,
not interchangeable key-recovery recipes: Fenway/MPC8314 UART plus diagnostic
firmware and `mdputil`; Amlogic-era EL3 OTP dumping plus `sonostool` (HITB
Amsterdam 2023); Sonos One Gen 2 PCIe DMA root shell (Synacktiv, 2021); and
Sonos One / Era 100 OTA and secure-boot research (NCC Group, BlackHat USA
2024). Every hardware generation still needs model-specific validation.

Priority order by blocked-package count: model 13 (Play:5 Gen 2, 17
packages, `completeness.json` target), then models 23, 24, 21, 29, 28, 26
(12–19 packages each). Model 25 (Move, 17 packages) is moot until the
`96.0-79160` package itself is recovered. Dumps contain device secrets and
must be supplied out-of-band; only recipient fingerprints and component
hashes enter the archive. Note the two recovered model-8/9 private keys are
absent from fresh checkouts by design (`keys/` never entered git), so even
model-8/9 re-extraction here needs the keys re-supplied.
