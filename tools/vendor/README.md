# Vendored acquisition and verification tools

These dependencies are kept in the repository so the application archive does
not depend on whichever tools happen to remain installed on a workstation.

| File | Upstream | Version | SHA-256 | License |
| --- | --- | --- | --- | --- |
| `apksig-9.4.1.jar` | Google Android Maven | `9.4.1` | recorded in `SHA256SUMS` | Apache-2.0 |
| `apktool-3.0.3.jar` | `https://github.com/iBotPeaches/Apktool` | `v3.0.3` | recorded in `SHA256SUMS` | Apache-2.0 |

CI installs the upstream **apkeep 1.0.0 release binary** with
[`tools/install-apkeep`](../install-apkeep). Linux x86-64 and ARM64 digests are
pinned from the [upstream release](https://github.com/EFForg/apkeep/releases/tag/1.0.0).
There is no Rust compilation step. Apkeep supplies APKPure history and downloads.

Google Play uses **goopdl**, pinned to a source revision in the `google-play`
Python extra. Its adapter explicitly selects anonymous authentication and keeps
tokens in the ephemeral worker, outside discovery caches and uploaded metadata.

Google's `apksig` validates every APK signature and exposes the signer
certificate used as the archive's provenance anchor. `apktool` decodes package
identity and version metadata before publication.
