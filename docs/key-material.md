# Model-key handling

This archive records five locally verified Sonos model-key identities used to
decrypt historical firmware containers:

| Package family | Recipient fingerprint | Coverage in this archive |
|---|---|---|
| Model 8 / Play:1 family | `346ce6e38225ca8177024fbebfaad7043344b3bd` | Archived model-8 packages |
| Model 9 / Playbar family | `12e82a182af27801eba0ff3c94e8e649ed962dbb` | Archived model-9 packages |
| Model 12 legacy family | `e35f7c21c0ec00a768bfbd364a9a105cf300f023` | All 16 preserved encrypted model-12 packages |
| Model 16 legacy family | `fd88f2642a9c89a44747c7b4342cdb483fe1d437` | All four preserved encrypted model-16 packages |
| Model 17 legacy family | `f2acc70d8db3ee388c534d99d4bff64d9dab8154` | All 17 preserved encrypted model-17 packages |

The fingerprints are SHA-1 values of the public-key representation carried in
the package envelope. They allow an independent reviewer to identify which
key family was used.

As of 2026-09-02, the full archived UPD catalog contains 399 encrypted
packages using 33 distinct RSA recipients. The five locally recovered keys cover
69 of those packages exactly. The remaining 28 recipient identities and their
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
The same recipient ID is stable across all 14 archived model-9 versions.

### Model 12

Model 12 uses the same legacy cryptographic construction, but its wrapper is
not adjacent to the updater log-message marker. MIPS disassembly identifies a
1,264-byte wrapper at file offset `0x19300`. An exhaustive bounded search of
all 65,536 possible 16-bit system words found exactly one valid RSA key:
system word `0x0000`, recipient
`e35f7c21c0ec00a768bfbd364a9a105cf300f023`. The recipient matches all 16
preserved encrypted model-12 envelopes, and the key decrypted all 48 payload
components. The pre-upload audit found none of those exact plaintext
filenames in their releases; server sizes and SHA-256 values were reconciled
after upload. See
[`data/decryption-runs/2026-09-09-new-model12.json`](../data/decryption-runs/2026-09-09-new-model12.json).

### Models 16 and 17

The plaintext `34.16-37101` packages for models 16 and 17 contain PowerPC
`/bin/upgrade` binaries with 1,264-byte encrypted private-key wrappers. Static
analysis established that both updaters construct the same deterministic
32-byte entropy input from fixed MDP/RSAref fields, the package-model number,
and system word `0x1996`. They feed that input to Mbed TLS AES-256 CTR-DRBG,
use its first two blocks as an AES-CBC IV and key, and synthesize a 31-byte
password for a PKCS#12 SHA-1/RC4 encrypted PKCS#8 object.

The checked-in implementation independently reproduces that construction. It
recovers RSA-2048 keys whose fingerprints are
`fd88f2642a9c89a44747c7b4342cdb483fe1d437` for model 16 and
`f2acc70d8db3ee388c534d99d4bff64d9dab8154` for model 17. Those are exact
matches for their encrypted OTA envelopes. The keys successfully decrypted
all 21 preserved packages for the two recipients, yielding 63 components;
their hashes and publication results are recorded in
[`data/decryption-runs/2026-09-08-new-model16-model17.json`](../data/decryption-runs/2026-09-08-new-model16-model17.json).

This recovery is reproducible from an authorized copy of either plaintext
updater without placing private key material in the repository:

```bash
sonos-fw recover-legacy-updater-key /path/to/upgrade \
  --model 16 \
  --expect-recipient fd88f2642a9c89a44747c7b4342cdb483fe1d437 \
  --output /secure/model16-private.pem
```

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
`trulyspinach/sonor` includes public Sub Gen1 MTD dumps, but the published
sample contains only MDP1 and no MDP3/model-private-key field;
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
hashes enter the archive. Note the five recovered private keys are absent from
fresh checkouts by design (the local `recovery-work/` vault never entered
git), so re-extraction still needs the appropriate key re-supplied or locally
recovered from an authorized plaintext updater.

For the next concrete target, model 13 / Encore (Play:5 Gen 2), the exact
kernel and Sonos GPL source establish a different path: its MDP3 model key is
a CAAM-wrapped red blob, so a matching authorized i.MX6 unit must perform the
unwrap. The storage layout, exported `sonos_key_encdec()` call, validation
fingerprint, and a non-destructive workflow are documented in
[`docs/model13-recovery.md`](model13-recovery.md). Do not apply the Amlogic
OTP formula to Encore.

For the two Amlogic A113 families now backed by model-specific evidence,
[`docs/model26-recovery.md`](model26-recovery.md) records the publicly proven
Tupelo/model-26 path, while
[`docs/model25-recovery.md`](model25-recovery.md) records Dhuez/model-25 as a
hardware-validation candidate. The model-25 route is deliberately not labeled
proven until a matching authorized MDP/OTP capture yields the exact package
recipient and successfully decrypts a preserved UPD.

A retained diagnostic-JFFS file named `private.key` was also audited as a
possible lead. Its fixed-width RSAref structure validates as an RSA-1024
private key, while every encrypted UPD envelope here targets an RSA-2048 model
key. Its public fingerprint matches neither a recovered nor a missing UPD
recipient, so it is a device/application credential and is not imported or
used for firmware decryption.
