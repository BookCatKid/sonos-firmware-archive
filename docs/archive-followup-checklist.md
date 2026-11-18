# Archive follow-up acceptance checklist

This tracks the follow-up scope for [PR #26](https://github.com/BookCatKid/sonos-firmware-archive/pull/26).
Related issues: [#23](https://github.com/BookCatKid/sonos-firmware-archive/issues/23),
[#24](https://github.com/BookCatKid/sonos-firmware-archive/issues/24),
[#25](https://github.com/BookCatKid/sonos-firmware-archive/issues/25).
Issue references do not authorize comments or closure; all issues remain open.

Existing tests and validation runs are starting evidence. Each requirement
needs evidence at its stated scope before it can be marked complete. Payload
acquisition and private-key handling stay in CI; local tests use synthetic data.
Public manifest repositories are comparison evidence only. Their manifests,
repository lists and version URLs must not become runtime discovery inputs.
Historical discovery must come from Sonos protocols, source traversal and
independent web-archive indexes.

| Item | Acceptance evidence required | Follow-up status |
| --- | --- | --- |
| 1 | CI publishes the mutable ConnectX source and verifies its catalog asset. | Isolated CI published 86.4-73207; Release metadata matches the catalog's size and SHA-256. Production validation remains item 21. |
| 2 | Published Anacapa, other extractable components and an architecture/version/provenance inventory, without fixed offsets. | CI published all 11 raw ConnectX components. Generic ELF header analysis now describes application/filesystem/raw/kernel binaries; full live inventory evidence remains pending. |
| 3 | Independent historical ConnectX discovery, including distinct captures at reused URLs. | Audit methods and live coverage. |
| 4 | Every installer reported in #25 is compared with independently discovered candidates; omissions have discovery fixes. | Comparison found incomplete native coverage. Desktop/mobile workers now consume native manifest observations; traversal now checks channel counterparts even after a failed manifest. Live comparison and preservation validation continue. |
| 5 | Reproduced TLS failure has a working verified transport; CLI clearly identifies a JSON output file. | CLI and certificate-verified TLS fallback fixed; offline and PR CI checks pass. |
| 6 | Independently found GPL indexes and retrievable sources are published and stream-verified, including encoded plus signs. | Audit backlog and encoding paths. |
| 7 | Historical index TLS, pagination, timeout, query-resume and replay behavior is verified; independent alternatives investigated. | TLS fallback verified offline; continue live index and replay investigation. |
| 8 | Anonymous Google Play acquisition yields a signer-verified delivery through goopdl. | CI isolates HTTP 403 at Aurora's token dispenser, before Play API calls; compare transports and playpull protocol paths. |
| 9 | APKPure pagination, SDK/ABI variants, refreshed tickets and expired links continue making historical progress. | Extend existing live evidence and audit coverage. |
| 10 | All advertised Android delivery components are preserved, with signatures, version checks and upload recovery. | Store and official APKs now share signature/manifest inspection; receipt migration and interrupted-upload verification are tested. Audit full SDK coverage and live receipts. |
| 11 | Current and historical official mobile captures, including reused URLs, are independently discovered and preserved. | Native beta APK published; official-source signature/identity inspection and migration implemented, awaiting live validation. Historical backlog remains. |
| 12 | Public web resources are preserved; redirects and authentication requirements are classified accurately. | Investigate remaining resources and redirects. |
| 13 | Apple listing/history coverage is verified; IPA scope and authorization prerequisites are explicit. | Audit metadata and binary acquisition prerequisites. |
| 14 | Unsupported firmware and missing-key cases are investigated; decoder/key improvements automatically trigger revisits. | Audit formats, recovery methods and live ledgers. |
| 15 | Ranges, pagination, parsers, traversal and budgets impose no unintended lifetime discovery ceilings. | Audit source adapters and worker checkpoints. |
| 16 | 429/backoff, expired URLs, host failures and mixed retry/fresh queues preserve progress without starvation. | Mutable-root ordering and durable inventory queue order fixed; pending first inventories precede new arrivals and decoder reanalysis. Audit remaining queues. |
| 17 | Existing publication references are reconciled; unrecorded assets and inconsistent metadata are investigated without deleting evidence. | Repair older references and investigate page snapshot. |
| 18 | Interrupted publication, lost responses, conflicting names and changed content preserve successful work. | Document reconciliation tests cover fresh per-run inventories, corruption, outages and registration after earlier failures; continue other worker acceptance checks. |
| 19 | Every target supports cold startup and migration, missing caches/tools, concurrent updates and worker restarts. | Extend per-target acceptance evidence. |
| 20 | Obsolete entrypoints, duplicated logic and stale docs are reconciled; target registration requires no payloads or hashes. | ConnectX runtime documentation corrected; continue repository audit. |
| 21 | After merge approval, the unified workflow runs successfully against the existing production archive. | Requires approved deployment after fixes and validation. |
| 22 | Requirements in the three reported issues and automated reports are checked against published evidence. | Link from PR only; do not comment on or close issues. |

Live validation of mutable-root acquisition:
[run 44198556575](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44198556575).
Its ConnectX capture is 19,697,417 bytes with SHA-256
`5e4c5bf49d02a9c61fa93c010b5e6c44a0fc5f178e7ff2414f7d4d2711e1501a`,
published under `firmware-86.4-73207` in the isolated validation archive.
The acquisition regression checks that a mutable root is captured before the
historical backlog and that the historical candidate is processed next.

Installer handoff tests cover an empty checkout and publication from a native
manifest lead without a prior binary or hash. Channel traversal regressions
cover a missing Prod sibling that leads to available Dev history, and a 429
that retains the counterpart in the frontier without another host request.
The metadata-only [first-party traversal comparison](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44229422877)
starts from protocol roots; no external manifest collection supplies its URLs.
It completed with zero pending URLs: both traversals found 29 manifest URLs;
production found 41 installer URLs and included all 37 reference installer URLs.
This establishes method parity for the current first-party graph, not complete
historical coverage of campaigns that are no longer linked from those roots.
The [native mobile run](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44229422866)
published a newly discovered Sonos-hosted `85.00.12` beta APK (339,757,559 bytes).
GitHub's asset size and digest match its receipt. That run remained partial:
two advertised QA APK URLs returned 404 and the historical index timed out.
Hash-verified publication alone does not establish APK signature verification.

Inventory workers persist the order of waiting first analyses across catalog
insertions and decoder changes. Their default batch is 24, matching the source
extraction batch, with per-package resource bounds and resumable observations.

The metadata-only [Google Play diagnostic](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44207881506)
received HTTP 403 from Aurora's dispenser with both the SDK transport and
certificate-verified curl. No anonymous token was issued and no APK was fetched.
The adapter now distinguishes dispenser failures from Play API failures while
retaining throttle status and retry delays without recording response secrets.
