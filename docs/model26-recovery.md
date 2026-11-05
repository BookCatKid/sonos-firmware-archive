# Model 26 / Tupelo (Sonos One): verified key recovery

**Recovered 2026-11-05.** Peter Geissler (`blasty`) supplied the missing
MDP3 model-key field in [blasty/sonos issue #1](https://github.com/blasty/sonos/issues/1#issuecomment-6653881846).
Combined with the OTP published in his HITB AMS 2023 slides, it yielded an
RSA-2048 private key whose public fingerprint exactly matches the encrypted
Sonos model-26 firmware recipient. Two preserved firmware updates were decrypted
into valid Linux components and SquashFS root filesystems.

## Reproduce the recovery

The exact binary inputs and their provenance are described in
[`data/key-material/model26/README.md`](../data/key-material/model26/README.md).
Both original input binaries are now included in the tracked repository, so
recovery can be reproduced directly from a fresh clone. The binaries were
reconstructed from the published hexadecimal dump and OTP slide, with exact
sizes and SHA-256 hashes verified before committing them.

| Input | Bytes | SHA-256 |
| --- | ---: | --- |
| `data/key-material/model26/mdp3-model-key.bin` | 2,048 | `140843cf0a950ab3891498fecb7c658dd996ded54bafeefdfc886b8b39956b5c` |
| `data/key-material/model26/otp.bin` | 256 | `5c218954d7ee17fb84d75c937e6fae1f4c7b0ae434f2185992786c8c08208e5b` |

With those exact files, and the archive installed (`pip install -e .`):

```bash
python scripts/recover_model26_key.py --output /secure/model26-private.pem
sonos-fw key-id /secure/model26-private.pem
```

The result must be `b271039ff7ab0f3d395039f1f079ff71af9dea71`.
The script verifies input sizes and hashes, checks RSA DER and the public
recipient fingerprint *before* writing the private key, and writes the PEM
exclusively with owner-only permissions. Alternative input paths are supported
through `--field` and `--otp`.

Existing `sonos-fw recover-amlogic-mdp-key` accepts a complete 16-KiB MDP3
page or a larger manufacturing-data capture, optionally validating a model
identity in MDP1. Blasty provided only the 2,048-byte model-key **field** at
MDP3 offset `0x780`; the dedicated script accepts this field directly and
identifies the model through the independently observed UPD recipient. No
synthetic manufacturing page or fabricated model identity is required.

## Primary sources and layout

- [Blasty's original reply](https://github.com/blasty/sonos/issues/1#issuecomment-6653881846)
  provides the hexadecimal dump beginning at MDP3 offset `0x780`.
- [HITB Amsterdam 2023 slides](https://conference.hitb.org/hitbsecconf2023ams/materials/D2T1%20-%20Smart%20Speaker%20Shenanigans%20-%20Making%20the%20SONOS%20One%20Sing%20Its%20Secrets%20-%20Peter%20Geissler.pdf),
  slide 52, contain the matching 0x100-byte OTP/eFUSE capture.
- [blasty/sonos/sonostool.py](https://github.com/blasty/sonos/blob/main/sonostool/sonostool.py)
  implements `sonos_blob_encdec` and the firmware RSA-OAEP unwrap.
- [S18-One manufacturing-page layout](https://github.com/darkarnium/sonor/blob/a7b16b36c37c636ac37b383325858903affa1d31/devices/S18-One/MDP.md)
  identifies `mdp3_model_private_key` as a 0x800-byte field at MDP3 `0x780`.

The binary field starts with raw magic `8d c6 cc f4` (little-endian
`0xf4ccc68d`), a little-endian length `0x4cc` (1,228 bytes), and
four reserved bytes. The next 1,228 bytes contain the wrapped RSA material;
the remainder of the 2,048-byte field is zero padding.

The algorithm, reproduced in [`src/sonos_firmware/mdp.py`](../src/sonos_firmware/mdp.py),
matches the original binary-oriented implementation:

1. Derive AES-128 key `SHA256(OTP[0xd0:0xe0])[:16]`.
2. Split the wrapped field payload into ciphertext, a 12-byte IV, and a
   16-byte trailer. XOR the IV with the zero-padded ASCII `model` modifier.
3. Encrypt `modified_IV || uint32_be(counter)` with AES-ECB for counters
   starting at 2 to construct a keystream, then XOR it with ciphertext.
4. Remove PKCS#7 padding and parse the result as an RSA-2048 DER private key.
5. SHA-1-hash its public SubjectPublicKeyInfo DER; the result matches
   `b271039ff7ab0f3d395039f1f079ff71af9dea71`, shared by all
   **29 preserved model-26 packages**.

The original routine does not validate the final 16-byte trailer as a GCM
authentication tag; we preserve that behavior instead of claiming authenticated
GCM decryption. The DER parse and exact public fingerprint independently
validate the recovered key. The firmware updater's RSA-OAEP envelope then
unwraps individual AES keys for the encrypted firmware sections.

## Verified firmware extraction

The original `.upd` files were retrieved from the archive's matching GitHub
Releases and their hashes were checked against `data/catalog.json` before
decryption.

| UPD package | Source SHA-256 | Preinstall | Kernel | Rootfs |
| --- | --- | ---: | ---: | ---: |
| `57.14-37030-1-26` | `a2944eefdb47c008b1fa7c3518bdfd5a432b5e592d38f478918bfd513e6f303d` | 5,097 B | 7,764,464 B | 31,051,776 B |
| `97.1-80312-1-26` | `2d3ce8b00b7085c4d97b28c83befbc9d261888aae3490b62c7e369bf59bb78b8` | 2,489 B | 9,007,716 B | 82,362,368 B |

The extracted preinstall scripts are readable shell scripts; both rootfs
files were validated with `unsquashfs -s` and `unsquashfs -ll`, reporting
SquashFS 4.0 (gzip in 57.14; LZ4 in 97.1). Component sizes and SHA-256
values are preserved in
[`data/decryption-runs/2026-11-05-model26-local-validation.json`](../data/decryption-runs/2026-11-05-model26-local-validation.json).

**Status:** Two packages have been locally decrypted and verified; 27 have
not yet been processed. No decrypted model-26 assets have been uploaded to
GitHub Releases, so the catalog correctly continues to mark their raw
artifacts as unpublished. Recovering one recipient key does not imply all
24 plaintext packages have already been archived.

See [model-key handling](key-material.md) for the comparable recovery notes
for models 1, 8, 9, 12, 16, and 17.
