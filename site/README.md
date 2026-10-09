# Sonos Archive Explorer

This directory contains a dependency-free static browser for the archive.

The UI is intentionally plain. Its job is to make the archive auditable rather
than decorative: exact preserved artifacts, explicit gaps, provenance,
coverage, hashes, structural manifests, and diffs stay visible.

## Build

From the repository root:

```fish
.venv/bin/python scripts/build_site.py
python3 -m http.server 8000 --directory site
```

Then open `http://127.0.0.1:8000`.

`site/archive-data.js` and `site/app-diffs/` are generated and ignored by Git. GitHub Pages rebuilds the compact index and copies committed semantic reports from `data/app-diffs/` when `main` is deployed. Detailed app diffs are fetched only when opened, so historical backfill does not inflate the initial viewer download.

## Views

- **Overview** — archive totals, coverage states, categories, recent records,
  and input freshness.
- **Artifacts** — all normalized firmware, app, web, evidence, GPL/source,
  coverage, and key-status records with search, filters, sorting, pagination,
  JSON export, and CSV export.
- **Missing / gaps** — only explicit missing, partial, metadata-only, or blocked
  records.
- **Review queue** — live read-only view of open `[monitor]` GitHub issues, kept
  separate from preservation status so successful automation still receives human review.
- **Updates** — user-facing release history grouped by platform/family. Each release links directly to its details and, when a predecessor exists, a one-click adjacent-version diff. macOS releases use precomputed format-aware semantic bundle diffs when available; large reports load on demand rather than with the main archive index.
- **Timeline** — dated observations and captures. Evidence dates remain
  evidence dates; the viewer does not invent release dates.
- **Firmware matrix** — exact firmware version × package-model coverage plus decryption/extraction state. `D` means an encrypted source package was decrypted, `X` means a plaintext package was extracted, `E`/`B` mean encrypted/key-blocked, and an `F` corner marker means a root filesystem manifest is indexed. Failed CDN/model probes are hidden by default because they are negative evidence, not archive gaps.
- **Compare** — low-level arbitrary record comparison. Normal adjacent-release comparison should start from Updates, which chooses the pair automatically. Compare still provides normalized field/provenance differences plus UPD-section, extracted-filesystem, Web-resource, and Android-component diffs when those structural inputs exist.
- **Sources** — provenance rollup.
- **Key coverage** — published recovery coverage only; private key material is
  never included.
- **Record detail** — hashes, exact sizes, release/source links, child records,
  explicit firmware availability/decryption/extraction state, UPD sections,
  filesystem paths, release notes, and raw normalized metadata.

Every view is hash-routed and deep-linkable. The compare tray is stored only in
the browser's local storage.

## Data rules

The viewer does not use AI classification. Smart application diffs are deterministic and content-driven: files are identified by magic/schema/structure before falling back to names or extensions. Structured formats are decoded before comparison (for example plists/JSON/text/ZIP/SQLite/Mach-O metadata). Sonos desktop `libutils.so` is recognized as its actual AES-encrypted SQLite resource database, decrypted, and compared as translations, images, and JSON resources rather than as an opaque `.so` blob.

The viewer does not infer an unknown artifact as preserved, and it never treats a nearby version as a substitute for an exact missing version. A failed speculative CDN/model probe is retained as
`unavailable-probe`, not promoted to a missing exact artifact. Ciphertext/section
differences are labeled as structural
differences, not functional source-code differences.
