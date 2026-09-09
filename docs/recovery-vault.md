# Local recovery vault

The working tree may contain a private, Git-ignored `recovery-work/` directory.
It is the canonical local recovery vault and is deliberately excluded from Git
because it contains RSA private keys and sensitive decryptor intermediates.

The vault currently retains verified keys for models 1, 8, 9, 12, 16, and 17,
along with each exact `34.16-37101` updater input, recovery intermediates,
private provenance notes, and a SHA-256 inventory. The private README records
the exact source-package hash, updater hash, wrapper offset, system word,
recipient fingerprint, derivation construction, and reproduction command for
every key.

Audit it without printing key material:

```bash
python scripts/audit_recovery_vault.py
```

That command verifies every inventoried SHA-256 hash, all six public-key
recipient fingerprints, restrictive file permissions, and Git ignore status.

The custom recovery workflow is preserved in tracked source:

- `src/sonos_firmware/legacy.py` implements the complete legacy wrapper
  recovery construction;
- `scripts/find_legacy_system_word.py` reproduces the bounded search that
  discovered the unique model 1 and model 12 parameters;
- `scripts/ghidra/` preserves the headless reference-following, decompilation,
  and instruction-listing helpers used to recover model 1's SH byte order;
- `scripts/decrypt_recipient_batch.py` performs resumable, hash-verified batch
  extraction while retaining only one downloaded source at a time;
- `scripts/extract_cramfs_little.py` preserves the little-endian CramFS
  extractor used to reach model 6's updater;
- `scripts/generate_recovery_vault_inventory.py` regenerates the complete
  private SHA-256 manifest, while `scripts/audit_recovery_vault.py` rejects
  missing, altered, or unlisted vault files;
- `scripts/register_recovered_keys.py`, `scripts/import_raw_receipts.py`, and
  `scripts/upload_raw_assets.py` preserve ledger, catalog, and publication
  steps; and
- `tests/test_legacy.py` independently exercises the full wrapper round trip.

The ignored vault is not included in Git clones or GitHub backups. A second
local copy of the PEMs exists under the adjacent firmware-download workspace,
but both copies are on the same physical machine. Back up `recovery-work/` to
encrypted offline storage to protect against disk loss.

The vault also contains a `candidates/` subtree with every model 1, 5, 6, and
7 updater used in recovery work, the tested model-5 flash
reference, hashes, and the present reverse-engineering conclusions.
