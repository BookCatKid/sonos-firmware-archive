# Model 26 / Tupelo key-recovery evidence

This note records the strongest public evidence found for the blocked model-26
packages. It is a recovery plan, not a recovered private key. No device dump,
OTP value, or private key belongs in this repository.

## What the archive needs

The preserved model-26 packages use one RSA recipient fingerprint:

```text
b271039ff7ab0f3d395039f1f079ff71af9dea71
```

For example, `65.1-21040-1-26` and `96.0-78270-1-26` have all four payload
sections (preinstall, kernel, rootfs, and section 15) encrypted for that
recipient. Their section-level facts are retained in
[`data/upd/65.1-21040-1-26.json`](../data/upd/65.1-21040-1-26.json) and
[`data/upd/96.0-78270-1-26.json`](../data/upd/96.0-78270-1-26.json); no
plaintext release asset or matching private key is present in this archive
checkout.

## Public model-26 layout

The public [`darkarnium/sonor` S18-One MDP notes](https://github.com/darkarnium/sonor/blob/a7b16b36c37c636ac37b383325858903affa1d31/devices/S18-One/MDP.md)
identify the unit as Sonos Tupelo and report `MDP model 26`, `mdp3_version 2`,
and `mdp_pages_present 7` from an observed retail unit. The notes reproduce
Sonos's `smdp` layout: 0x200 bytes MDP1, 0x1000 bytes MDP2, and a 0x4000-byte
MDP3 page. Within MDP3, the model-private-key field begins at offset `0x780`
and is 2048 bytes long. The source layout is:

```c
struct smdp {
    struct manufacturing_data_page      mdp;   /* 0x0200 */
    struct manufacturing_data_page2     mdp2;  /* 0x1000 */
    struct manufacturing_data_page3     mdp3;  /* 0x4000 */
};

/* within MDP3 */
uint8_t mdp3_fskey1[256];
uint8_t mdp3_fskey2[256];
uint8_t mdp3_model_private_key[2048];
```

The same repository publishes a read-only-oriented [`dump-mdp.py`](https://github.com/darkarnium/sonor/blob/master/devices/S18-One/scripts/dump-mdp.py)
parser and says that the MDP can be located in a full flash image by its
`0xce10e47d` magic. Its public dump directory contains console logs and a
device-tree dump, not the raw MDP bytes; the raw dump is therefore still a
required out-of-band artifact.

## OTP-derived unwrap

The primary [`blasty/sonos` sonostool implementation](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py)
hard-codes `SONOS_MODEL = 26` and `SONOS_SUBMODEL = 1` (the Tupelo target),
expects a 0x4000-byte MDP3 input and a 0x100-byte OTP input, and reads the
model-key field at `0x780`. Its blob routine derives the AES-128 keystream key
as `SHA256(OTP[0xd0:0xe0])[:16]`; the `model` modifier is used to unwrap the
RSA model key. The recovered RSA key then unwraps the AES key in each encrypted
update record. The archive's offline implementation mirrors this layout in
[`src/sonos_firmware/mdp.py`](../src/sonos_firmware/mdp.py). It can require the
complete manufacturing pages to identify model 26, validate that the result is
an RSA-2048 private key, and require its public fingerprint to match the
expected package recipient:

```sh
sonos-fw recover-amlogic-mdp-key /secure/model26-smdp.bin \
  --otp /secure/model26-otp.bin \
  --output /secure/model26-private.pem \
  --expect-model 26 \
  --expect-recipient b271039ff7ab0f3d395039f1f079ff71af9dea71
```

Use a complete SMDP or larger device dump when `--expect-model` is set. A
standalone MDP3 page contains no MDP1 model identity and is deliberately
rejected by that validation.

The public repository's [README](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/README.md)
states that this path is intended to work from OTP/MDP material without using
the speaker as a decryption oracle. That does **not** make the OTP or MDP
public: the repository provides code, not a model-26 private key or a
reusable OTP dump.

## Independent corroboration and limits

The [HITB Amsterdam 2023 talk page](https://conference.hitb.org/hitbsecconf2023ams/session/smart-speaker-shenanigans-making-the-sonos-one-sing-its-secrets/)
and its [slides](https://conference.hitb.org/hitbsecconf2023ams/materials/D2T1%20-%20Smart%20Speaker%20Shenanigans%20-%20Making%20the%20SONOS%20One%20Sing%20Its%20Secrets%20-%20Peter%20Geissler.pdf)
independently describe the Sonos One's Amlogic secure monitor, protected OTP,
and extraction of hardware-backed material. Those observations are useful for
validating a supplied capture, but do not recover the 16-byte slice needed by
the host-side formula.

The public [S18 eFuse inventory](https://github.com/darkarnium/sonor/blob/master/devices/S18-One/EFUSES.md)
records secure-boot/encryption fuse state and reports the raw `SBOOT_AES256`
value as write-only (only a hash is shown). The S18 notes expose enough
structure to make a supplied capture testable, but they do not contain the
capture itself. A matching, authorized model-26 unit
must therefore supply both:

1. an exact 0x5200-byte `smdp` image (or a 0x4000-byte MDP3 page) containing a
   valid model-key field; and
2. the exact 0x100-byte OTP dump for that unit.

Until both are available, trying the formula against the archived `.upd`
files cannot produce a new key. The current state is consequently
`blocked-model-key`, not a newly discovered decryption key.

## Additional public access lead

For historical context only, [Synacktiv's Sonos One Gen 2 research](https://www.synacktiv.com/en/publications/dumping-the-sonos-one-smart-speaker)
documents obtaining cleartext firmware through a physical PCIe/DMA research
setup. This is not needed for the offline unwrap once MDP+OTP are supplied and
is outside this archive's static, non-device workflow.
