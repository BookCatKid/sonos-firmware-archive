# Model 20 firmware-key recovery

## Conclusion

Package model 20 is Sonos platform `Royale`, used by the first-generation IKEA
SYMFONISK family. The supported recovery path is an authorized, read-only
unwrap on the same Royale unit that supplies its MDP3 model-key blob. Royale
uses the i.MX6 SoloX CAAM design also documented for model 13: the model RSA
private key is CAAM-wrapped under a device-bound hardware key and cannot be
recovered from an MDP copy alone.

One successfully unwrapped RSA-2048 key should cover all 17 preserved model-20
packages carrying recipient
`8cb7e8b66c9984623bce23125ef6a0c88b1d4cc3`. No such key is claimed yet.
This note documents an offline and read-only recovery target; it does not
provide a device-write or exploitation workflow.

## Package and platform evidence

Sonos's published `mdp.h` assigns numeric model 20 to
`MDP_MODEL_ROYALE`. The exact plaintext-era package
[`34.16-37101-1-20`](../data/upd/34.16-37101-1-20.json) has SHA-256
`a43eafc4bf71ce13d8219f272226e9dc097ce7360d79003c23c483d954acde57`.
Its preinstall and rootfs sections are encrypted to recipient
`8cb7e8b66c9984623bce23125ef6a0c88b1d4cc3`, while its kernel and section 15
bootloader image are plaintext.

The package's signed kernel container starts at offset `0x16c` within section
6. Removing that container header yields a valid 3,308,316-byte legacy uImage:

- SHA-256:
  `229643404dee12a2f57b34c00e499a252524e0d9f1748d5dec67514e0efcf90d`;
- build: Linux 3.10.31, ARM, uncompressed;
- load and entry address: `0x80008000`; and
- device-tree identity: `Sonos i.MX6 SoloX Royale`.

The plaintext section 15 has SHA-256
`eb2989bf3bb74322a6763273740ceae035f62143e46ac61dceccaa0743c9da06`
and identifies `U-Boot 2014.04.58-Royale-Strict-Rev1.1`, built for `Sonos
Royale`. Its CAAM, MDP3, fuse-status, and OTPMK validation strings independently
corroborate the secure-production platform.

## MDP3 and CAAM construction

The retained official Sonos Linux 3.10.53 tree contains a Royale-specific
configuration at `arch/arm/configs/royale_defconfig`. It sets
`CONFIG_SONOS_ROYALE=y`, `CONFIG_SONOS_SECBOOT=y`, and
`CONFIG_SONOS_CAAMKEYS=m`. The same tree maps Royale to `MDP_MODEL_ROYALE` in
`arch/arm/kernel/setup.c`, validates MDP3 into the exported `sys_mdp3`
structure, and implements CAAM secure-memory blob decapsulation in
`kernel/sonos_secure.c` and `drivers/crypto/caam/sonos_sm_access.c`.

The model-key field and unwrap contract are therefore the same documented
layout used by Encore/model 13:

| Item | Value |
|---|---|
| Complete `smdp` MDP3 offset | `0x1200` |
| Model-key field offset in MDP3 | `0x0780` |
| Model-key field size | `0x0800` |
| Required header magic | `0xF4CCC68D` |
| CAAM operation | decrypt, red-key color, CCM cover |
| Key modifier | ASCII `model` in an eight-byte zero-padded field |
| Plaintext size | wrapped blob length minus 48 bytes |

The underlying API is `sonos_key_encdec()` from
`include/linux/sonos_kernel.h`. The exact bounds checks and call contract are
recorded in [`model13-recovery.md`](model13-recovery.md). A Royale recovery
helper must additionally require MDP model 20 and must accept output only if it
parses as RSA-2048 and produces the expected recipient fingerprint.

Because CAAM secure-production blobs are rooted in the individual SoC's
unreadable OTP master key, the blob from unit A must be decapsulated on unit A.
The resulting RSA key is expected to be model-wide, but that expectation is
not proof: the exact recipient check remains mandatory.

## Existing public access research

The [Depthcharge project](https://github.com/tetrelsec/depthcharge)
includes
[`python/examples/symfonisk_unlock_bypass.py`](https://github.com/tetrelsec/depthcharge/blob/f8591f23cb7b216c321bc0405774ad460393bfd9/python/examples/symfonisk_unlock_bypass.py),
a published case study for the
older Royale Rev0.2 U-Boot. It demonstrates that an overlooked I2C command on
that already-patched bootloader could expose a fallback shell. The source is
useful evidence that authorized research access has been achieved on Royale,
but its address-specific patches apply only to Rev0.2. They do **not** apply to
the preserved Rev1.1 bootloader and are not part of this archive's recovery
procedure.

An exact source snapshot at Depthcharge commit
`f8591f23cb7b216c321bc0405774ad460393bfd9` is retained in the ignored recovery
vault. The archive SHA-256 is
`9a9156a7c1c13faacc18f16f02cb9dd7d07fcb018e208bc61669823aa38ae0e8`.

The older unlocked command set names `dek_blob`, but that name is misleading
for this recovery target. The public Freescale/NXP implementation accepts a
plaintext DEK and invokes only CAAM **encapsulation**. Its companion CAAM
library contains a separate `blob_decap()` function, but `dek_blob` does not
expose it. The exact preserved Royale Rev1.1 image also contains no
`dek_blob`, `encapsulate`, or `decapsulate` command string. Consequently,
neither the public Rev0.2 command nor the retained Rev1.1 command table offers
a read-only model-key unwrap primitive; the Linux helper path remains
necessary. The two public reference source files and their exact commit are
retained in the ignored recovery vault.

## Safe completion criteria

On an owned Royale unit with pre-existing authorized administrative access:

1. Read and hash a backup without modifying NAND, MDP, fuses, U-Boot state, or
   boot arguments.
2. Validate an MDP3-sized or complete `smdp` capture with
   `sonos-fw mdp-inspect`; require model 20 and the CAAM model-key header.
3. Use a helper built against the exact running kernel ABI to ask that same
   unit's CAAM to decapsulate only the model-key blob with modifier `model`.
4. Store the one-shot result in a newly created mode-0600 file, erase temporary
   plaintext buffers, and reboot to clear recovery state.
5. Run `sonos-fw key-id` and require exactly
   `8cb7e8b66c9984623bce23125ef6a0c88b1d4cc3`.
6. Test one preserved model-20 package with `sonos-fw extract`, retain its
   hash-only receipt, and only then batch-decrypt the other 16 packages sharing
   that recipient.

Until the CAAM call succeeds, the RSA output validates, the fingerprint
matches, and at least one package decrypts, model 20 remains a documented but
blocked recovery target.
