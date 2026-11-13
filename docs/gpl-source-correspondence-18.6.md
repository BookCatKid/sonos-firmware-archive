# Sonos GPL 18.6 — preliminary binary-to-source correspondence audit

This is a reproducible technical audit, **not a claim of a GPL violation**. In particular, the GPL disclosure version label `18.6` has not yet been conclusively mapped to the precise 86.10 firmware build below. Matching an upstream component version is not proof that Sonos's published source is the complete corresponding source for that specific binary.

## Firmware evidence, extracted from actual binaries

Source image: preserved Sonos ConnectX firmware root filesystem from `86.10-80260-1-17`, a 32-bit big-endian PowerPC SquashFS image, dated August 26, 2026. The analysis extracts **the binaries**, inspects their ELF note structures without executing them, and reads their build identifiers and embedded version/build strings. The local source image remains outside Git; only hashes and findings are published.

| Firmware component | SHA-256 | ELF GNU build ID | Extracted binary evidence |
| --- | --- | --- | --- |
| `/bin/busybox` | `a2fac7926b0e06c744e948e2f07e3c1b8e95aa5b0916822bf90189e47288ea1f` | `e4653d2cc580ea828d902b88311ffa2388a3bbbe` | `BusyBox v1.35.0 ()` |
| `/lib/libavcodec.so.59` | `d3d6883977dca9b71cbe2395db41b29ccf41cd60e4a92ac85aa37c4c8f18ebe1` | `8fd1e4bc2df0c363c963f3b9124955462bf59f27` | `FFmpeg version 5.0.3`; `/usr/src/debug/ffmpeg/5.0.3-r0`; PowerPC cross-compiler arguments |

Source versions advertised by [the original Sonos GPL 18.6 index](https://www.sonos.com/documents/gpl/18.6/gpl.html):

- `busybox-1.35.0-r0-patched.tar.xz` (SHA-256 `98b0cbc83e1ec21da8e64976bc053b38f938522d86693e7735a9950dc94838c2`)
- `ffmpeg-5.0.3-r0-patched.tar.xz` (SHA-256 `158915c3ae4d30e9f41e8b5e8ed71636784f24aec406d1ed27e44679ee003ba4`)

Both source archives passed XZ and tar integrity checks. The BusyBox archive contains 2,935 tar members and its root Makefile specifies version `1.35.0`. The FFmpeg archive contains 7,687 tar members, with the `RELEASE` file identifying `5.0.3`.

## Candidate missing compilation configuration

**BusyBox:** The Sonos-published `busybox-1.35.0-r0-patched.tar.xz` has no root-level `.config` and no Sonos/ConnectX-specific `defconfig`. The `configs/` directory contains only generic sample configurations (such as Android, FreeBSD, and test configurations). The generic Makefile is present, but the exact applet-selection and target-build configuration is not provided in this archive. Four additional small Sonos GPL 18.6 archives (`cc-includes`, `platforms-ctl`, `platforms-includes`, `drivers`) were inspected for BusyBox configurations and Yocto/BitBake recipes; none contained such a file.

**FFmpeg:** The source archive includes its `configure` script but lacks generated target-specific `config.h` and `ffbuild/config.mak`. Some of the exact cross-compile/configure arguments *are* embedded in the preserved `libavcodec` ELF binary. Whether the corresponding build scripts and flags are supplied via other materials is not established.

GNU GPLv2 section 3 includes scripts controlling compilation and installation in the definition of complete corresponding source ([GPL text](https://www.gnu.org/licenses/old-licenses/gpl-2.0.html)). The Linux Foundation's *Practical GPL Compliance* guide explicitly identifies missing BusyBox `.config` files as a common compliance defect, while explaining that the correct configuration could instead appear in a separately supplied build system ([guide](https://www.linuxfoundation.org/hubfs/Reports/Practical_GPL_Compliance_Digital.pdf?hsLang=en)). **Absence from an individual package archive is not by itself proof of noncompliance** if the missing build materials are legitimately available elsewhere.

## What would prove or disprove a compliance defect

1. Establish the exact Sonos software build/product corresponding to the GPL 18.6 source collection, rather than relying only on compatible BusyBox/FFmpeg versions.
2. Enumerate other offered source/build bundles and the corresponding GPL attributions for that exact firmware. Determine whether Sonos provides the target-specific BusyBox `.config`, Yocto recipes, and build/install scripts elsewhere.
3. Compare the actual compiled BusyBox applet set, embedded build IDs, and debug/build strings against source configuration and any provided build scripts. Reconstruct the binary's build procedure from evidence; a generic successful build is insufficient.
4. Try a controlled reproduction of the corresponding binaries, documenting configuration, toolchain, and unavoidable nondeterminism. If source/build materials are missing, request them from Sonos before alleging a confirmed license violation.

`python scripts/analyze_gpl_firmware.py --kernel <uImage> --rootfs <SquashFS>` repeats the ELF and version correlation on locally held binaries. It does not execute target firmware. The machine-readable evidence is `data/gpl/live/binary-analysis.json`.
