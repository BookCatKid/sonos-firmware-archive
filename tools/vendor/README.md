# Vendored acquisition and verification tools

These dependencies are kept in the repository so the application archive does
not depend on whichever tools happen to remain installed on a workstation.

| File | Upstream | Version | SHA-256 | License |
| --- | --- | --- | --- | --- |
| `apkeep-1.0.0.tar.gz` | `https://github.com/EFForg/apkeep` | tag `1.0.0` | recorded in `SHA256SUMS` | MIT |
| `apksig-9.4.1.jar` | Google Android Maven | `9.4.1` | recorded in `SHA256SUMS` | Apache-2.0 |
| `apktool-3.0.3.jar` | `https://github.com/iBotPeaches/Apktool` | `v3.0.3` | recorded in `SHA256SUMS` | Apache-2.0 |

`apkeep` performs authenticated Google Play downloads and preserves split APK
sets. Google's `apksig` validates APK signing schemes and exposes the signer
certificate used as the archive's provenance anchor.
`apktool` is retained because it was used to inspect Fire OS manifest device
features while distinguishing Fire tablet packages from Fire TV applications.
