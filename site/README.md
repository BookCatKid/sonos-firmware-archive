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

`site/archive-data.js` is generated and ignored by Git. GitHub Pages rebuilds
it from the committed archive metadata when `main` is deployed.

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
- **Timeline** — dated observations and captures. Evidence dates remain
  evidence dates; the viewer does not invent release dates.
- **Firmware matrix** — exact firmware version × package-model coverage.
- **Compare** — normalized field/provenance differences for any two records,
  plus UPD-section, extracted-filesystem, Web-resource, and Android-component
  diffs when those structural inputs exist.
- **Sources** — provenance rollup.
- **Key coverage** — published recovery coverage only; private key material is
  never included.
- **Record detail** — hashes, exact sizes, release/source links, child records,
  UPD sections, filesystem paths, release notes, and raw normalized metadata.

Every view is hash-routed and deep-linkable. The compare tray is stored only in
the browser's local storage.

## Data rules

The viewer does not use AI classification. It does not infer an unknown artifact
as preserved, and it never treats a nearby version as a substitute for an exact
missing version. Ciphertext/section differences are labeled as structural
differences, not functional source-code differences.
