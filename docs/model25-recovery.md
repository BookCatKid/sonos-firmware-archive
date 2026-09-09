# Model 25 / Dhuez key-recovery evidence

## Conclusion

Model 25 is a strong candidate for the same **offline Amlogic MDP3 plus OTP
unwrap** used by the public model-26 tooling, but that extension has not yet
been validated on Dhuez hardware. Sonos's GPL source maps numeric model 25 to
Dhuez and compiles its product support for the Amlogic A113 platform. The
archive has a complete encrypted model-25 package and a recipient fingerprint,
so a capture from an authorized Move can be tested without guessing the
success condition.

No model-25 key, MDP3 page, or OTP dump was found locally or in the public
sources checked. The current status remains `blocked-model-key`.

## Target identity

The local Sonos GPL header
[`mdp.h`](../sonos-research/gpl-10.6/includes/includes1/mdp.h) defines
`MDP_MODEL_DHUEZ` as 25 and `MDP_SUBMODEL_DHUEZ` as 1. The same source tree's
[`amperctl_drv.c`](../sonos-research/gpl-10.6/drivers/amperctl/amperctl_drv.c)
places Dhuez-specific amplifier support under
`SONOS_ARCH_ATTR_SOC_IS_A113`. This establishes the model number and Amlogic
A113 architecture directly from Sonos-published source.

All 17 preserved model-25 packages use recipient fingerprint:

```text
83fb98ffd324255b26fdab99105a1a02a82bae93
```

The retained [`96.1-79270-1-25` manifest](../data/upd/96.1-79270-1-25.json)
records a 100,409,662-byte package with SHA-256
`b6e1b93674098569c0e901fb74ac27eb28c61edfbaee6fab14de5175d7052b67`.
Its preinstall, kernel, rootfs, and section-15 payloads are all encrypted for
that recipient. The package itself is preserved in the
[`firmware-96.1-79270` release](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/firmware-96.1-79270).

## What is established and what is inferred

The MDP3 storage layout is shared in Sonos's published `mdp.h`: a 0x4000-byte
MDP3 page contains 256-byte JFFS and rootfs key fields followed by a 2048-byte
model-private-key field at offset `0x780`. The archive's read-only
[`mdp-inspect`](../src/sonos_firmware/mdp.py) implementation checks that layout
and reports the MDP1 model identity without printing secret bytes.

The public [`blasty/sonos` implementation](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py)
proves the offline unwrap for Amlogic model 26: it derives the AES key as
`SHA256(OTP[0xd0:0xe0])[:16]`, unwraps the field at MDP3 offset `0x780` with
the `model` modifier, and parses the result as an RSA private key. Its
`SONOS_MODEL = 26` constant is important: the public code does not claim a
model-25 test.

Applying that construction to model 25 is therefore an evidence-backed test,
not yet a fact. The test is justified by the Sonos source placing Dhuez on the
same A113 architecture and using the same MDP3 structure. It becomes a
confirmed recovery method only if the result is a valid RSA-2048 key whose
public fingerprint exactly matches the package recipient above.

## Non-destructive validation workflow

1. Obtain a read-only capture from an owned, authorized Move: either a complete
   0x5200-byte SMDP or a larger device dump containing it, plus the matching
   0x100-byte Amlogic OTP dump. Keep both outside this repository.
2. Inspect the manufacturing pages:

   ```sh
   sonos-fw mdp-inspect /secure/model25-smdp.bin
   ```

   Require `model: 25`, `submodel: 1`, one complete MDP3 page, and a valid
   model-private field. Stop on any mismatch.
3. Attempt the offline unwrap with both model and recipient checks enabled:

   ```sh
   sonos-fw recover-amlogic-mdp-key /secure/model25-smdp.bin \
     --otp /secure/model25-otp.bin \
     --output /secure/model25-private.pem \
     --expect-model 25 \
     --expect-recipient 83fb98ffd324255b26fdab99105a1a02a82bae93
   ```

   The command refuses to replace an existing output and creates a successful
   key with mode 0600. It writes nothing if model validation, DER parsing,
   RSA-2048 validation, or recipient validation fails.
4. Test the recovered key against one preserved package in a disposable output
   directory and retain only hashes and a redacted receipt:

   ```sh
   sonos-fw extract 96.1-79270-1-25.upd \
     --directory model25-extracted \
     --private-key /secure/model25-private.pem \
     --receipt model25-extraction.json
   ```

5. Treat successful decryption of all four encrypted payloads as the hardware
   proof. If it succeeds, the same key can be applied only to packages carrying
   the identical recipient fingerprint.

This workflow does not write flash, fuses, bootloader state, or manufacturing
pages. Obtaining the two device captures remains a separate authorized hardware
task; this repository intentionally contains no exploit or device-write step.
