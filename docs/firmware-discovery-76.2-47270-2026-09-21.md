# Firmware discovery: 76.2-47270

Checked and preserved: **2026-09-21**.

## Result

A public Sonos Connect:Amp repair report exposed the exact official model-17
URL for firmware `76.2-47270`, including the otherwise unguessable release
directory token:

`https://update-firmware.sonos.com/firmware/Prod/76.2-47270-v15.11-zypefwbr-GA-1/76.2-47270-1-17.upd`

Source: [trulyspinach/sonos-fenway issue 2](https://github.com/trulyspinach/sonos-fenway/issues/2).
The report is evidence for the URL; authenticity of the payloads was separately
established by fetching them from Sonos's live CDN and hashing the exact bytes.

A bounded model-ID expansion from 0 through 80 found **25 live UPDs** totaling
**1,028,933,580 bytes**. Live package models are:

`8, 9, 12, 13, 14, 17, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 32, 33, 34, 35, 36, 37, 38, 40`.

All 25 source UPDs are preserved in the
[`firmware-76.2-47270`](https://github.com/BookCatKid/sonos-firmware-archive/releases/tag/firmware-76.2-47270)
Release. The complete 81-model probe and download hashes are recorded in
[`76.2-47270-model-probe-2026-09-21.json`](../data/discovery/76.2-47270-model-probe-2026-09-21.json)
and [`76.2-47270-receipt.json`](../data/discovery/76.2-47270-receipt.json).

## Extraction

Existing recovered keys decrypted models 8, 9, 12, and 17. Their **13**
preinstall, kernel, and root-filesystem components were uploaded individually
beside the source packages and reconciled by byte size and SHA-256:

- model 8: preinstall, kernel, rootfs;
- model 9: preinstall, kernel, rootfs, device payload;
- model 12: preinstall, kernel, rootfs; and
- model 17: preinstall, kernel, rootfs.

The remaining 21 UPDs use 21 model-specific recipient keys that are not in the
recovery vault. A skip-encrypted extraction pass found no supported plaintext
component types in those packages. They remain preserved source targets for
future key recovery rather than being represented as extracted.

## Upload reliability

The initial multi-file GitHub upload created five incomplete server records.
Each had an intended byte count but no digest and returned HTTP 404 when
downloaded. Only those five broken records were deleted. The resumable uploader
then streamed every local hash-verified file independently and reconciled all
source and extracted assets against GitHub's release metadata.
