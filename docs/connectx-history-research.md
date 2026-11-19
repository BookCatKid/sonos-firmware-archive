# ConnectX historical firmware and recipient provenance

Research checked 2026-11-13. The [candidate ledger](../data/discovery/connectx-history-candidates.json) records exact source URLs, provenance, response metadata, package hashes, and application hashes.

## Findings

- **One byte-verified static snapshot:** both [HTTPS](https://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd) and [HTTP](http://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd) now return `86.4-73207`, 19,697,417 bytes, SHA-256 `5e4c5bf49d02a9c61fa93c010b5e6c44a0fc5f178e7ff2414f7d4d2711e1501a`. This reproduces the existing [static receipt](../data/discovery/connectx-2026-11-13.json).
- **An older static version is evidenced, but its bytes were not recovered:** a February 2025 device log identifies `82.3-60197`, 19,026,553 bytes. This is distinct from the preserved versioned package `82.3-60160`. [Original report](https://github.com/trulyspinach/sonos-fenway/issues/2).
- **Useful historical application comparisons already exist:** 11 versioned model-17 packages have plaintext type-23 archives. All 11 were downloaded and their regular `bin/anacapad` members hashed in memory. Their application hashes are distinct from each other and the static snapshot. These are historical model-17 applications, not proof that the static URL ever served those packages. [Section receipts](../data/upd/76.2-47270-1-17.json), [download ledger](../data/discovery/connectx-history-candidates.json).
- **Catalog coverage is not release completeness:** [catalog.json](../data/catalog.json) lists 37 model-17 versions: 30 preserved packages and seven unavailable candidates. The 30 preserved packages comprise two plaintext containers and 28 encrypted-recipient containers. Additional older probe candidates are retained below as negatives, without asserting they ever existed.
- **No historical static capture was recovered from Archive.org.** Exact and prefix CDX queries returned empty indexes; broader queries encountered 503s. The absence of an accessible index entry is not evidence that no copy exists elsewhere.
- **Recipient provenance is already established locally:** model-17 recipient `f2acc70d8db3ee388c534d99d4bff64d9dab8154` traces to the plaintext `34.16-37101` updater. The matching key decrypts the static package's three OS components; see [complete extraction](connectx-anacapad.md).

## Static-link historical observations

| Observation | What is evidenced | What remains unknown |
| --- | --- | --- |
| 2024-06-13 | A firsthand network investigation reports HTTP about 17.3 MB and HTTPS about 19.3 MB for the same static path. | No exact sizes, hashes, parsed version, or recovered bytes. |
| 2024-07-01 | Replies describe the two URLs serving the same file, approximately 18.8 MB. | Unit conventions and rounding are unspecified; this does not identify today's payload. |
| 2025-02-15 | An updater log reports `82.3-60197`, Content-Length 19,026,553, model 17/submodels 2–5, target software generation 2, minimum version `34.7-33246`; the operation fails for lack of a decryption key. | No complete retained package or cryptographic hash. The log's shorter read count is not the package size. |
| 2026-10-16 / 18 | A researcher requests older copies with MD5 different from `8fbe820ecf23010b496cd63864cc5033`; a follow-up describes plaintext Anacapa and encrypted OS material. | The historical SHA-256 was not independently supplied. A reported January 2026 application date is an author interpretation, not an established release date. |
| 2026-11-13 | Direct HTTP and HTTPS downloads have identical bytes, SHA-256 above, MD5 `8fbe820ecf23010b496cd63864cc5033`, parsed type-1 version `86.4-73207`. | This does not date the first deployment or establish the newest production release. |

The 2024 observations come from the [Sonos-hosted firsthand discussion](https://en.community.sonos.com/components-and-architectural-228999/sonos-connect-cannot-factory-reset-flashing-orange-white-never-green-6877130/index3.html). The February log and October discussion are in [sonos-fenway issue #2](https://github.com/trulyspinach/sonos-fenway/issues/2), particularly the [October 16 request](https://github.com/trulyspinach/sonos-fenway/issues/2#issuecomment-6217996335) and [October 18 follow-up](https://github.com/trulyspinach/sonos-fenway/issues/2#issuecomment-6248529857). The current MD5 matches that historical reference; it supplies no evidence of an additional older snapshot. MD5 is retained for historical correlation, with SHA-256 used for today's byte identity.

The current tar payload is 13,660,160 bytes, SHA-256 `405d2322d12f125f97888cdcc4e3fe860101a9fcac4a4957883dc596b2916514`. Its section header starts at `0x249af`; the payload occupies `[0x249bf, 0xd2b9bf)`. The previously extracted `bin/anacapad` is 13,651,008 bytes, SHA-256 `e4f2514c5cae8ba44fcb975c20f23c200c6a5d9e1ba88ba0ba732224a853d84f`. Today's full package and section hashes reproduce that [extraction receipt](../data/discovery/connectx-2026-11-13.json); the member hash is inherited from the matching receipt, corroborated by the complete extraction manifest. The current three encrypted sections all name the model-17 recipient. The versioned application downloads below were extracted separately in memory.

## Versioned packages with directly verified plaintext applications

These are the concrete comparison targets for issue #24. Each package was fully fetched from its evidenced Sonos URL with a 32-MiB cap and a 30-second request timeout; all package SHA-256 values match the catalog. Only the regular tar member `bin/anacapad` was read; no files were extracted to disk or executed. Freshly checked ELF headers identify the ten newly processed entries as ELF32, big-endian PowerPC; the separately processed `81.1-58210` member was hashed without a new ELF-header check. The package/section hashes and application byte counts are in the JSON ledger.

| Version and official package | Anacapa bytes | Anacapa SHA-256 |
| --- | ---: | --- |
| [76.2-47270](https://update-firmware.sonos.com/firmware/Prod/76.2-47270-v15.11-zypefwbr-GA-1/76.2-47270-1-17.upd) | 11,748,480 | `c680c336704a86ff1356477900ca5c2d528875ff88fa2b13106ff40e481d48c6` |
| [80.1-55014](https://update-firmware.sonos.com/firmware/Prod/80.1-55014-v16.3-xqkmivn9QB-GA-1/80.1-55014-1-17.upd) | 12,601,704 | `86f8de70ca61c64871abc45b0237c63681b0ad8cc5a2ef3514094ad5f6a118f1` |
| [80.1-56190](https://update-firmware.sonos.com/firmware/Prod/80.1-56190-v16.3.3-5g1N0fceQI-GA-1/80.1-56190-1-17.upd) | 12,667,240 | `2d5ad53c965d687eb50d4f4526c0c725994642bf9cf82cc4a0ce2326cb62f007` |
| [81.1-58074](https://update-firmware.sonos.com/firmware/Prod/81.1-58074-v16.4.2-R3MV5bKh64-GA-1/81.1-58074-1-17.upd) | 12,929,488 | `0bca7602b2a7dce31018a4c3bb92e1a2f2993b035c594a3da5f140b4a6605683` |
| [81.1-58210](https://update-firmware.sonos.com/firmware/Prod/81.1-58210-v16.4.4-mXM1doJeKr-GA-2/81.1-58210-1-17.upd) | 12,929,488 | `d02525f977a56452e947132dea0e775ab4b6ca901126829c1badf8f8093cd4e2` |
| [82.2-59204](https://update-firmware.sonos.com/firmware/Prod/82.2-59204-v16.5-tNGSLOBaIg-GA-1/82.2-59204-1-17.upd) | 12,929,544 | `8f0673125292310b9f6aebee238416762d525d686ebf1fe27ae9551001998a8f` |
| [82.3-60160](https://update-firmware.sonos.com/firmware/Prod/82.3-60160-v16.5.1-BMh6lQokUO-GA-1/82.3-60160-1-17.upd) | 12,929,552 | `e8448c8ad22bd16c80131fb5374f804693b186b64a0871d6dd35be3c1eda59be` |
| [86.1-68260](https://update-firmware.sonos.com/firmware/Prod/86.1-68260-v17.2.1-8G439LJJPb-RC-1/86.1-68260-1-17.upd) | 13,126,328 | `bb05cb1cd136ce4bae140e787fe73f5fc1ec8ba7a33acb3842a5f438385a8be0` |
| [86.8-78270](https://update-firmware.sonos.com/firmware/Prod/86.8-78270-v17.2.6-HlGdHczqmy-RC-1/86.8-78270-1-17.upd) | 13,978,808 | `b88d05cc149751d70b600693fbee76bc49497780377e336be8a07f18fd9ad333` |
| [86.10-80260](https://update-firmware.sonos.com/firmware/Prod/86.10-80260-v17.2.7-WghpQDvg5l-RC-2/86.10-80260-1-17.upd) | 13,979,472 | `8d2b6784aa54654d41ad3af0c14b4b012616d44ffb120365fb9db8c01b4c4cce` |
| [86.11-82170](https://update-firmware.sonos.com/firmware/Prod/86.11-82170-v27.3-CsuS1t5Ahk-RC-1/86.11-82170-1-17.upd) | 14,176,160 | `e01f92731df9fe333f37bdc3f6b32903aa5b559d11dca7e77330715c788ef890` |

These 11 application hashes are distinct. Together with the static snapshot's matching extraction receipt, they identify 12 different known application binaries. The earliest versioned type-23 archive in the preserved set is `76.2-47270`; this is a boundary of this evidence set, not a claim about Sonos's first use of that format. Older model-17 containers can still contain Anacapa inside their filesystem rather than a separate type-23 archive. [Preserved section manifests](../data/upd/76.2-47270-1-17.json), [older plaintext filesystem receipt](../data/raw/34.16-37101-1-17.json).

The two evidenced `81.1-58210` directories are an explicit duplicate: [GA-1](https://update-firmware.sonos.com/firmware/Prod/81.1-58210-v16.4.4-mXM1doJeKr-GA-1/81.1-58210-1-17.upd) and [GA-2](https://update-firmware.sonos.com/firmware/Prod/81.1-58210-v16.4.4-mXM1doJeKr-GA-2/81.1-58210-1-17.upd) each return 19,026,536 bytes, package SHA-256 `2aedaa152a0a399754659851d8dce3e725c49153e91fe898f77751120a91ace5`, and the same application hash. GA-1 is recorded in [2024-Sonos-22 discovery](../data/discovery/2024-Sonos-22.json); GA-2 is the canonical catalog entry. Two live URLs do not imply two versions. Neither size matches the older static report's 19,026,553 bytes.

## All cataloged model-17 versions

The following is the complete 37-entry model-17 slice of the current catalog, numerically ordered. Every URL was HEAD-checked during this research. Hashes and sizes for the 30 preserved packages come from existing catalog/UPD receipts; only the plaintext predecessor and the 11 application-bearing versions were fully downloaded again here. A HEAD 200 alone does not recompute a hash. A 404 candidate establishes neither a release nor a historical payload. `source_manifest` in the JSON names the provenance record, which can differ from the package version because a manifest references older upgrade steps.

| Version / exact official URL | HEAD | Preserved bytes | Preserved package SHA-256 |
| --- | ---: | ---: | --- |
| [10.20-75300](https://update.sonos.com/firmware/Gold/Kanye-v2.8/10.20-75300-1-17.upd) | 404 | — | — |
| [28.1-83040](https://update-firmware.sonos.com/firmware/Gold/28.1-83251-v5.2-pcyakr-RC4/28.1-83040-1-17.upd) | 200 | 4,698,656 | `277b1e97298a19449800498d9d7cdac6f7b5a6a86f26390a7cb104049011c6aa` |
| [34.16-37101](https://update-firmware.sonos.com/firmware/Prod/34.16-37101-v7.1-nzhemz-MS-1/34.16-37101-1-17.upd) | 200 | 5,014,032 | `ccbb884d22a6dccae2c7dcdf9ecacb6a9217a5439a5f9b74a10e2598eb719e42` |
| [47.2-59120](https://update-firmware.sonos.com/firmware/Prod/47.2-59120-v9.3-fctsul-LR-1/47.2-59120-1-17.upd) | 404 | — | — |
| [55.1-74250](https://update-firmware.sonos.com/firmware/Prod/55.1-74250-v11.0-ytwbbr-GA-1/55.1-74250-1-17.upd) | 200 | 5,682,418 | `d9e593b1535dcf12e63bf816c78a476953a1c24004c0f9076dd0f7ec4b1c0975` |
| [57.5-87010](https://update-firmware.sonos.com/firmware/Prod/57.5-87010-v11.2.6-aocdfj-GA-1/57.5-87010-1-17.upd) | 200 | 5,715,186 | `aee5fdd3f52393d6eb19edcc747a2f028ffc0a89bce2364a41202d856545040e` |
| [57.9-23010](https://update-firmware.sonos.com/firmware/Prod/57.9-23010-v11.2.13-ykeqad-GA-1/57.9-23010-1-17.upd) | 200 | 5,719,282 | `7a46272c17dd5e895f374c180f52633248da7f082a037ace922ea7b6344efbf2` |
| [57.14-37030](https://update-firmware.sonos.com/firmware/Prod/57.14-37030-v11.7-oxmdyztc-GA-1/57.14-37030-1-17.upd) | 200 | 5,861,497 | `ac45e96627c1febff9b980de21689b6eb52847fe4a6f01244276fd7a30f2285a` |
| [57.15-39070](https://update-firmware.sonos.com/firmware/Prod/57.15-39070-v11.8-vghahcgk-GA-1/57.15-39070-1-17.upd) | 200 | 5,861,514 | `6437f387a6f38606366b858eb74ec66488bcd708954719250adf1c444c72a95e` |
| [57.19-46310](https://update-firmware.sonos.com/firmware/Prod/57.19-46310-v11.12-rozcvdwa-GA-1/57.19-46310-1-17.upd) | 200 | 5,873,802 | `2d7bfe64d6a0bda015600fbd537e55b5ccab543e07445173b4952e3fa809fd45` |
| [57.22-59130](https://update-firmware.sonos.com/firmware/Prod/57.22-59130-v11.15-72ynP0v9lu-GA-2/57.22-59130-1-17.upd) | 200 | 5,872,972 | `590548cdc2dbea0c300b8f0877b97b5ec61d65a75a8c2e2f61a3ee29961d12a3` |
| [57.22-63071](https://update-firmware.sonos.com/firmware/Prod/57.22-63071-v11.15.3-pkAzZSYXYB-SP-4/57.22-63071-1-17.upd) | 200 | 5,872,989 | `414dacc2f1999e024d3e2e20fe510e19c7c32ad1c7f44bd2bea0038f15226905` |
| [57.22-65130](https://update-firmware.sonos.com/firmware/Prod/57.22-65130-v11.15.3-2TxNp6BQia-SP-5/57.22-65130-1-17.upd) | 200 | 5,872,989 | `3701eb66136ee1b9150daa3eed1cc62642981ab0736a92f9ba71db52d96158ab` |
| [57.22-67080](https://update-firmware.sonos.com/firmware/Prod/57.22-67080-v11.15.4-yNhAXS5QQR-SP-6/57.22-67080-1-17.upd) | 200 | 5,872,989 | `aca4ba9a3b9847cc4c7a845d801b59abe0b88eba211883409cfb69a6a65e9192` |
| [57.23-74170](https://update-firmware.sonos.com/firmware/Prod/57.23-74170-v11.16.1-DILU1Q33hC-GA-1/57.23-74170-1-17.upd) | 200 | 5,872,989 | `aad8981038e71e868bf71c78e26a88493e443cd1bec2d80066412738d4631830` |
| [62.1-86220](https://update-firmware.sonos.com/firmware/Prod/62.1-86220-v13.0-mtbvge-GA-1/62.1-86220-1-17.upd) | 200 | 6,534,386 | `fd0ebfada4541f745df1d2c6d511a3e397be2cbd79bf02250dff7e749d2922fb` |
| [64.3-19080](https://update-firmware.sonos.com/firmware/Prod/64.3-19080-v13.2-udvsve-GA-1/64.3-19080-1-17.upd) | 200 | 6,718,706 | `cf8432ad4062fd0f7704efe2cc12e111294901503639fa78600922b7737a6b86` |
| [65.1-21040](https://update-firmware.sonos.com/firmware/Prod/65.1-21040-v13.3-maswnk-GA-1/65.1-21040-1-17.upd) | 200 | 6,739,186 | `b0f8e1fc887ada8b88384e981b7446b371c7811c74383de957f128b105304c58` |
| [66.4-23090](https://update-firmware.sonos.com/firmware/Prod/66.4-23090-v13.4-xgqmdq-GA-1/66.4-23090-1-17.upd) | 200 | 6,739,186 | `931535498363a7503c04b06e0ab84a03991cd8cade59f64d0fbb9921946661e1` |
| [67.1-27100](https://update-firmware.sonos.com/firmware/Prod/67.1-27100-v14.4-bjaiyg-GA-1/67.1-27100-1-17.upd) | 200 | 8,503,177 | `e2d41a1767be384671cc417c659633cb9914805f3cfd393fa4ec1f3bd01f07e1` |
| [73.0-42060](https://update-firmware.sonos.com/firmware/Prod/73.0-42060-v15.5-eqsrsbpq-GA-1/73.0-42060-1-17.upd) | 200 | 10,442,073 | `c6f85eedb24ab648875ff5235797e907064db7aa5193ed4e9577cab3e4a0b751` |
| [76.2-47270](https://update-firmware.sonos.com/firmware/Prod/76.2-47270-v15.11-zypefwbr-GA-1/76.2-47270-1-17.upd) | 200 | 19,322,684 | `856a9cafa3b46b236d76177a273378e03597e4070437c1037a4f6ce4fce25d73` |
| [80.1-55014](https://update-firmware.sonos.com/firmware/Prod/80.1-55014-v16.3-xqkmivn9QB-GA-1/80.1-55014-1-17.upd) | 200 | 18,686,552 | `1e6f6999e3c9630b3f5af851c5d0aa819bbeafb717c4afeade0f36ab4592cfc8` |
| [80.1-56190](https://update-firmware.sonos.com/firmware/Prod/80.1-56190-v16.3.3-5g1N0fceQI-GA-1/80.1-56190-1-17.upd) | 200 | 18,758,264 | `827fd85730ce29458adb6d7473e1681231c433559f2f79302e9ca79f450ec9d6` |
| [81.1-58074](https://update-firmware.sonos.com/firmware/Prod/81.1-58074-v16.4.2-R3MV5bKh64-GA-1/81.1-58074-1-17.upd) | 200 | 19,026,536 | `7d4f38b2284886af88bd75a25bf7bc2bb9ed228a16c1caa85cf07acd4f21d1ae` |
| [81.1-58210](https://update-firmware.sonos.com/firmware/Prod/81.1-58210-v16.4.4-mXM1doJeKr-GA-2/81.1-58210-1-17.upd) | 200 | 19,026,536 | `2aedaa152a0a399754659851d8dce3e725c49153e91fe898f77751120a91ace5` |
| [82.2-59204](https://update-firmware.sonos.com/firmware/Prod/82.2-59204-v16.5-tNGSLOBaIg-GA-1/82.2-59204-1-17.upd) | 200 | 19,022,424 | `368df5f08fd7e2d37f3f0595d19d288e5b3fa081b25a4eefa55017eef7ab96c0` |
| [82.3-60160](https://update-firmware.sonos.com/firmware/Prod/82.3-60160-v16.5.1-BMh6lQokUO-GA-1/82.3-60160-1-17.upd) | 200 | 19,022,440 | `5f2c6aa7d74a0dfb5d6ea87fd79c4b70e91011f0ada789851e4dab962512b18a` |
| [86.1-68260](https://update-firmware.sonos.com/firmware/Prod/86.1-68260-v17.2.1-8G439LJJPb-RC-1/86.1-68260-1-17.upd) | 200 | 19,121,881 | `b42b1f5f9445c12658fc0187dc01621cc6b994208c793e4a8bd1ecf9ccbdb99b` |
| [86.8-78270](https://update-firmware.sonos.com/firmware/Prod/86.8-78270-v17.2.6-HlGdHczqmy-RC-1/86.8-78270-1-17.upd) | 200 | 20,705,129 | `48ea99b5528d1dead3ba7f87dcabb93237ac29650f3a2b50207ff990030a5a79` |
| [86.10-80260](https://update-firmware.sonos.com/firmware/Prod/86.10-80260-v17.2.7-WghpQDvg5l-RC-2/86.10-80260-1-17.upd) | 200 | 20,705,129 | `f89815256d311abb6d4f87a1663309fb857342772a63ac62192cb1f4734b29b5` |
| [86.11-82170](https://update-firmware.sonos.com/firmware/Prod/86.11-82170-v27.3-CsuS1t5Ahk-RC-1/86.11-82170-1-17.upd) | 200 | 20,897,305 | `2808a492a67580101cc3521f051cd4de29652c3c1c4f2781a2463edd4265f5a8` |
| [90.0-67171](https://update-firmware.sonos.com/firmware/Prod/90.0-67171-v17.2-ZethjbGivZ-GA-1/90.0-67171-1-17.upd) | 404 | — | — |
| [91.0-68261](https://update-firmware.sonos.com/firmware/Prod/91.0-68261-v17.4-uG1Czb1YW1-RC-1/91.0-68261-1-17.upd) | 404 | — | — |
| [96.0-78270](https://update-firmware.sonos.com/firmware/Prod/96.0-78270-v18.6-xhPUjlijVZ-RC-1/96.0-78270-1-17.upd) | 404 | — | — |
| [96.1-79270](https://update-firmware.sonos.com/firmware/Prod/96.1-79270-v18.7-4b07MiHfnw-RC-2/96.1-79270-1-17.upd) | 404 | — | — |
| [97.1-80312](https://update-firmware.sonos.com/firmware/Prod/97.1-80312-v18.8-jZ0oXJNpeN-RC-6/97.1-80312-1-17.upd) | 404 | — | — |

The `10.20-75300` row is explicitly a synthetic model expansion of a known base URI. Other missing entries likewise must be interpreted using their evidence records, not counted as preserved ConnectX releases. A campaign named `96.x` or `97.x` may reference an older `86.x` model-17 image; the manifest's system version is not the package's own version. The static `86.4-73207` and reported static `82.3-60197` are additional observations, outside these 37 catalog rows. [Catalog source](../data/catalog.json), [signed-manifest discovery implementation](../src/sonos_firmware/discovery.py).

## Additional exact URLs and prior bounded-probe candidates

This table accounts for every ledger URL outside the 37 canonical catalog URLs. Paths retain case and opaque directory tokens exactly as recorded. Most 404s below were previously generated by bounded model-17 expansion inside an evidenced directory; their inclusion means the probe is evidenced, not that a ConnectX package existed. No new campaign-token guessing or sibling enumeration was performed.

| Exact URL | HEAD | Evidence path / kind |
| --- | ---: | --- |
| [http://update-firmware.sonos.com/firmware/Prod/34.16-37101-v7.1-nzhemz-MS-1/34.16-37101-1-17.upd?model=17](http://update-firmware.sonos.com/firmware/Prod/34.16-37101-v7.1-nzhemz-MS-1/34.16-37101-1-17.upd?model=17) | 200 | [firsthand research](https://github.com/trulyspinach/sonos-fenway/issues/2#issuecomment-2676297325); available-alias-or-manifest-candidate |
| [http://update-firmware.sonos.com/firmware/Prod/76.2-47270-v15.11-zypefwbr-GA-1/76.2-47270-1-17.upd](http://update-firmware.sonos.com/firmware/Prod/76.2-47270-v15.11-zypefwbr-GA-1/76.2-47270-1-17.upd) | 200 | [firsthand research](https://github.com/trulyspinach/sonos-fenway/issues/2); available-alias-or-manifest-candidate |
| [http://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd](http://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd) | 200 | [firsthand research](https://github.com/trulyspinach/sonos-fenway/issues/2); mutable-static-link |
| [https://update-firmware.sonos.com/firmware/Dev/50.1-63230-v10.2-fkolfu-Beta-1/50.1-63230-1-17.upd](https://update-firmware.sonos.com/firmware/Dev/50.1-63230-v10.2-fkolfu-Beta-1/50.1-63230-1-17.upd) | 404 | [data/discovery/50.1-63230-model-probe-2026-09-21.json](../data/discovery/50.1-63230-model-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Gold/Kanye-v2.8/10.20-75300-1-17.upd](https://update-firmware.sonos.com/firmware/Gold/Kanye-v2.8/10.20-75300-1-17.upd) | 404 | [data/discovery/current-availability-audit-2026-09-10.json](../data/discovery/current-availability-audit-2026-09-10.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/28.1-86141-v5.3.2-twphva-LR-1/28.1-86141-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/28.1-86141-v5.3.2-twphva-LR-1/28.1-86141-1-17.upd) | 404 | [data/discovery/28.1-86141-catalog-directory-probe-2026-09-21.json](../data/discovery/28.1-86141-catalog-directory-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/38.9-46251-v8.1.1-mvckho-LR-1/38.9-46251-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/38.9-46251-v8.1.1-mvckho-LR-1/38.9-46251-1-17.upd) | 404 | [data/discovery/38.9-46251-catalog-directory-probe-2026-09-21.json](../data/discovery/38.9-46251-catalog-directory-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/57.22-68080-v11.15.6-NUPz7mPXln-SP-8/57.22-68080-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/57.22-68080-v11.15.6-NUPz7mPXln-SP-8/57.22-68080-1-17.upd) | 404 | [data/discovery/57.22-68080-catalog-directory-probe-2026-09-21.json](../data/discovery/57.22-68080-catalog-directory-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/57.23-80060-v11.16.2-9tmT45qD4t-SP-1/57.23-80060-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/57.23-80060-v11.16.2-9tmT45qD4t-SP-1/57.23-80060-1-17.upd) | 404 | [data/discovery/57.23-80060-catalog-directory-probe-2026-09-21.json](../data/discovery/57.23-80060-catalog-directory-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/58.1-77280-v12.0-jajuoi-GA-1/58.1-77280-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/58.1-77280-v12.0-jajuoi-GA-1/58.1-77280-1-17.upd) | 404 | [data/discovery/58.1-77280-model-probe-2026-09-21.json](../data/discovery/58.1-77280-model-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/62.1-87200-v13.0.2-vqwxip-GA-1/62.1-87080-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/62.1-87200-v13.0.2-vqwxip-GA-1/62.1-87080-1-17.upd) | 404 | [data/discovery/62.1-87080-model-probe-2026-09-21.json](../data/discovery/62.1-87080-model-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/62.1-87200-v13.0.2-vqwxip-GA-1/62.1-87130-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/62.1-87200-v13.0.2-vqwxip-GA-1/62.1-87130-1-17.upd) | 404 | [data/discovery/62.1-87130-model-probe-2026-09-21.json](../data/discovery/62.1-87130-model-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/64.3-19080-v13.2-qelzft-LR-1/64.3-19080-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/64.3-19080-v13.2-qelzft-LR-1/64.3-19080-1-17.upd) | 404 | [data/discovery/64.3-19080-catalog-directory-probe-2026-09-21.json](../data/discovery/64.3-19080-catalog-directory-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/64.3-21150-v13.2.3-hkzgqn-LR-1/64.3-21150-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/64.3-21150-v13.2.3-hkzgqn-LR-1/64.3-21150-1-17.upd) | 404 | [data/discovery/64.3-21150-model-probe-2026-09-21.json](../data/discovery/64.3-21150-model-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/81.1-58210-v16.4.4-mXM1doJeKr-GA-1/81.1-58210-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/81.1-58210-v16.4.4-mXM1doJeKr-GA-1/81.1-58210-1-17.upd) | 200 | [data/discovery/2024-Sonos-22.json](../data/discovery/2024-Sonos-22.json); available-alias-or-manifest-candidate |
| [https://update-firmware.sonos.com/firmware/Prod/90.0-67300-v17.2.1-Lp7WoQPkxr-RC-1/90.0-67300-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/90.0-67300-v17.2.1-Lp7WoQPkxr-RC-1/90.0-67300-1-17.upd) | 404 | [data/discovery/90.0-67300-model-probe-2026-09-21.json](../data/discovery/90.0-67300-model-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/90.0-77070-v17.2.3-rT0797IawE-RC-6/90.0-77070-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/90.0-77070-v17.2.3-rT0797IawE-RC-6/90.0-77070-1-17.upd) | 404 | [data/discovery/90.0-77070-catalog-directory-probe-2026-09-21.json](../data/discovery/90.0-77070-catalog-directory-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/90.0-79210-v17.2.3-HBVQeC4A1l-RC-1/90.0-79210-1-17.upd](https://update-firmware.sonos.com/firmware/Prod/90.0-79210-v17.2.3-HBVQeC4A1l-RC-1/90.0-79210-1-17.upd) | 404 | [data/discovery/90.0-79210-catalog-directory-probe-2026-09-21.json](../data/discovery/90.0-79210-catalog-directory-probe-2026-09-21.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd](https://update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd) | 200 | [data/discovery/connectx-2026-11-13.json](../data/discovery/connectx-2026-11-13.json); mutable-static-link |
| [https://update.sonos.com/firmware/Prod/54.2-72160-v10.6.1-stbins-GA-1/StubInstaller-1-17.upd](https://update.sonos.com/firmware/Prod/54.2-72160-v10.6.1-stbins-GA-1/StubInstaller-1-17.upd) | 404 | [data/discovery/54.2-72160-stubinstaller-model-probe-2026-09-10.json](../data/discovery/54.2-72160-stubinstaller-model-probe-2026-09-10.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update.sonos.com/firmware/Prod/90.0-67171-v17.2-ZethjbGivZ-GA-1/90.0-67171-1-17.upd](https://update.sonos.com/firmware/Prod/90.0-67171-v17.2-ZethjbGivZ-GA-1/90.0-67171-1-17.upd) | 404 | [data/discovery/current-availability-audit-2026-09-10.json](../data/discovery/current-availability-audit-2026-09-10.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update.sonos.com/firmware/Prod/91.0-68261-v17.4-uG1Czb1YW1-RC-1/91.0-68261-1-17.upd](https://update.sonos.com/firmware/Prod/91.0-68261-v17.4-uG1Czb1YW1-RC-1/91.0-68261-1-17.upd) | 404 | [data/discovery/current-availability-audit-2026-09-10.json](../data/discovery/current-availability-audit-2026-09-10.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update.sonos.com/firmware/Prod/96.0-78270-v18.6-xhPUjlijVZ-RC-1/96.0-78270-1-17.upd](https://update.sonos.com/firmware/Prod/96.0-78270-v18.6-xhPUjlijVZ-RC-1/96.0-78270-1-17.upd) | 404 | [data/discovery/current-availability-audit-2026-09-10.json](../data/discovery/current-availability-audit-2026-09-10.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |
| [https://update.sonos.com/firmware/Prod/96.1-79270-v18.7-4b07MiHfnw-RC-2/96.1-79270-1-17.upd](https://update.sonos.com/firmware/Prod/96.1-79270-v18.7-4b07MiHfnw-RC-2/96.1-79270-1-17.upd) | 404 | [data/discovery/current-availability-audit-2026-09-10.json](../data/discovery/current-availability-audit-2026-09-10.json); previously-recorded-probe-candidate-not-proof-of-historical-existence |

The explicit February 2025 [public predecessor link](http://update-firmware.sonos.com/firmware/Prod/34.16-37101-v7.1-nzhemz-MS-1/34.16-37101-1-17.upd?model=17) was also fully fetched: its SHA-256 is identical to the HTTPS predecessor. Query parameters containing device or household identifiers were excluded; the public `model=17` selector is retained.

## Historical capture search and limits

The research checked these first-party Archive.org index endpoints on 2026-11-13 before 16:55:07 UTC; individual request timestamps were not retained. Successful requests returned HTTP 200 with the following contents:

| Archive query | Result |
| --- | --- |
| [Query 1](https://web.archive.org/cdx/search/cdx?url=update-firmware.sonos.com%2Ffirmware%2FProd%2FJFFS_Static_Link%2Fconnectx.upd&output=json&fl=timestamp%2Coriginal%2Cstatuscode%2Cmimetype%2Cdigest%2Clength&limit=1000) | `[]` |
| [Query 2](https://web.archive.org/cdx/search/cdx?url=update.sonos.com%2Ffirmware%2FProd%2FJFFS_Static_Link%2Fconnectx.upd&output=json&fl=timestamp%2Coriginal%2Cstatuscode%2Cmimetype%2Cdigest%2Clength&limit=1000) | `[]` |
| [Query 3](https://web.archive.org/cdx/search/cdx?url=update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/&matchType=prefix&output=json) | `[]` |
| [Query 4](https://web.archive.org/cdx/search/cdx?url=update-firmware.sonos.com/firmware/Prod/JFFS_Static_Link/connectx.upd*&output=json) | `[]` |
| [Query 5](https://archive.org/wayback/available?url=https%3A%2F%2Fupdate-firmware.sonos.com%2Ffirmware%2FProd%2FJFFS_Static_Link%2Fconnectx.upd&timestamp=20240613) | `empty archived_snapshots` |
| [Query 6](https://archive.org/wayback/available?url=http%3A%2F%2Fupdate-firmware.sonos.com%2Ffirmware%2FProd%2FJFFS_Static_Link%2Fconnectx.upd&timestamp=20250215) | `empty archived_snapshots` |

Queries 1–2 target the exact static path on each Sonos update host; 3 covers the static directory prefix; 4 covers filename-prefix/query-string variants; 5 targets HTTPS near 2024-06-13; 6 targets HTTP near 2025-02-15. The search also found zero Archive.org item-search matches for the exact filename. Broader filtered CDX queries returned 503, so broad historical coverage remains inconclusive. No CDX capture digest, replayed package, historical SHA-256, or independently preserved static body was obtained. Empty results must not be rephrased as “the file was never archived.”

No date-based Wayback replay URLs were invented. The 2024 size reports and 2025 log remain documentary observations, not downloadable historic captures. Repeated downloads of today's static URL only reproduce today's snapshot.

## Recipient and public-key provenance

The public recipient identifier is `f2acc70d8db3ee388c534d99d4bff64d9dab8154`. The implementation defines it as **SHA-1 over DER SubjectPublicKeyInfo**, not a package SHA-256, TLS certificate fingerprint, model-8 key, or firmware signature-verification key. [Fingerprint implementation](../src/sonos_firmware/extract.py), [UPD recipient parser](../src/sonos_firmware/upd.py).

The existing [2026-09-08 recovery receipt](../data/decryption-runs/2026-09-08-new-model16-model17.json) attributes model-17 recovery to the plaintext `34.16-37101-1-17` updater and records successful decryption of the then-preserved 17 encrypted model-17 packages. Today's preserved UPD receipts identify the same recipient in 28 encrypted model-17 versions; the older count of 17 is a dated recovery-run count. The [key documentation](key-material.md) and [legacy implementation](../src/sonos_firmware/legacy.py) describe the embedded encrypted wrapper and deterministic legacy construction. The [complete extraction pipeline](connectx-anacapad.md) independently reproduces this recovery.

The public acquisition chain is concrete:

1. The [official plaintext package](https://update-firmware.sonos.com/firmware/Prod/34.16-37101-v7.1-nzhemz-MS-1/34.16-37101-1-17.upd) freshly downloads as 5,014,032 bytes, SHA-256 `ccbb884d22a6dccae2c7dcdf9ecacb6a9217a5439a5f9b74a10e2598eb719e42`.
2. Its [preserved rootfs asset](https://github.com/BookCatKid/sonos-firmware-archive/releases/download/firmware-34.16-37101/34.16-37101-1-17-rootfs.bin) has recorded SHA-256 `c586832f1212fd38881db731860cb31f4f06aa4496138d732d3636620347f9f2`. Reading `bin/upgrade` with `unsquashfs -cat` yields SHA-256 `097d64dfce2b02b620950a3dd6688fd85572a68666474f0d42b0d4d418587e26`. [Existing component receipt](../data/raw/34.16-37101-1-17.json).
3. The extracted updater is 123,792 bytes, the same SHA-256. Its 1,264-byte encrypted wrapper at file offset `0x18f98` hashes to `aa0acda1dd5460b4bd114f437266dac1a88a4e13847d5fd1735cea089a107786` and exactly matches the existing encrypted support copy. No secret wrapper plaintext or derived values are included.
4. The matching recipient successfully decrypts the static preinstall, kernel, and rootfs. Component hashes and filesystem inventories are recorded under [`data/raw/`](../data/raw/).

The public research header [includes/mdp.h at a pinned commit](https://github.com/trulyspinach/sonos-fenway/blob/b01d1cf7eed34988e15a8208034d2e5a400efde7/includes/mdp.h#L38) distinguishes CONNECTX `0x11` (17) from FENWAY `0x08` (8). It carries Sonos copyright/GPL labeling, but the research repository alone does not establish its original release provenance. This supports keeping the numeric families separate. A model-8 key must not be inferred to work for the model-17 recipient.

Exact-fingerprint and prefix GitHub searches found this archive's own receipts/documentation, but no independent matching public-key file or external attestation. This is a search limitation, not proof that no external publication exists. Existing local recovery evidence and the hash-verified public rootfs are sufficient for the stated chain; no external key leak is required or claimed.

## Coverage and remaining historical gap

The ledger records 61 exact URLs, including protocol aliases, duplicate campaign
directories, and unavailable candidates. The automated pipeline normalizes HTTP
to HTTPS, merges those URLs with catalog and evidenced sibling candidates, and
deduplicates recovered bytes by SHA-256. Negative probes remain candidates,
not claimed releases.

The remaining historical-static lead is a retained or newly indexed copy with
a hash different from today's snapshot, particularly the documented `82.3-60197`.
The versioned application hashes above support historical comparisons independently
of that missing static capture. This research accounts for the URLs found in the
scoped repository evidence and cited original reports; it does not establish every
firmware Sonos ever released.
