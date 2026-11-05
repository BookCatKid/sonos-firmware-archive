# Sonos One (Tupelo/model 26) public recovery inputs

This folder describes the two original binary inputs for the independently
verified model-26 RSA-2048 firmware key recovery. The code and metadata can be
reviewed without the binaries. The binary copies are **not yet included** in
this pull request; they must be supplied before the documented one-command
recovery works in a fresh clone.

| Filename | Length | SHA-256 |
| --- | ---: | --- |
| `mdp3-model-key.bin` | 2,048 bytes (`0x800`) | `140843cf0a950ab3891498fecb7c658dd996ded54bafeefdfc886b8b39956b5c` |
| `otp.bin` | 256 bytes (`0x100`) | `5c218954d7ee17fb84d75c937e6fae1f4c7b0ae434f2185992786c8c08208e5b` |

Provenance:

- **MDP3 field:** Peter Geissler (`blasty`) published the field in
  [issue #1, comment 6653881846](https://github.com/blasty/sonos/issues/1#issuecomment-6653881846),
  in response to the archive's request. This is exactly the `mdp3_model_private_key`
  field from offset `0x780`, including its 12-byte header and trailing
  zero-filled space. It is **not** a complete `mdp3.bin` flash page.
- **OTP:** The 0x100-byte OTP/eFUSE layout printed in slide 52 of
  [Peter Geissler's HITB AMS 2023 presentation](https://conference.hitb.org/hitbsecconf2023ams/materials/D2T1%20-%20Smart%20Speaker%20Shenanigans%20-%20Making%20the%20SONOS%20One%20Sing%20Its%20Secrets%20-%20Peter%20Geissler.pdf).
  The host-side decryption uses bytes `0xd0..0xdf`.
- **Implementation:** The `sonos_blob_encdec` routine in
  [blasty/sonos/sonostool/sonostool.py](https://github.com/blasty/sonos/blob/main/sonostool/sonostool.py),
  reproduced in the archive's `src/sonos_firmware/mdp.py`.

The checked-in `scripts/recover_model26_key.py` requires **both** exact sizes
and hashes above, parses the field header and RSA DER, and rejects a recovered
key unless its SPKI SHA-1 recipient fingerprint is:

```text
b271039ff7ab0f3d395039f1f079ff71af9dea71
```

With the binary files in this folder, from the repository root:

```bash
python scripts/recover_model26_key.py --output /path/to/model26-private.pem
sonos-fw key-id /path/to/model26-private.pem
```

The output is created exclusively (mode 0600) and never overwritten.

See [the complete research note](../../../docs/model26-recovery.md) and the
[2026-11-05 local firmware validation receipt](../../decryption-runs/2026-11-05-model26-local-validation.json).
