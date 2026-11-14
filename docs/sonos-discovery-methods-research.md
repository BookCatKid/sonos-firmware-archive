# Sonos discovery methods: algorithms, provenance, and limits

Research date: **2026-11-13 UTC**. Scope: independent public Sonos discovery algorithms, the provenance of historical manifests, and limits imposed by unreferenced opaque campaign paths.

## Findings relevant to implementation

**Porting the traversal algorithm is justified; importing the collection is not necessary for current endpoint discovery. Neither approach establishes discovery of every historical campaign.** Public software-generation bootstrap endpoints can expose a current canonical manifest without knowing its opaque campaign identifier. Recursively following image-directory siblings and header locations can discover additional manifests. Historical directories absent from that reachable graph require another provenance source or a demonstrably effective additional protocol profile; a numeric build label alone supplies no opaque directory token.

The important distinctions are:

| Mechanism | What the primary evidence establishes | What it does not establish |
| --- | --- | --- |
| Anonymous software-generation bootstrap | GET the public `swgen/1` and `swgen/2` manifest routes; record canonical `Content-Location` and parse the returned XML. | A complete historical index, or arbitrary generation support. |
| Legacy UPS bootstrap | A synthetic request returns typed records containing a base URI and manifest URI. | That changing the supplied current version requests a specific historic release. |
| Recursive manifest traversal | Follow literal URLs and derive `update.upm` in already observed package directories; optionally probe the evidenced Dev/Prod counterpart. | Discovery of an opaque directory that no visited response references. |
| Saved local manifest corpus | Its URLs create additional traversal roots, including historical roots. | Independent rediscovery of how that initial corpus was acquired. |
| Known URL/release utilities | Reference lists and filename-derived metadata exist in the external repository. | That these files are the crawler's input or an algorithm for deriving unknown campaign tokens. |
| Passive device/update-log evidence | A real client can expose an exact update URL/profile that generic public requests may not reveal. | Permission to scan unrelated devices, or a universal account-free history endpoint. |
| Repeated scheduled capture | New response bytes can be retained while public mutable URLs change. | Recovery of overwritten old bodies after the fact. |

## How ReverseTelnet's collection was obtained: what is documented

### The historical crawler is algorithmic, but starts with a corpus

The initial populated [crawler at `d0a1133035eb1af83dc565495ecb7c8cafb9fdaf`](https://github.com/ReverseTelnet/sonos_update_manifests/blob/d0a1133035eb1af83dc565495ecb7c8cafb9fdaf/check_for_new_manifest_files.py) documents its queue algorithm in lines 11–29. Its actual implementation starts with **eight fixed bootstrap requests plus URLs extracted from saved `.upm`/`.xml` files**. Those requests combine generations 1 and 2 with ordinary, stage, performance, and test update hosts. It then:

1. Parses absolute `base_url` and image URLs from existing manifests.
2. Normalizes an observed image directory to a sibling `update.upm` request.
3. Adds Dev/Prod substitutions for observed URLs.
4. GETs queued URLs, following response `Content-Location` into the queue.
5. Saves newly observed signature variants, parses their links, and repeats with an already-visited set.

These are demonstrated in the historical source's [link parser](https://github.com/ReverseTelnet/sonos_update_manifests/blob/d0a1133035eb1af83dc565495ecb7c8cafb9fdaf/check_for_new_manifest_files.py#L434-L480), [fixed roots and local-corpus processing](https://github.com/ReverseTelnet/sonos_update_manifests/blob/d0a1133035eb1af83dc565495ecb7c8cafb9fdaf/check_for_new_manifest_files.py#L581-L623), and [response/queue processing](https://github.com/ReverseTelnet/sonos_update_manifests/blob/d0a1133035eb1af83dc565495ecb7c8cafb9fdaf/check_for_new_manifest_files.py#L696-L820).

This implementation **does not import `known_manifest_urls`, `known_release_versions`, or `known_manifest_release_versions`**. The relevant precomputed input it does import is `known_404_url_list`, used to skip requests. It has fixed client tuples for its generation roots, not a controller-version/hardware/submodel/region exploration algorithm. Its `timeout=5`, request headers, signature-derived filenames, and negative skip list are implementation choices, not protocol requirements. In particular, porting permanent negative skips would suppress later revivals; a historical miss should remain a dated observation rather than a permanent exclusion.

This assessment is pinned to the historical implementation, not a duplicate review of the current implementation.

### The reference utilities do not explain initial acquisition

The empty `known_manifest_urls.py` placeholder first appears in [commit `c44baf6`](https://github.com/ReverseTelnet/sonos_update_manifests/commit/c44baf6ab36b528f4cf8e33cee12fd278e348470). The populated file appears at [commit `3a81f0f`](https://github.com/ReverseTelnet/sonos_update_manifests/blob/3a81f0f9d4f0635b1e5c4a27f57741ccb2f3e93c/utilities/known_manifest_urls.py#L3-L18). Its author describes the list as a search-engine discoverability aid and a quick reference. It contains complete historical opaque paths but provides no per-entry acquisition transcript or source attribution. These strings are explicitly supplied reference data; their presence is not a derivation method. It also includes staging/test routes and a `QAtest` `FakeRelease1`, so it is not a verified production-release index.

The populated [known-release utility at `d272d3f`](https://github.com/ReverseTelnet/sonos_update_manifests/blob/d272d3f8acbd790261952af0852edd85b99a39ea/utilities/known_release_versions.py#L19-L26) explicitly describes how it was generated: enumerate local `.upm`/`.xml` files, split each filename before `__`, and sort the results. This list is **derived from already acquired files**, not manually guessed release tokens and not an independent discovery source. Calling every list in the project “hardcoded discovery” would therefore be inaccurate.

The [initial project README](https://github.com/ReverseTelnet/sonos_update_manifests/blob/318415f13816801a7297a6fe647ab70f81c1ca9f/README.md) describes a daily update workflow, but does not document where each initial historical manifest was found. It describes the collection and how to use its contents; it does not provide a recipe reconstructing all historical opaque identifiers from a public Sonos root.

### Commit and issue history does not fill that gap

The public reachable history contained **202 commits**, obtained with three `per_page=100` requests (100, 100, 2), and two branches pointing to the same tip `5583630e7e6723a434bdc9e58fa6fd04b352bd65`. The issue listing contained only four pull requests, with no ordinary issues. Each PR's issue-comment listing returned zero comments. [Commit history](https://github.com/ReverseTelnet/sonos_update_manifests/commits/master), [pull requests](https://github.com/ReverseTelnet/sonos_update_manifests/pulls?q=is%3Apr+is%3Aclosed).

History records many initial manifest additions together, then later repeated observations at existing release paths. Their commit timestamps establish repository insertion history, not when Sonos originally published the bytes or how the author discovered the path. PRs [#1](https://github.com/ReverseTelnet/sonos_update_manifests/pull/1), [#2](https://github.com/ReverseTelnet/sonos_update_manifests/pull/2), and [#3](https://github.com/ReverseTelnet/sonos_update_manifests/pull/3) list added manifest/version/signature observations. [PR #4](https://github.com/ReverseTelnet/sonos_update_manifests/pull/4) lists `75.1-46030`, `73.0-41050`, and two `57.18-44230` variants, without an acquisition transcript. Its [individual manifest addition](https://github.com/ReverseTelnet/sonos_update_manifests/commit/84331227c7a4262d58fdf637c1b753e1f4dcb374) adds the file itself, not an independent URL-discovery method.

**Conclusion on “manually supplied”:** fixed protocol roots, a pre-existing saved corpus, and explicitly written reference URLs are visible inputs. The original acquisition of individual historical files is **undocumented in the examined public history**. Neither a human-looking commit author nor an autogenerated PR description proves whether a specific file was manually downloaded or discovered by a prior automated run. The provenance gap should remain explicit.

## Independent protocol evidence and usable algorithms

### 1. Discover software-generation roots from first-party client material

ReverseTelnet documented the string `/firmware/swgen/%u/latest/` in Windows `sclib-csharp.dll`, the macOS Sonos executable, Android `libsetup_sdk.so`, and iOS client material. This supplies a method for deriving the route from distributed Sonos clients instead of importing historical campaigns. Their original anonymous request transcript shows the corresponding XML endpoint, canonical location, and generation header. Treat the “all binaries” breadth as the researcher's report; this sidecar did not independently inspect every client. [Original binary-string investigation](https://github.com/Homebrew/homebrew-cask/issues/285208#issuecomment-5921042829), [request transcript](https://github.com/Homebrew/homebrew-cask/issues/285208#issuecomment-5644495696).

The operational modern request is:

```text
https://update.sonos.com/firmware/swgen/2/latest/update.upm
?sonosid=Anonymous&householdid=nohhid&cmaj=81&cmin=0&cbld=52
```

The path contains the software generation. These are public placeholder identity values, not a captured customer's identifiers. Generation 1 is a separately demonstrated route; unsupported generation numbers must not be treated as discovered channels merely because a path can be formatted.

**Algorithm:** record endpoint provenance from static client material or a primary protocol transcript; issue bounded GETs for each demonstrated generation; retain request profile, headers, raw body, hash and time; follow same-origin/approved-origin canonical locations; parse the manifest into further evidence. A canonical path from `Content-Location` is a response observation and should retain its producing request as provenance. An absent canonical header is an incomplete observation, not a reason to manufacture a campaign identifier.

There is independent operational confirmation: Homebrew's merged [PR #290872](https://github.com/Homebrew/homebrew-cask/pull/290872) replaced a blocked redirect-based check with this manifest endpoint. Its [commit-pinned cask implementation](https://github.com/Homebrew/homebrew-cask/blob/03bd766728a74263062d60ba4e7a1cd684ea2891/Casks/s/sonos.rb#L10-L22) selects macOS model-4 entries without submodel/from-version restrictions. That is a concrete current-installer discovery algorithm, not a complete player/history enumerator.

### 2. Retain the separate legacy binary bootstrap

The original [blasty `sonostool` constants](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L47-L55) and [request implementation](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L182-L216) demonstrate:

```text
https://update.sonos.com/firmware/latest/default-1-1.ups
?cmaj=4&cmin=1&cbld=1&subm=100&rev=1&reg=2
&serial=1&sonosid=111111111&householdid=X
```

**Algorithm:** parse the returned stream as successive 16-byte little-endian `(magic, type, total_length, unknown)` headers with magic `0x35167F49`; validate lengths and preserve the raw response. Type 7 supplies a base URI and type 14 a manifest URI. Enqueue those URI observations with their source profile rather than assuming the binary response is itself an XML manifest. [Original parser](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L132-L151).

The source then expands a caret base for a fixed target, submodel/variant `1`, package model `26`. Its query uses `subm=100`: those values are visibly different. Do not substitute the query's `subm` directly into the package filename. The original code demonstrates one working profile and target, not a generic hardware matrix or parameter sweep. Extracting the manifest URI is useful even though that original command does not fetch the manifest itself.

### 3. Separate current-client parameters from target versions

The direct device investigation shows a player on `76.2-47270` appending `cmaj=76`, `cmin=2`, and `cbld=47270` while requesting a destination advertised as `82.3-60197`. Thus these fields describe the requester's current internal version in that updater; they are not an instruction to return the identically numbered target release. The controller route likewise accepts the older synthetic `81,0,52` tuple while returning newer manifests. Use “current client version,” allowing for a controller or player, rather than claiming an exclusively player-firmware meaning. [Original updater log](https://github.com/trulyspinach/sonos-fenway/issues/2), [Homebrew client implementation](https://github.com/Homebrew/homebrew-cask/blob/03bd766728a74263062d60ba4e7a1cd684ea2891/Casks/s/sonos.rb#L10-L22).

`subm`, `rev`, and `reg` occur in device requests and compatibility evidence, but their presence does not establish that changing each one independently selects an otherwise hidden historical release. Nor is a retail product number automatically the `.upd` package-model ID.

### 4. Fresh parameter-ablation evidence

The background researcher made a small set of bounded **GET** requests during this session, completed before **2026-11-13 18:53:10 UTC**. Individual timestamps were not recorded. Successful responses were read with a 100,000-byte cap and hashed; no firmware payload was fetched for these tests. The baseline is the complete anonymous generation-2 request printed above, including `householdid`.

| Single change from the baseline | Result |
| --- | --- |
| Remove `sonosid` | 404 |
| Remove `householdid` | 200; identical body SHA-256 |
| Remove `cmaj` | 404 |
| Remove `cmin` | 404 |
| Remove `cbld` | 404 |
| Remove the entire query | 404 |
| Replace client version with `90.0-82050` | 200; identical body SHA-256 |
| Replace client version with `98.0-82170`, add `subm=1&rev=1&reg=2` | 200; identical body SHA-256 |
| Change path generation from 2 to 1 | 200; S1 manifest |

Baseline and successful S2 variants returned **13,841 bytes**, SHA-256 `6da0eec0c2efdcadf729e95281bae7053d7b0c21b1de060c60a2f9bb4c768220`. The response identified the [official current canonical manifest](http://update.sonos.com/firmware/Prod/2026-Sonos-22-RhFftbkg3y-SP-1/update.upm), whose system version is `98.0-82170` with desktop `90.0-82050` and legacy-player `86.11-82170` branches. Generation 1 identified the [official S1 manifest](http://update.sonos.com/firmware/Prod/57.23-80060-v11.16.2-9tmT45qD4t-SP-1/update.upm), with distinct `system_version=57.23-80060` and `default_version=57.23-74170`.

These single-request observations demonstrate a presently working anonymous shape and some omission failures. They are **not a universal parameter specification**, do not distinguish independent effects of the jointly added hardware fields, and do not prove that low/old versions or other profiles can never expose a different response. An incomplete request returning 404 does not establish authentication requirements. Keep the full demonstrated profile as a stable probe while recording ablation results separately.

The repository's earlier [1,200-profile research](research-sources.md) reported three distinct legacy UPS responses, including low/invalid-version fallbacks. Its later [channel note](update-channel-enum-2026-09-21.md) reports a sampled byte-identical S1 response and the anonymous S2 correction. These are dated, local measurements of different requests/endpoints, not a published Sonos selection contract. Their apparent breadth must not be converted into an unsupported general rule about how today's S2 router selects historical releases.

### 5. Traverse the manifest graph, preserving selection semantics

**Algorithm:** for each newly observed body, retain exact bytes and parse every image rule plus the root's default/base and supported-model lists. Follow actual manifest links and canonical headers. For every absolute package expression or installer URL, derive only its literal directory's sibling `update.upm`; enqueue it with the observed URL as the parent edge. Preserve case, tokens, and original schemes. Dev/Prod substitution is a separately labeled inference using an observed directory token, not a literal link and not proof that the counterpart exists. The historical crawler supplies this traversal precedent; the [official current XML](http://update.sonos.com/firmware/Prod/2026-Sonos-22-RhFftbkg3y-SP-1/update.upm) supplies the actual current graph data.

For artifact enumeration, preserve `model`, submodel/revision ranges, `fromver_min`, `fromver_max`, `flags`, `milestone_index`, software generation, and store/link distinctions. Expand `^version` only using the evidenced UPD filename rule and applicable numeric models; retain literal `.exe`, `.dmg`, `.bin`, accessory and store expressions as their own kinds. Default membership should use the matching software-generation list, not the union of every generation. Explicit cross-generation compatibility rules still need their own records. A manifest's system version, default version, and each image version are separate fields. [Original expansion code](https://github.com/blasty/sonos/blob/27be43832f6106d068541534ed0f151b841d1fa4/sonostool/sonostool.py#L207-L216), [official manifest](http://update.sonos.com/firmware/Prod/2026-Sonos-22-RhFftbkg3y-SP-1/update.upm).

Resolve relative references using the evidenced original Sonos manifest context, not a research mirror's GitHub raw URL. When only a mirrored body is known and its source origin cannot be recovered, retain an unresolved expression. In the historical crawler, a caret-only `base_url` is stripped to a version-like string rather than converted into an invented absolute URI. [Historical normalization notes](https://github.com/ReverseTelnet/sonos_update_manifests/blob/d0a1133035eb1af83dc565495ecb7c8cafb9fdaf/check_for_new_manifest_files.py#L303-L328).

### 6. Discover additional roots without copying a historical registry

The following are implementable acquisition methods, not instructions to import another curated candidate list:

| Method | Evidence to retain | Constraint |
| --- | --- | --- |
| Static inspection of distributed Sonos controller/player material | Artifact hash, exact string offset or code reference, endpoint template, observed parameter construction. | A route string proves the template's presence, not the required parameters or a server's current behavior. Validate with bounded public requests. |
| Authorized local device events or existing diagnostic output | Minimal `AvailableSoftwareUpdate` fields: version, update URI, manifest URI, revision and generation; strip customer identifiers. | Discovers the device's exposed selection, not every backend rollout cohort. |
| Existing local upgrade logs | Exact URL/path and current-version/hardware fields, with identifying query values removed. | Logs can be old, malformed, private or installation-specific; validate official URLs independently. |
| First-party desktop redirect metadata and installer history | Official redirect/location, installer URL, source timestamp and embedded update references. | Current redirects reveal current installer channels; older entries still require observed historical evidence. Do not derive an opaque firmware campaign from a build number alone. |
| Public archive indexes queried by official host/path patterns | Original indexed URI, capture timestamp/status/digest and query coverage. | An index search is an independent discovery method; replay may fail and missing entries do not prove nonexistence. No exhaustive public Sonos directory index is established here. |

The [observed ZoneGroupTopology interface](https://sonos.svrooij.io/services/zone-group-topology) is generated from actual service-discovery data: it exposes `AvailableSoftwareUpdate` as an event variable, and `CheckForUpdate` returns an `UpdateItem` with `UpdateType`, `CachedOnly`, and `Version` inputs. This documents an additional local acquisition surface; this sidecar did not call it or trigger an update. Device-specific state is a separate source from the anonymous public roots.

The pinned [SonOpen investigation](https://github.com/turbotas/SonOpen/blob/69477b61e2c2f718c41affff23ebf9535752dd8f/ReverseEngineeringEfforts.md) documents local diagnostic URLs but adds no demonstrated public profile-enumeration algorithm; it even lists upgrade-log routes among attempted URLs that did not work. Local diagnostic paths must therefore be treated as firmware-dependent observations, not a universal read API.

A **proposed bounded profile-exploration method**, not an established Sonos historical selector, is to derive numeric client versions and compatibility boundaries from newly fetched first-party manifests/artifacts; test representative version tuples and boundary-adjacent tuples while holding identities anonymous; vary independently evidenced hardware fields one axis at a time; and deduplicate by body hash while retaining every tested profile and result. Prefer adaptive exploration when a response actually changes, rather than a Cartesian product of all integers. Report this as exploration coverage, not successful historical enumeration. The original blasty request and this session's ablation show how to issue/compare profiles; they do not prove that this strategy can recover all missing manifests.

## Which historical URLs are not explained by a reproducible public-root path?

No examined source supplies a complete derivation of the initial historical corpus from anonymous Sonos endpoints. It would be equally unsupported to assert that every URL in the corpus is impossible to rediscover from any public profile. The defensible distinction is **observed reachability versus missing acquisition provenance**.

A concrete local-graph check inspected the 147 previously retained original bodies. After excluding each manifest's own release directory, these four directory labels had **zero literal incoming references from a different release directory** in that corpus:

- `57.18-44230-v11.11-rdiahrpn-GA-1`
- `63.2-90210-v13.1.4-jaffit-GA-1`
- `73.0-41050-v15.4-iwkvpnqy-GA-1`
- `75.1-46030-v15.9-irbxsaud-GA-1`

The check extracted absolute `firmware/Prod/` and `firmware/Dev/` directory references and compared them with each saved filename's release prefix. It is deliberately a bounded literal-reference check, not a model of every header, sibling, host substitution or future server response. Its inputs are the hashed original files listed in [the additional-research ledger](../data/discovery/connectx-additional-candidates.json), pinned to [collection commit `5583630`](https://github.com/ReverseTelnet/sonos_update_manifests/tree/5583630e7e6723a434bdc9e58fa6fd04b352bd65).

This explains why seeding a saved corpus can add coverage that a current-only crawl misses. For comparison, `57.18-45134` has literal incoming references in two other saved manifests, and `78.1-51030` is referenced by the retained `78.1-51070` manifest. Those have concrete graph edges once their referring roots are acquired. Zero incoming edges within the corpus does **not** establish global backend unreachability, nor does adding a URL to an automated seed list solve its original-provenance gap.

Overwritten **bytes** are a different limit from undiscovered **URLs**. The collection's same-release signature variants and its historical filename notes demonstrate that a mutable URL can serve multiple bodies over time. Today's successful request cannot recover a discarded older body unless another still-accessible snapshot or versioned URL preserves it. A scheduler that captures changes independently is therefore a preservation method; replaying a current URL is not historical reconstruction. [Historical filename rationale](https://github.com/ReverseTelnet/sonos_update_manifests/blob/d0a1133035eb1af83dc565495ecb7c8cafb9fdaf/check_for_new_manifest_files.py#L33-L50).

## Concrete requirements for an independent implementation

1. Start with **protocol-derived bootstrap templates and demonstrated anonymous profiles**, not another project's opaque campaign registry. Give each root an acquisition source and distinguish production from staging/test observations.
2. Record a provenance edge for every discovered URI: response location, XML reference, literal sibling inference, Dev/Prod inference, static-artifact extraction, device observation, or archive-index result. Record URL/hash/time independently because URLs and bodies are not one-to-one.
3. Traverse both generation manifests and all compatibility branches. Deduplicate fetch work by normalized request identity and body work by complete SHA-256, while retaining every source observation. A copied signature string is a useful change marker, not proof of signature verification.
4. Use request and byte budgets, resumable queues, response caps and dated negatives. Record blocked requests, parse failures and exhausted budgets as incomplete work. A successful finite traversal means the measured graph is exhausted, not the historical namespace.
5. Separate profile research from dependable monitoring. Keep known-good complete requests; add narrowly scoped profile experiments with explicit hypotheses and observed response differences. Do not advertise hardware/region enumeration as effective without those differences.
6. Persist newly observed originals immediately and monitor changing roots periodically. Report independently recovered history separately from imported snapshots or manually supplied evidence.

An accurate completion statement is: **all artifact references in the independently observed, successfully traversed public manifest graph are accounted for, with explicit discovery/profile/index limits**. The reviewed primary sources do not justify “everything Sonos ever released” or an algorithm guaranteed to enumerate every unreferenced historical opaque URL.
