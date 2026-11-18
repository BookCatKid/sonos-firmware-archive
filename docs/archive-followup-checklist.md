# Archive follow-up acceptance checklist

This tracks the follow-up scope for [PR #26](https://github.com/BookCatKid/sonos-firmware-archive/pull/26).
Related issues: [#23](https://github.com/BookCatKid/sonos-firmware-archive/issues/23),
[#24](https://github.com/BookCatKid/sonos-firmware-archive/issues/24),
[#25](https://github.com/BookCatKid/sonos-firmware-archive/issues/25).
Issue references do not authorize comments or closure; all issues remain open.

Existing tests and validation runs are starting evidence. Each requirement
needs evidence at its stated scope before it can be marked complete. Payload
acquisition and private-key handling stay in CI; local tests use synthetic data.

| Item | Acceptance evidence required | Follow-up status |
| --- | --- | --- |
| 1 | CI publishes the mutable ConnectX source and verifies its catalog asset. | First-capture ordering fixed; live validation running. |
| 2 | Published Anacapa, other extractable components and an architecture/version/provenance inventory, without fixed offsets. | Pending live source and extraction evidence. |
| 3 | Independent historical ConnectX discovery, including distinct captures at reused URLs. | Audit methods and live coverage. |
| 4 | Every installer reported in #25 is compared with independently discovered candidates; omissions have discovery fixes. | Pending comparison. |
| 5 | Reproduced TLS failure has a working verified transport; CLI clearly identifies a JSON output file. | CLI and certificate-verified TLS fallback fixed; offline and PR CI checks pass. |
| 6 | Independently found GPL indexes and retrievable sources are published and stream-verified, including encoded plus signs. | Audit backlog and encoding paths. |
| 7 | Historical index TLS, pagination, timeout, query-resume and replay behavior is verified; independent alternatives investigated. | TLS fallback verified offline; continue live index and replay investigation. |
| 8 | Anonymous Google Play acquisition yields a signer-verified delivery through goopdl. | CI isolates HTTP 403 at Aurora's token dispenser, before Play API calls; compare transports and playpull protocol paths. |
| 9 | APKPure pagination, SDK/ABI variants, refreshed tickets and expired links continue making historical progress. | Extend existing live evidence and audit coverage. |
| 10 | All advertised Android delivery components are preserved, with signatures, version checks and upload recovery. | Audit SDK coverage and live receipts. |
| 11 | Current and historical official mobile captures, including reused URLs, are independently discovered and preserved. | Audit live backlog. |
| 12 | Public web resources are preserved; redirects and authentication requirements are classified accurately. | Investigate remaining resources and redirects. |
| 13 | Apple listing/history coverage is verified; IPA scope and authorization prerequisites are explicit. | Audit metadata and binary acquisition prerequisites. |
| 14 | Unsupported firmware and missing-key cases are investigated; decoder/key improvements automatically trigger revisits. | Audit formats, recovery methods and live ledgers. |
| 15 | Ranges, pagination, parsers, traversal and budgets impose no unintended lifetime discovery ceilings. | Audit source adapters and worker checkpoints. |
| 16 | 429/backoff, expired URLs, host failures and mixed retry/fresh queues preserve progress without starvation. | Mutable-root ordering fixed; audit remaining queues. |
| 17 | Existing publication references are reconciled; unrecorded assets and inconsistent metadata are investigated without deleting evidence. | Repair older references and investigate page snapshot. |
| 18 | Interrupted publication, lost responses, conflicting names and changed content preserve successful work. | Document reconciliation tests cover fresh per-run inventories, corruption, outages and registration after earlier failures; continue other worker acceptance checks. |
| 19 | Every target supports cold startup and migration, missing caches/tools, concurrent updates and worker restarts. | Extend per-target acceptance evidence. |
| 20 | Obsolete entrypoints, duplicated logic and stale docs are reconciled; target registration requires no payloads or hashes. | ConnectX runtime documentation corrected; continue repository audit. |
| 21 | After merge approval, the unified workflow runs successfully against the existing production archive. | Requires approved deployment after fixes and validation. |
| 22 | Requirements in the three reported issues and automated reports are checked against published evidence. | Link from PR only; do not comment on or close issues. |

Live validation of mutable-root acquisition:
[run 44198556575](https://github.com/BookCatKid/sonos-archive-bootstrap-validation/actions/runs/44198556575).
The acquisition regression checks that a mutable root is captured before the
historical backlog and that the historical candidate is processed next.
