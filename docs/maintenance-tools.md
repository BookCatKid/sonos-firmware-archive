# Maintenance tools

Normal acquisition runs through the scheduled targets in
[`config/targets.json`](../config/targets.json). These commands support those
workers or provide reusable repair and analysis tools. A fresh archive needs no
manual imports or pre-collected payloads.

## Worker entrypoints

[`scripts/README.md`](../scripts/README.md) describes the folder layout.
[`providers.py`](../src/sonos_firmware/platform/providers.py) lists the exact
commands and options used by each target.

| Command | Role |
| --- | --- |
| `scripts/archive/audit_release_assets.py` | Reconcile Release metadata and recover attributable document publications |
| `scripts/gpl/audit_gpl_published_streams.py` | Revalidate original GPL source streams |
| `scripts/gpl/audit_gpl_link_coverage.py` | Report unresolved advertised GPL links after verification |
| `scripts/recovery/prepare_model_keys.py` | Prepare ephemeral recipient keys from verified evidence |
| `scripts/recovery/refresh_key_recovery_ledger.py` | Refresh recipient coverage |
| `scripts/firmware/decrypt_pending_firmware.py` | Extract preserved sources with available keys |
| `scripts/firmware/import_raw_receipts.py` | Register staged component receipts during verified publication |
| `scripts/firmware/upload_raw_assets.py` | Publish and verify extracted components |

The raw-component registration command is part of a worker transaction; running
it alone does not prove that its assets have been published.

## Repair and analysis

| Command | Purpose |
| --- | --- |
| `scripts/apps/reconcile_web_release_assets.py` | Recover lost web receipts from verified published bundles |
| `scripts/gpl/reconcile_gpl_release_assets.py` | Recover GPL publication receipts without replacing conflicting hashes |
| `scripts/gpl/analyze_gpl_firmware.py` | Compare firmware evidence with published source components |
| `scripts/firmware/diff_firmware_trees.py` | Compare extracted file trees by content |
| `scripts/firmware/diff_upd_manifests.py` | Compare UPD section manifests |
| `scripts/firmware/extract_cramfs_little.py` | Extract supported CramFS images |
| `scripts/apps/diagnose_smapi_playback.py` | Diagnose SMAPI playback |
| `scripts/recovery/find_legacy_system_word.py` | Search legacy wrapper parameters |
| `scripts/recovery/test_legacy_flash_prefixes.py` | Test candidate legacy flash prefixes |
| `scripts/recovery/register_recovered_keys.py` | Record recovered-key provenance |
| `scripts/recovery/generate_recovery_vault_inventory.py` | Inventory private recovery storage |
| `scripts/recovery/audit_recovery_vault.py` | Check private recovery storage integrity and permissions |
| `scripts/ghidra/` | Firmware decompilation and instruction-analysis helpers |

Use `--help` for command arguments. Live acquisition belongs in Actions;
local inspection can use synthetic fixtures and separately available analysis
inputs. Private keys and device captures stay outside Git.
