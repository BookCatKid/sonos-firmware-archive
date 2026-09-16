# 97.1-80312 versus 96.1-79270: observable firmware diff

**Scope and limit.** The target is Sonos's modern player update
`97.1-80312` (2026-09-08), compared with the immediately preceding modern
player version `96.1-79270` (2026-08-11). Sonos's [official system release
notes](https://support.sonos.com/en-us/article/release-notes-sonos-system-updates)
name those releases and announce dynamic portable rear surrounds, Move
Line-In/Combo Adapter support, and support for Sonos 27mcp. This report does
**not** claim to verify those three features in decrypted modern code: all
modern kernel and rootfs sections here remain encrypted. The exhaustive
observable section diff is in
[`data/diffs/96.1-79270_to_97.1-80312_sections.json`](../data/diffs/96.1-79270_to_97.1-80312_sections.json).

Sonos's [2026-09-08 staff rollout announcement](https://en.community.sonos.com/controllers-and-music-services-228995/8th-sept-2026-new-sonos-app-player-updates-now-available-6934737)
separately lists `97.1-80312` for modern players and `86.10-80260` for
legacy players. That announcement calls **27mcp an MCP integration with
Anthropic/OpenAI**, not a speaker model; its app and player feature lists
should not be conflated. The official notes page shown in the user's screenshot still
labels `86.8-78270` as the *current* legacy version. That is a disagreement
between two first-party pages, likely publication/rollout timing, not evidence
that `86.10` and `97.1` are the same firmware. A legacy `86.8`→`86.10`
extractable comparison appears below as a **parallel** release-line diff.

## What can be established for the modern 97.1 update

The archive has 22 same-model `96.1`/`97.1` pairs (models 21, 23–30,
32–38, 40–42, 46, 49, 51) and one additional `97.1` package, model 52.
There is no archived model-52 `96.1` counterpart. The common 22 have the
same section layout and the **same RSA recipient ID per model across both
versions**; there are 23 distinct recipients in the 23 new packages. The
recipient identifies which model key could unwrap a payload. It says nothing
about whether the decrypted bytes or feature set changed.

For all 22 pairs, the encrypted rootfs hash changed and its encrypted payload
length changed. Encrypted kernel hashes changed for all 22; kernel payload
length changed for 21. All 22 encrypted preinstall and section-15 hashes
changed, even when their lengths did not. The plaintext section type 22
(172,287 bytes per package) is **byte-identical** between versions for all
22. All 67 plaintext compatibility/type-2 records are byte-identical. The
plaintext section type 17 labelled `application` is only a 364-byte
descriptor/signature-like record in these modern packages, **not** an
application executable; its bytes changed in each pair.

These are exact package/section comparisons, not an inferred code diff.
Encrypted size changes can reflect code, compression, padding, encryption
packaging, or any combination; they cannot be mapped reliably to files or
Sonos's three release-note features.

| Model ID | Kernel encrypted payload delta (bytes) | Rootfs encrypted payload delta (bytes) | Whole UPD delta (bytes) |
|---:|---:|---:|---:|
| 21 | +3,360 | -32,768 | -29,408 |
| 23 | +1,232 | -94,208 | -92,976 |
| 24 | 0 | +6,021,120 | +6,021,120 |
| 25 | +93,152 | +1,282,048 | +1,375,200 |
| 26 | +93,168 | -118,784 | -25,616 |
| 27 | +93,168 | +1,896,448 | +1,989,616 |
| 28 | +3,360 | +647,168 | +650,528 |
| 29 | +3,376 | +622,592 | +625,968 |
| 30 | +1,232 | +1,302,528 | +1,303,760 |
| 32 | +3,344 | +659,456 | +662,800 |
| 33 | +93,168 | +1,044,480 | +1,137,648 |
| 34 | +3,376 | +630,784 | +634,160 |
| 35 | +39,136 | +1,495,040 | +1,534,396 |
| 36 | +93,168 | +659,456 | +752,624 |
| 37 | +3,344 | +614,400 | +617,744 |
| 38 | +640 | +1,093,632 | +1,094,272 |
| 40 | +1,008 | +2,039,808 | +2,040,816 |
| 41 | +624 | +1,916,928 | +1,917,552 |
| 42 | +640 | +503,808 | +504,448 |
| 46 | +640 | +528,384 | +529,024 |
| 49 | +63,056 | +1,073,152 | +1,136,235 |
| 51 | +3,648 | +2,039,808 | +2,043,456 |

The two changed plaintext capability/type-21 records are concrete additions:

- Model 35 adds `BRAVO_TITO:` to its existing capability list (+12 bytes).
- Model 49 adds `BAMBINO_SUPPORTS_512MBDDR:` (+27 bytes).

The other 20 type-21 records are byte-identical. All four source UPDs used
to read these two lists were re-downloaded from the archive's GitHub Releases
and SHA-256-matched against their checked-in
[`data/upd/`](../data/upd/) manifests. The new tokens are hardware/profile
markers; **do not equate model 27 with the marketing name “27mcp”** or
interpret either token as confirmation of a release-note feature without a
model mapping and plaintext implementation. In particular, 27mcp is an MCP
service/integration, not model ID 27.

For model 25 (Dhuez/Move), the specifically checked `96.1` and `97.1`
packages also share byte-identical type-21 capabilities, type-22 contents,
and all type-2 compatibility records. Its encrypted kernel grew 93,152
bytes and rootfs 1,282,048 bytes. This shows the Move package changed, but
does not locate the adapter implementation.

## Parallel extractable legacy comparison: 86.8 versus 86.10

The paired model-8 (Play:1/Fenway) source UPDs are each 21,290,793 bytes
and have identical section sizes and layout. Their type-21/type-22 and
type-2 compatibility records are unchanged. The old and new extracted
rootfs trees each contain **386 paths** (183 regular files and 203
directories/symlinks); **zero added, zero removed, 61 changed regular
files, 325 identical paths**. Every path's old/new size and SHA-256 is
recorded in the complete
[`model-8 rootfs inventory`](../data/diffs/86.8-78270_to_86.10-80260_model8_rootfs.json).
This is an exhaustive *file/byte* inventory, not a claim that every changed
binary was semantically decompiled.

The decrypted `preinstall.sh` is byte-identical (1,599 bytes). The decrypted
uImage kernel has the same 1,495,436-byte image size and 3,136,036-byte
decompressed body. Only **six bytes** of the decompressed bodies differ,
in two embedded build-host strings: `aws-jammy-190-c`→`aws-jammy-239-c`.
The compressed images and CRCs differ, but there is no other kernel-body
byte change in this model-8 pair.

The plaintext type-23 `bin/anacapad` application executable did change:
14,178,644→14,179,316 bytes, distinct build IDs and SHA-256 values, and
`.text` 11,940,840→11,941,788 bytes. Its imported dynamic symbols number
847→846; the removed import is `__atomic_compare_exchange_8`. A direct
string-set comparison (27,708→27,784 unique printable strings of length
at least eight) yields 1,392 new and 1,316 absent strings, with possible
alignment/string-extraction noise. Concrete *newly detectable* names include
`ClearSource`, `householdUpdate` endpoint/status strings,
`settings:frontierLlms`, and `reportPortableSurrounds`. However,
`PORTABLE_SURROUNDS`, `FLEXIBLE_SURROUNDS`, `enablePortableSurrounds`, and
`PrimarySupportsFlexSurrounds` were **already present in the old binary**.
This is not evidence that model 8 newly supports portable surrounds, nor a
substitute for diffing 97.1's modern implementation.

### Rootfs changes with directly readable meaning

| Area | Exact observed delta | Interpretation boundary |
|---|---|---|
| Build identity | `VERSION` and `build.properties` change 86.8→86.10, dates, build host, and source commit | Proven version/provenance change, not by itself a behavioral change. |
| Owner authorization policy | Adds `householdUpdate` permission 3 and `settings:frontierLlms` permission 7; `voice` permission 7→15 | Concrete policy expansion; numeric permission meanings require API semantics. |
| P2P authorization policy | `voice` permission 5→13 | Concrete policy expansion; no proof it affects Apple Music playback. |
| UPnP AVTransport | `AVTransport1.xml` adds boolean `A_ARG_TYPE_ClearSource` and `ClearSource` input to `RejoinGroup` | Exposed action schema change; associated runtime behavior needs application-code analysis. |
| Wi-Fi provisioning | `wifi/wpaconfig` removes the conditional nl80211/SAE and WPA-PSK-SHA256 branches and always appends the open-network block; retains WPA-PSK block | This is a real script delta; model-8 hardware reachability and system-wide Wi-Fi consequences cannot be inferred from one rootfs. |
| Trust roots | `etc/fallback_trusted_roots.rcb` shrinks 33,644→27,215 bytes | Trust-bundle bytes changed; issuer additions/removals have not been parsed. |
| Password database | `etc/shadow` bytes changed | Sensitive contents deliberately not copied into this report. |
| Logging configs | `anacapa.conf` removes a blank line; logger TOML removes two comments | No active directive change observed in these text diffs. |
| Application receipt | `opt/bin/anacapad.sha256` changed | Expected when the application binary changes. |

Exactly **38** changed ELF files differ by only 23–32 bytes each and have
byte-identical `.text` sections. Their differences are mostly build-ID and
checksum/data bytes; `libdns_sd.so.1` and `sbin/mdnsd` also have changed
`.rodata`, so even the small-diff group should not be called wholly
identical. The remaining **12** changed ELF paths have changed `.text`:
`bin/busybox`, `bin/mdputil`, `bin/upgrade`, `bin/upgrade_mgr`,
`lib/libmbedcrypto.so.16`, `lib/libmbedtls.so.21`,
`lib/libsonosminiutils.so.1`, `lib/libsonosutils.so.1`,
`wifi/N/ath_driver.ko`, `wifi/netstartd`, `wifi/wacd`, and
`wifi/wpa_supplicant`. The application `anacapad` is in addition to these
12 rootfs paths. These code-section differences are real; this investigation
has **not** attributed exact functions, bug fixes, or vulnerabilities to them.

## Why the modern code diff is still blocked, and a credible key route

There is **no recovered private key for any 97.1 recipient** in this
archive. The six legacy model keys do not match any modern recipient. Sonos
GPL material and a published model-26/Tupelo derivation give a concrete,
model-specific *offline* way to validate a candidate key, but require a
complete manufacturing-data-page-3 capture and OTP capture from the same
owned, authorized model-26 speaker. Those device-bound inputs are not
present here. Model 25/Dhuez is a same-platform candidate, not a verified
extension of the model-26 recipe. See the evidence and limits in
[`modern-key-feasibility-2026-09-15.md`](modern-key-feasibility-2026-09-15.md).
One successful, recipient-matched model-26 key would unlock both the
archived `96.1` and `97.1` model-26 packages and enable the first actual
modern file/code diff. Recipient equality across the two versions is why
one key would suffice; it is **not** evidence that the key has been found.

## Reproduction and preservation

The archive's source packages, section manifests, raw-component receipts,
and GitHub Release assets are referenced by package ID. The exact section
comparison can be regenerated with:

```sh
python3 scripts/diff_upd_manifests.py data/upd 96.1-79270 97.1-80312
```

The rootfs inventory can be regenerated from the two matching decrypted
`rootfs.squashfs` assets (after `unsquashfs` extraction) with:

```sh
python3 scripts/diff_firmware_trees.py OLD_ROOTFS NEW_ROOTFS \
  --old-label 86.8-78270-1-8/rootfs \
  --new-label 86.10-80260-1-8/rootfs
```

Both reusable scripts and both complete machine-readable comparison outputs
are checked into the repository. No recovered private key, OTP, device
capture, or `etc/shadow` plaintext is included in this report.
