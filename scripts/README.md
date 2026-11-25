# Archive entrypoints

GitHub Actions runs targets from `config/targets.json` through `sonos-archive`.
These folders hold the command-line entrypoints used by those targets and manual
maintenance commands. Shared implementation lives in `src/sonos_firmware/`.

| Folder | Purpose |
| --- | --- |
| `firmware/` | Firmware discovery, extraction, metadata and publication |
| `apps/` | Desktop, mobile, web and store discovery and acquisition |
| `gpl/` | Source discovery, acquisition and verification |
| `recovery/` | Recipient-key recovery and recovery evidence |
| `archive/` | Release reconciliation across archive categories |
| `ghidra/` | Firmware analysis helpers for Ghidra |

Run the existing offline checks with `python scripts/run_tests.py`. Tests are
organized by subsystem under `tests/`; shared sample data stays in `tests/fixtures/`.
