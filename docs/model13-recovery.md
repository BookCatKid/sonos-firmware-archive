# Model 13 firmware-key recovery

## Conclusion

The practical recovery path for package model 13 is an **authorized, read-only
unwrap on the same Play:5 (Gen 2) unit that supplied the manufacturing data**.
The model RSA private key is stored in MDP3 as a CAAM-wrapped red-key blob. On
this i.MX6 platform, the CAAM hardware—not a host-side OTP dump—must decapsulate
that blob. Once recovered, one RSA-2048 key should cover the 17 preserved model
13 packages that share recipient fingerprint
`87da92d5173e329c366300810f9d94f525bf2d65`.

This is not an access or exploitation guide. It assumes an owned device on
which the operator already has legitimate administrative control or an
authorized recovery environment. Nothing in the workflow writes NAND, fuses,
the bootloader, or the manufacturing pages.

## Package evidence and target identity

The archive maps package model 13 to Play:5 (Gen 2) in
[`data/completeness.json`](../data/completeness.json). Sonos's published MDP
header independently names numeric model 13 `MDP_MODEL_ENCORE` in the locally
extracted [`mdp.h`](../sonos-research/gpl-10.6/includes/includes1/mdp.h). The
plaintext kernel from `34.16-37101-1-13` identifies its device tree as
`Sonos i.MX6 SoloX Encore`; its plaintext section 15 identifies `Board: Sonos
Encore` and `U-Boot 2014.04.58-Encore-Strict-Rev2.1`. The local analysis files
are [`model13-34.16-kernel.uImage`](../sonos-research/model13-34.16-kernel.uImage)
and [`model13-34.16-section15.bin`](../sonos-research/model13-34.16-section15.bin).

The exact legacy package record,
[`data/upd/34.16-37101-1-13.json`](../data/upd/34.16-37101-1-13.json), records a
9,904,177-byte UPD with SHA-256
`4bdfdd788a11cc0844357fe6bd50b00071a15fd44b9d87f4ae52e666eb0f34a4`.
Its preinstall and rootfs sections are encrypted to recipient
`87da92d5173e329c366300810f9d94f525bf2d65`, while its kernel is plaintext.
The newer [`86.8-78270-1-13`](../data/upd/86.8-78270-1-13.json) record gives the
same recipient for its encrypted preinstall, kernel, rootfs, and additional
payload. The [`key-recovery ledger`](../data/key-recovery-ledger.json) lists 17
preserved packages under this one missing recipient.

The recipient value is not an arbitrary label. The archive parser reads it
from the Sonos encrypted envelope, and the extractor computes the matching key
identity as SHA-1 over the RSA public key encoded as DER SubjectPublicKeyInfo;
see [`upd.py`](../src/sonos_firmware/upd.py) and
[`extract.py`](../src/sonos_firmware/extract.py).

## MDP3 layout

The structure below follows Sonos's published `mdp.h`. The local copy came from
the separately retained Sonos GPL/LGPL source artifacts indexed by
[`data/gpl/indexes/10.6.html`](../data/gpl/indexes/10.6.html).

| Region | Size | Offset from complete `smdp` | Offset within MDP3 |
|---|---:|---:|---:|
| MDP1 | `0x0200` | `0x0000` | — |
| MDP2 | `0x1000` | `0x0200` | — |
| MDP3 | `0x4000` | `0x1200` | `0x0000` |
| JFFS/UBIFS key field | `0x0100` | `0x1780` | `0x0580` |
| Rootfs key field | `0x0100` | `0x1880` | `0x0680` |
| Model private-key field | `0x0800` | `0x1980` | `0x0780` |

MDP3 begins with little-endian magic `0xcba979f0` (bytes `f0 79 a9 cb`).
The model-key field is 2,048 bytes and begins with this 12-byte header:

```c
struct mdp_key_hdr {
    uint32_t m_magic;
    uint32_t m_len;
    uint32_t m_reserved;
};
```

For the model RSA key, `m_magic` must be
`MDP_KEY_HDR_MAGIC_CAAM_PKCS1_RSA_PRIV`, value `0xF4CCC68D` (little-endian
bytes `8d c6 cc f4`). `m_len` is the CAAM blob length; it must fit within the
remaining 2,036 bytes of the field. The wrapped blob begins immediately after
the header. The public [`blasty/sonos` implementation](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L32-L45)
independently uses the same MDP3 size, magic, field offsets, and model-key
header, and selects the `model` key modifier when unwrapping the RSA field
([source](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L161-L163)).

The boot path in Sonos's local
[`arch/arm/kernel/setup.c`](../sonos-research/gpl-7.3/linux-3.10.53/arch/arm/kernel/setup.c)
checks the MDP1 and MDP3 magics and copies MDP3 into `sys_mdp3`. That object is
exported by [`kernel/sonos.c`](../sonos-research/gpl-7.3/linux-3.10.53/kernel/sonos.c),
so an in-kernel recovery helper can consume the already validated in-memory
copy instead of rereading raw flash.

## Why this needs the same i.MX6 device

This model must not be treated like the Amlogic target in `sonostool`. That
tool's host-side formula, `SHA256(OTP[0xD0:0xE0])[:16]`, is explicitly tied to
its hardcoded model 26 target; it is not evidence for Encore.

Encore's published Linux source instead routes blob import through the i.MX
CAAM secure-memory driver. On decrypt, the driver imports the blob with red-key
color, CCM cover mode, the eight-byte key modifier, and then reads the
decapsulated plaintext from the secure-memory slot. See local
[`kernel/sonos_secure.c`](../sonos-research/gpl-7.3/linux-3.10.53/kernel/sonos_secure.c),
[`sonos_sm_access.c`](../sonos-research/gpl-7.3/linux-3.10.53/drivers/crypto/caam/sonos_sm_access.c),
and [`sm_store.c`](../sonos-research/gpl-7.3/linux-3.10.53/drivers/crypto/caam/sm_store.c).

The CAAM key-blob design roots secure-production blobs in a random,
device-specific 256-bit OTPMK fused into the SoC. That key is never disclosed
to the CPU; CAAM can only use it internally. The
[`usbarmory/caam-keyblob` documentation](https://github.com/usbarmory/caam-keyblob)
describes both the device-specific, unreadable OTPMK and the reverse
decapsulation operation. The Linux kernel's
[`trusted-encrypted.rst`](https://github.com/torvalds/linux/blob/master/Documentation/security/keys/trusted-encrypted.rst)
likewise describes the CAAM trust root as a never-disclosed per-SoC OTPMK.
NXP's official [`keyctl_caam`](https://github.com/nxp-imx/keyctl_caam)
documents CAAM blob import as an on-device operation.

Therefore, a copied MDP3 blob is not enough: the blob from unit A must be
submitted to unit A's CAAM. Another retail Encore has a different OTPMK even
though the recovered plaintext RSA key is expected to be model-wide. A device
in a non-secure manufacturing/test state can select a common test key instead
of OTPMK, but that exception must not be assumed for retail hardware.

## Exact in-kernel unwrap

The exported API and constants are declared in the local
[`include/linux/sonos_kernel.h`](../sonos-research/gpl-7.3/linux-3.10.53/include/linux/sonos_kernel.h):

```c
int sonos_key_encdec(int operation, int color,
                     const void *in, int inlen,
                     void *out, size_t *outlen,
                     const char *keymod);
```

After copying and validating the 12-byte field header, an authorized helper's
essential call is:

```c
blob = sys_mdp3.mdp3_model_private_key + sizeof(struct mdp_key_hdr);
blob_len = hdr.m_len;
plain_len = blob_len - 48;

rc = sonos_key_encdec(DECRYPT, USE_RED,
                      blob, blob_len,
                      output, &plain_len,
                      "model");
```

All of these details matter:

- reject the field unless `hdr.m_magic == 0xF4CCC68D`;
- reject `blob_len < 48` or `blob_len > 2036` before allocating or calling;
- pass only the blob, not its 12-byte MDP header;
- use `DECRYPT`, `USE_RED`, and the key modifier `model`;
- allocate at least `blob_len - 48` output bytes;
- treat the expected plaintext length as exactly `blob_len - 48`, because the
  Sonos wrapper sets `original_length = inlen - BLOB_OVERHEAD`, where
  `BLOB_OVERHEAD` is 48;
- parse the result as DER PKCS#1 RSA private-key material and require a
  2,048-bit key before writing anything; and
- erase temporary plaintext buffers after the validated key has been handed
  to a root-only destination.

The exact `34.16-37101` kernel is Linux 3.10.31, while the retained Sonos GPL
tree demonstrating this API is Linux 3.10.53. Its embedded configuration has
`CONFIG_MODULES=y`, `CONFIG_SONOS_SECBOOT=y`, `CONFIG_SONOS_ENCORE=y`, and
`CONFIG_SONOS_CAAMKEYS=m`, with module signatures and modversions disabled.
That makes a small helper plausible, but it does **not** justify force-loading
an ABI-mismatched module. First verify the symbols and prototype against the
exact running kernel, and build against its matching headers/toolchain. A
controlled RAM-booted recovery kernel is preferable if an exact module build
cannot be established.

## Safe, non-destructive workflow

1. Use only an owned model 13 device already under authorized administrative
   control. Record its serial and current firmware, disconnect it from normal
   service, and keep all capture media offline.
2. Make a full read-only backup before analysis. Hash the backup immediately.
   Do not alter the MDP partition, NAND, fuses, boot arguments, or U-Boot
   environment.
3. Prefer the kernel's exported `sys_mdp3` copy. If a manufacturing-page read
   is also retained, accept either exactly `0x4000` bytes beginning with MDP3
   magic or a complete `0x5200`-byte `smdp` with MDP3 at `0x1200`. Run
   `sonos-fw mdp-inspect <dump>` and confirm model 13 plus the model-key header
   before any unwrap attempt.
4. Use a minimal, audited helper built for the exact kernel. It should expose
   no write operations, unwrap only the model field, allow only root to read
   the one-shot result, avoid kernel logging of key bytes, zero temporary
   buffers, and unload or disappear after one successful read.
5. On the analysis host, store the recovered key in a newly created mode-0600
   file. Never paste it into logs, issue trackers, shell history, or this
   repository.
6. Validate before relying on it:

   ```sh
   sonos-fw key-id recovered-model13.pem
   ```

   The only acceptable output is
   `87da92d5173e329c366300810f9d94f525bf2d65`. A different fingerprint means
   stop; do not try the key against unrelated packages.
7. Test one package into a disposable output directory:

   ```sh
   sonos-fw extract 86.8-78270-1-13.upd \
     --directory model13-extracted \
     --private-key recovered-model13.pem \
     --receipt model13-extraction.json
   ```

   The extractor verifies the envelope recipient before RSA-OAEP/AES
   decryption. Inspect the receipt and component hashes, then use the same key
   only for packages carrying the identical recipient.
8. Encrypt the recovered key at rest or move it to an offline secret store.
   Retain only the public recipient fingerprint and non-secret component hashes
   in version control. Reboot the device to clear recovery-kernel state.

## What remains to prove on hardware

The static evidence establishes the storage location, wrapper type, key
modifier, CAAM API, and expected recipient. One controlled run on an authorized
Encore is still required to prove the runtime ABI and to obtain the key. The
completion criteria are deliberately strict: successful CAAM return, valid
DER RSA-2048 output, exact recipient fingerprint match, and successful
decryption of at least one model 13 UPD with a reproducible receipt. Until all
four occur, model 13 should remain `blocked-model-key` in the archive ledger.
