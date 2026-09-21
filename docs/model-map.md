# Package-model to hardware-codename map

This table maps the numeric `package_model` used in `.upd` filenames and
manifests to the internal codenames defined in Sonos's GPL-published
`mdp.h` headers. The authoritative sources are:

- `sonos-research/gpl-10.6/includes/includes1/mdp.h` (Sonos GPL 10.6
  `includes1.txz`, copyright 2003-2019; defines models through 32), and
- `includes/mdp.h` in the public `trulyspinach/sonos-fenway` repository
  (copyright 2003-2016; defines models through 22), which matches the GPL
  header for every overlapping model.

Product-name assignments are only listed where independently evidenced by
archived artifacts (device icons, console logs, public manifests, or the
recovery docs); an empty product cell means the codename is established but
the retail product is not confirmed in this archive.

Models 24–52 are corroborated (or first named) by the plaintext
capability-flag section every modern `.upd` carries: section type 21 holds a
small header (magic `0x35167f49`) followed by newline-separated `FLAG:`
strings — generic `*_PATCHES:` entries plus per-platform hardware flags such
as `MONACO_AMPS:` or `PALLAS_SUPPORTS_DVT3:`. The flag prefix is the platform
codename, which is how models 28, 33, 35, 36, 38, 40, 46, 51 and 52 below got
their names without any `mdp.h` coverage.

| Model | Codename | Evidenced product / notes |
|---:|---|---|
| 0 | — | Legacy/base package family (10.20-75300 model-0 probes only) |
| 1 | `ZP` | ZonePlayer family (ZP80/ZP100); submodels ES2, TS1, TS2, REDROCKS |
| 2 | `HH` | CR100 handheld controller |
| 3 | `WDCR` | Windows desktop controller (`.exe` packages) |
| 4 | `MDCR` | Mac desktop controller (`.dmg` packages) |
| 5 | `LINK` | ZoneBridge ZB100/BR100; rootfs ships `icon-ZB100.png`, `ae531x.o`, SPI flash with RedBoot FIS table. **Key missing** — needs first 16 KiB of `/dev/mtd/0` (boot block) |
| 6 | `WOODSTOCK` | CR200-era controller family |
| 7 | `CASBAH` | Controller-era family |
| 8 | `FENWAY` | Hardware family spanning Play:5 Gen 1, Sub Gen 1, Play:1 (submodels FENWAY/ANVIL/AMOEBA/LP). **Key recovered** |
| 9 | `LIMELIGHT` | Playbar. **Key recovered** |
| 10 | `ICR` | iOS controller-era family |
| 11 | `ACR` | Android controller-era family |
| 12 | `FILLMORE` | Play:3 family. **Key recovered** |
| 13 | `ENCORE` | Play:5 Gen 2 (S6). i.MX6 CAAM path documented in `model13-recovery.md`. **Key missing** |
| 14 | `SOLBASE` | i.MX6 SoloX-family (`SOLBASE_IMX6SX_REVC` revision). **Key missing** — 22 blocked packages |
| 15 | `TEST_CONTROLLER` | Factory/test image family |
| 16 | `WEMBLEY` | ZP120 Connect:Amp Gen 1; public console log identifies board "Sonos Wembley" (MPC8272). **Key recovered** |
| 17 | `CONNECTX` | Connect family (submodels shared with WEMBLEY). **Key recovered** |
| 18 | `RESERVED1` | Reserved |
| 19 | `RESERVED2` | Reserved |
| 20 | `ROYALE` | IKEA SYMFONISK bookshelf (per public systemcrash manifest mapping); i.MX6 SoloX. Submodels ROYALE/MARQUEE/LARGO. CAAM path in `model20-recovery.md`. **Key missing** |
| 21 | `BOOTLEG` | S2-era (2016+) device. **Key missing** |
| 22 | `PARAMOUNT` | i.MX6 SoloX (`PARAMOUNT_MP_SOLOX` revision). **Key missing** |
| 23 | `ELREY` | Secure-boot era (`ELREY_AUTH_3_0` revision). **Key missing** |
| 24 | `HIDEOUT` | Secure-boot era (`HIDEOUT_AUTH_3_0`); confirmed by `HIDEOUT_ADP8863`/`HIDEOUT_STM32`/`HIDEOUT_FNI_PSOC`/`HIDEOUT_PSOC_4248` strings. **Key missing** |
| 25 | `DHUEZ` | Move (S17); confirmed by `DHUEZ_PSOC_BL483` string. Amlogic path candidate in `model25-recovery.md`. **Key missing** |
| 26 | `TUPELO` | Sonos One Gen 2 (S18); confirmed by `TUPELO_LED_NTC` string. Amlogic MDP3+OTP path in `model26-recovery.md`. **Key missing** |
| 27 | `APOLLO` | Confirmed by `APOLLO_PSOC_FNI`/`APOLLO_RICHTEK`/`APOLLO_GB11` strings. Pre-envelope updater uses on-device `upgradekey.priv` (see `key-material.md`). **Key missing** |
| 28 | `CHAPLIN` / `KAPITAL` | GPL header says CHAPLIN; UPD capability strings say `KAPITAL_PSOC_FNI` (probably a rename or board variant). **Key missing** — 25 blocked packages |
| 29 | `NEPTUNE` | Capability strings expose no platform name, only `CS43198_DAC_support` + `PSOC_4128` (Cirrus DAC + Cypress PSoC). **Key missing** |
| 30 | `DOMINO` | Confirmed by `DOMINO_BMA420`/`DOMINO_PSOC` capability strings. **Key missing** |
| 31 | `TRIDENT` | **Key missing** |
| 32 | `VERTIGO` | Sub Gen 3 (per public systemcrash manifest mapping); confirmed by `VERTIGO_Skyhigh_Flash` string. **Key missing** |
| 33 | `MONACO` | `MONACO_AMPS`, `MONACO_MULTI_FG`, `MONACO_SUPPORTS_SIDECAR` + `SIDECAR_SUPPORTS_NEW_QI_CHG` (Qi-charging sidecar accessory). **Key missing** |
| 34 | — | Generic patch flags only. **Key missing** |
| 35 | `BRAVO` | `BRAVO_GB21_V2`, `BRAVO_GB33`, `BRAVO_GB34`, `BRAVO_GB21_FINAL` board revisions. **Key missing** |
| 36 | `FURY` | `FURY_MA12070P` (Infineon MERUS class-D amp) + `FURY_MULTI_LED_AMP`. **Key missing** |
| 37 | — | Generic patch flags only. **Key missing** |
| 38 | `OPTIMO` / `RAVEN` | `OPTIMO_SUPPORTS_EVT/DVT/CORNELL1_MIC` + `RAVEN_SUPPORTS_ETHERNET/P2C` — the UPD carries both names (paired platform or mainboard/sub-board). **Key missing** |
| 40 | `PRIMA` | `PRIMA_SUPPORTS_P2B`, `!PRIMA_SUPPORTS_BOOTSOUND` (a `!` negation flag). **Key missing** |
| 41, 42, 49 | — | Generic patch flags only. **Key missing** |
| 46 | `PALLAS` | `PALLAS_SUPPORTS_P2B/EVT/DVT/DVT3` board revisions. **Key missing** |
| 51 | `MOJAVE` | `MOJAVE_SUPPORTS_DDR4/MULTI_PMU` + `A113X2_2025_BL31` — the **Amlogic A113X2** SoC with ARM Trusted Firmware BL31, i.e. the same SoC family as DHUEZ/TUPELO. **Key missing** |
| 52 | `GAMBIT` | `GAMBIT_SUPPORTS_P2A/P2B` board revisions. **Key missing** |
| 44, 47, 48, 53 | — | Appear in manifests but have no preserved encrypted package |

## Structural constants confirmed by the same headers

- MDP1 magic `0xce10e47d`, MDP2 magic `0xca989b4a` (plus `0xfa87b921`
  encrypted variant), MDP3 magic `0xcba979f0` — matching
  `src/sonos_firmware/mdp.py`.
- MDP3 layout: `auth_sig[512]` at 0x180, `cpuid_sig[512]` at 0x380,
  `fskey1[256]` at 0x580, `fskey2[256]` at 0x680,
  `model_private_key[2048]` at 0x780, then 2048-byte prod/dev unit RSA
  key+cert fields at 0xf80/0x1780/0x1f80/0x2780 — matching the parse offsets
  in `mdp.py` and the sonostool field reads.
- Key-field header magics: `CAAM_RSAREF_PRIV 0x19283746`,
  `CAAM_PKCS1_RSA_PRIV 0xf4ccc68d` (the model-private-key field),
  `CAAM_AES128_BLACK_KEY 0xf0a2ade7`, `CAAM_AES128_RED_KEY 0x618c4de8`
  (the fskey fields) — confirming CAAM blob terminology on secure-boot
  models.
- `MDP_DEVICE` is `/dev/mdp` on secure-boot architectures and `/dev/mtd/0`
  (or `/dev/mtd1` on Fillmore/MIPS24K) on legacy platforms — consistent with
  the model-5 updater hashing `/dev/mtd/0` while the MDP itself lives on a
  different device node.
- Signature blob magic `0x621da74d` (`SS_MAGIC` in `sonos_signature.h`),
  explaining the `MAGIC_UNK1` field in the public sonostool.
- Envelope crypto enums (`sonos_crypto_enums.h`): `RSA_OAEP_SHA1` key wrap
  and `AES_128_CBC` payload encryption — matching the encrypted UPD
  envelope construction.
- Flash partition table (`sonos_ptable.h`): magic `0x653503e4`,
  `PE_TYPE_MDP 6`, `PE_TYPE_KERNEL 16`, `PE_TYPE_ROOT 17`,
  `PE_TYPE_UBOOT 19` — useful when interpreting future device dumps.
- Section upgrade header (`sect_upgrade_header.h`): magic `0x536f7821`
  ("Sox!"), rootfs format values `PLAINTEXT`, `FIXED_KEY`, `RED_KEY`,
  `BLACK_KEY`.

## UPD section types identified from payload magics

Cross-checking `payload_magic` across all 559 preserved section manifests:

- Types 9 and 10 always start with `0x653503e4` (`PT_MAGIC`) — flash
  partition-table descriptors (e.g. model 5 carries KERNEL/ROOT entries).
- Type 21 is the capability-flag list described above.
- Type 22 always starts with `0x621da74d` (`SS_MAGIC`) — the signature blob,
  which wraps the certificate/CA material.
- Type 15 always starts with `0x886499ca` — an RSA-OAEP/AES envelope, i.e.
  another encrypted payload kind alongside type 13.
- `SECTION_NAMES` in `src/sonos_firmware/upd.py` now reflects these names.
