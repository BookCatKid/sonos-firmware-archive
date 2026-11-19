# Maintenance tools

Most archive work is automatic: one scheduled workflow reads
[`config/targets.json`](../config/targets.json) and runs each target's provider.
The scripts below are **not** on that path. They are manual maintenance and
recovery commands for a human operator, kept because they answer questions or
repair state that an automated worker cannot.

Nothing here is required to add a target, start from an empty archive, or run a
normal cycle. `scripts/audit_repository_consistency.py` treats a script as
accounted for when it is wired into a provider, a workflow or another module, or
documented in this file, so a new orphan is reported rather than forgotten.

## Consistency and coverage reporting

| Script | Purpose |
| --- | --- |
| `scripts/audit_repository_consistency.py` | Check provider/target correspondence, target reachability, orphan scripts, documentation paths and retired workflow references. Run before a release. |
| `scripts/audit_connectx_coverage.py` | Report ConnectX (model 17) historical coverage: preserved versus withdrawn contents, distinctness and manifest attribution. Also runs as the last firmware step. |
| `scripts/audit_gpl_link_coverage.py` | Require every advertised GPL link to be published or to carry a recorded terminal state. Also runs first in the `gpl-audit` target. |
| `scripts/audit_gpl_published_streams.py` | Reacquire published GPL sources and validate the entire compressed stream against the immutable receipt. |
| `scripts/audit_gpl_legacy_provenance.py` | Compare legacy GPL publications with direct-source provenance. |
| `scripts/audit_release_assets.py` | Reconcile every recorded asset against GitHub Release metadata; splits duplicate from unexplained extras. Also runs as the `release-audit` target. |
| `scripts/audit_recovery_vault.py` | Verify hashes, permissions, fingerprints and ignore status of the private recovery vault. |
| `scripts/audit_missing_live.py` | Re-probe catalog entries marked `missing-cdn` without downloading them. |
| `scripts/audit_missing_wayback.py` | Check the Wayback CDX index for each package marked `missing-cdn`. |

## Receipt recovery

| Script | Purpose |
| --- | --- |
| `scripts/reconcile_web_release_assets.py` | Rebuild lost web-capture receipts from each published bundle's embedded `capture.json`, verified against Release size and SHA-256. Records superseded captures instead of dropping them. |
| `scripts/reconcile_gpl_release_assets.py` | Reconstruct GPL publication receipts from Release-side size and SHA-256 after an interrupted job. Never overwrites a conflicting recorded hash. |

## Historical imports

These register evidence that was obtained outside a normal run. They are
explicitly not runtime discovery inputs.

| Script | Purpose |
| --- | --- |
| `scripts/import_manifest.py` | Register one exact update-manifest snapshot with its source URL, provenance and release tag. |
| `scripts/import_discovery.py` | Merge a retained discovery receipt into the catalog and regenerate UPD manifests. |
| `scripts/import_evidence.py` | Import redacted firmware-evidence records into the catalog. |
| `scripts/import_local_artifact.py` | Import one exact artifact with explicit provenance into the catalog. |
| `scripts/import_raw_receipts.py` | Import extracted raw component receipts into the catalog. |
| `scripts/import_gpl_wayback.py` | Archive Sonos-hosted GPL index and artifact captures from Wayback. Historical importer, not the discovery engine. |

## Key recovery and decryption

| Script | Purpose |
| --- | --- |
| `scripts/prepare_model_keys.py` | Prepare ephemeral recipient keys in CI from tracked public recovery inputs. |
| `scripts/recover_model26_key.py` | Recover a model-26 recipient key from preserved public inputs. |
| `scripts/decrypt_pending_firmware.py` | Decrypt preserved sources whose recipient keys are now available. |
| `scripts/decrypt_recipient_batch.py` | Decrypt a specific recipient batch. |
| `scripts/refresh_key_recovery_ledger.py` | Refresh the key-recovery ledger from current preservation state. |
| `scripts/register_recovered_keys.py` | Record recovered keys with their provenance. |
| `scripts/generate_recovery_vault_inventory.py` | Inventory the private recovery vault. |

## Analysis and diagnosis

| Script | Purpose |
| --- | --- |
| `scripts/analyze_gpl_firmware.py` | Analyze preserved GPL firmware contents. |
| `scripts/diff_firmware_trees.py` | Compare two extracted firmware trees. |
| `scripts/diff_upd_manifests.py` | Compare two UPD manifests. |
| `scripts/find_legacy_system_word.py` | Locate a legacy system word in a preserved image. |
| `scripts/extract_cramfs_little.py` | Extract a little-endian cramfs image. |
| `scripts/diagnose_smapi_playback.py` | Diagnose SMAPI playback behavior. |
| `scripts/test_legacy_flash_prefixes.py` | Probe legacy flash prefix handling. |
| `scripts/generate_metadata.py` | Regenerate per-package UPD manifests and extracted-rootfs inventories. |

## Publication helpers

| Script | Purpose |
| --- | --- |
| `scripts/upload_firmware_release.py` | Resumably upload a firmware discovery receipt to an existing Release. Built on the shared `verified_asset`/`publish_verified` publisher, so it performs the same size and SHA-256 reconciliation as the workers. |
| `scripts/upload_raw_assets.py` | Upload raw component assets that already have receipts. |
| `scripts/archive_android_store_apps.py` | Manual Android store acquisition, sharing the workers' signature and manifest inspection. |
| `scripts/discover_google_play_listings.py` | Metadata-only Google Play listing discovery. |

## Historical note

`docs/archive-platform-runtime-audit.md` is a snapshot of the state **before**
workflow unification. Its links are pinned to commit `d34f6c8`, so they describe
the retired ten-workflow layout rather than the current tree. Only
`archive.yml` and `verify.yml` remain; every capability in the old workflows is
now a target in `config/targets.json`.
