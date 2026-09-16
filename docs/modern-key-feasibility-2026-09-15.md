# Feasibility of decrypting the 97.1 modern speaker packages

Status: **no 97.1 model-private key recovered** as of 2026-09-15. This note
separates package facts, an independently implemented and model-specific
recovery construction, and steps that still require material from an owned,
authorized speaker. It does not treat a public recipe as possession of a key.

## Why this blocks the requested 97.1 versus 96.1 code diff

The archive retains 23 model-specific `97.1-80312` packages. Their kernel and
rootfs payload sections are encrypted; for example, the [model-26 UPD
manifest](../data/upd/97.1-80312-1-26.json) records the RSA recipient
`b271039ff7ab0f3d395039f1f079ff71af9dea71` on preinstall, kernel,
rootfs, and section 15. The plaintext `application` section there is just a
364-byte descriptor, not an executable image. The other package manifests
under [`data/upd/`](../data/upd/) provide the equivalent section-level facts.

There are 23 distinct encrypted recipients across the 23 `97.1` model
packages. A local same-model comparison of their manifests to
`96.1-79270` found an **identical recipient for 22 models**: 21, 23–30,
32–38, 40–42, 46, 49, and 51. Model 52 has no archived `96.1-79270`
same-model package. Thus, a key validated against one of the 22 matched
recipients would enable a plaintext diff for that speaker on both sides of
the transition; merely having the two encrypted UPDs cannot do so. The
[UPD extractor](../src/sonos_firmware/extract.py) refuses a private key whose
public fingerprint does not match the section's recipient.

This comparison does **not** show that the plaintext firmware is unchanged:
recipient equality only identifies the RSA envelope key. It also does not
establish that all models share one key. The 23 recipient IDs are distinct.

## What is established about possible key sources

Sonos's published [10.6 GPL download index](https://www.sonos.com/documents/gpl/10.6/gpl.html)
offers `includes1.txz` and kernel source. Its locally retained
[`mdp.h`](../sonos-research/gpl-10.6/includes/includes1/mdp.h) identifies
model 25 as **Dhuez** and 26 as **Tupelo**, defines a 16-KiB manufacturing
data page 3, and places a 2,048-byte `mdp3_model_private_key` storage field
after two 256-byte filesystem-key fields. The private field begins at offset
`0x780` within MDP3. This documents a *storage structure* in Sonos source;
it does not publish its contents or prove that every 2026 hardware generation
uses the same unwrap mechanism.

The original [model-26 `sonostool` implementation](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py)
contains a concrete Tupelo/Amlogic offline construction: read the MDP3 model
blob, derive an AES key from `SHA256(OTP[0xd0:0xe0])[:16]`, use modifier
`model`, and parse the result as an RSA private key. Its hardcoded
`SONOS_MODEL = 26` means that published code supports a **model-26 recipe**,
not a general key for every `97.1` package. The archive's own
[`mdp.py`](../src/sonos_firmware/mdp.py) reproduces the derivation and
validates MDP model identity and RSA-2048 output; the exact recipient check
is part of its [CLI](../src/sonos_firmware/cli.py). See the
[model-26 evidence note](model26-recovery.md) for provenance and limits.

Sonos's [`amperctl_drv.c`](../sonos-research/gpl-10.6/drivers/amperctl/amperctl_drv.c)
places Dhuez/model 25 product support under the Amlogic A113 architecture,
and its `mdp.h` shares the MDP3 field layout. Applying the model-26 OTP
construction to Dhuez is therefore a **candidate test**, not an established
model-25 result. A successful RSA parse alone would be insufficient: it must
produce the model-25 package recipient
`83fb98ffd324255b26fdab99105a1a02a82bae93` and actually decrypt a
preserved package. See [model-25 evidence](model25-recovery.md).

The historical [model-20 Royale](model20-recovery.md) and
[model-13 Encore](model13-recovery.md) evidence establishes a *different*
i.MX6 CAAM-wrapped-key design, requiring an unwrap on the exact matching
authorized unit. Neither model has a `97.1` package in this archive, but the
contrast matters: the Amlogic OTP formula must not be generalized to models
whose hardware or key-wrapper design is unverified. The
[key-material record](key-material.md) documents the six recovered **legacy**
model identities and why a diagnostic `private.key` (RSA-1024) is not an
OTA-recipient key (RSA-2048). Those keys do not match the `97.1` recipients.

## Feasible, conditional route for the first modern plaintext diff

1. **Prefer model 26 if a matching owned Tupelo/Sonos One unit is available.**
   The published algorithm and the archive's offline checker give it the
   strongest model-specific evidence. Obtain a complete read-only SMDP or
   MDP3 capture **and that same unit's** 0x100-byte OTP capture by an
   independently authorized method. The two artifacts are not contained in
   the public recipe or this repository. Keep captures outside Git.
2. Inspect the capture with `sonos-fw mdp-inspect` and demand MDP model 26,
   expected page/field structure, and no ambiguity about which unit supplied
   the OTP. Use `sonos-fw recover-amlogic-mdp-key` with
   `--expect-model 26` and
   `--expect-recipient b271039ff7ab0f3d395039f1f079ff71af9dea71`.
   The [model-26 note](model26-recovery.md) gives the exact offline command.
3. Treat recovery as achieved only after RSA-2048 parsing, exact fingerprint
   match, and successful extraction of the `97.1` and `96.1` same-model
   packages. Record only package/component hashes and a redacted receipt in
   Git. The raw capture and private key stay in the protected, ignored
   [recovery vault](recovery-vault.md) or another controlled secret store.
4. If model 26 hardware is unavailable, model 25 is the next *testable but
   unconfirmed* A113 candidate. Other modern models need a model-specific
   architecture and key-storage/unwrap analysis before choosing a recipe.
   Their RSA recipient IDs in [`97.1` UPD manifests](../data/upd/) give exact
   success conditions, not a source of private-key entropy.

No device writes, network changes, secure-boot bypasses, exploit deployment,
or key extraction from a live speaker were performed for this note.

## Blockers and invalid shortcuts

| Idea | Evidence-based status |
|---|---|
| Decrypt `97.1` with the six locally recovered legacy keys | Blocked: their public fingerprints differ from every `97.1` encrypted recipient. [Key inventory](key-material.md), [UPD manifests](../data/upd/) |
| Obtain model 26 key using the checked-in offline tool *without* matching MDP and OTP captures | Blocked: the tool requires both inputs, and the published source supplies code rather than these device-bound values. [`mdp.py`](../src/sonos_firmware/mdp.py), [original `sonostool`](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py) |
| Treat Sonos GPL source as publishing the 2026 private key | Unsupported: it exposes the MDP *field layout*, not a filled manufacturing page or key. [Sonos GPL index](https://www.sonos.com/documents/gpl/10.6/gpl.html), [`mdp.h`](../sonos-research/gpl-10.6/includes/includes1/mdp.h) |
| Apply model-26 Amlogic OTP derivation to all 23 modern model IDs | Unsupported: the source implementation names model 26; model 25 is only a same-platform candidate and other models need specific evidence. [Original `sonostool`](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py), [model-25 note](model25-recovery.md) |
| Brute-force the RSA private key from an update envelope | Not a viable recovery strategy: the UPD records a recipient fingerprint and encrypted payload, not an RSA private exponent or the OTP/CAAM secret needed to unwrap one. [Extractor](../src/sonos_firmware/extract.py), [model-26 construction](model26-recovery.md) |

The practical next decision is whether an **owned model-26 unit and authorized
read-only MDP+OTP captures** can be supplied. If not, a comprehensive `97.1`
code/filesystem diff remains blocked. Package-section metadata and encrypted
payload size/hash changes can still be compared honestly, but cannot be
translated into feature-level code changes.
