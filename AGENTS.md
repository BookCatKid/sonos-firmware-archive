# Agent rules for this repository

These rules are binding on every working session. They exist because each one
was violated or narrowly avoided at least once. Read them before changing
anything.

## Mission

Independently discover, acquire, verify, extract, categorize and publish as
much **official Sonos firmware and software** as possible: player firmware
(all models, S1/S2, historical and current, intermediate/compatibility
packages, all distribution channels), Windows/macOS desktop installers,
Android/iOS/Fire OS controller software, official web applications, and
Sonos's own GPL/LGPL source releases. Success is measured by what is
independently discovered and preserved, never by comparison to or copying of
any other archive.

## Sources

1. Preserve software supplied by Sonos itself or published by Sonos through
   its official store distribution. Mirrors (e.g. APKPure) are acceptable
   *acquisition* sources for genuine Sonos software when their true provenance
   and applicable hash/signer checks are recorded — but never label a mirror
   download as a direct Sonos download.
2. External metadata, package-manager entries, diagnostics and third-party
   research are **leads, never proof**. A filename containing "Sonos", a
   matching version label, or an external listing is not evidence of origin.
   Every preservation claim must bind to the actual source response and the
   verified captured bytes.
3. Never import another archive's manifest corpus, URL lists or version
   tables as runtime input. They may be read as research and used as
   offline comparison fixtures in ignored scratch only.
4. HTTP 200 or a successful HEAD is not byte preservation and not proof of
   distinct content. Do not claim a URL is unavailable merely because
   discovery has not found it.
5. HTTP 429 means retain the work and retry after the server's deadline.
   Shared service cooldowns apply across all query lanes of that service.
6. **Learn Sonos** (`id6747091626`) is explicitly excluded forever.
7. APKPure/apkeep acquisition is in scope but deprioritized; do not remove it
   and do not polish it further at the expense of discovery.

## Discovery posture

8. Discovery breadth is the point of the project. When a public source
   (repository, forum, diagnostic) contains native links we lack, investigate
   *how* that source obtained them and recreate the channel independently
   where possible — the canonical recreation being querying Sonos's update
   services the way each device class does.
9. Public-observation sources run continuously and broadly: global public
   issue search (bodies *and* comments), the community forum archive, release
   notes, package-manager metadata. No arbitrary repository exclusions; the
   protection against corpus contamination is that only native Sonos URLs are
   ever extracted and every one must be proven by an actual Sonos response.
10. Do not import community projects as archival targets; a diagnostic URL
    exposed by such a project is a lead for Sonos content only.
11. Negative results are durable knowledge. Do not blindly repeat disproven
    endpoint guesses in later sessions; record what was tried and what the
    server answered.

## Execution constraints

12. **Use the worker's resources.** No per-run item, batch, page, download,
    scan or step quotas. Continue eligible work until exhausted or the shared four-hour
    soft wind-down. Finish the active operation normally and checkpoint it;
    never terminate a running subprocess on that signal. Keep only protocol-imposed or structural
    limits, including per-release asset capacity, and real disk-space checks.
    Commit checkpointed progress after the worker returns. Hosted Actions
    itself imposes an external job runtime; four hours is not a hard kill.
13. **No repeated full-matrix validation runs.** Use focused local checks and,
    when CI proof is needed, one targeted validation deployment plus a single
    normal target dispatch. A complete end-to-end run is never a pre-merge
    ritual, and validation exists to prove machinery, not to gate discovery.
14. **Zero tolerated failures in worker receipts.** An inspection or decoder
    failure on a capture that cannot contribute discovery is removed by
    narrowing the work, not by accepting the red entry. Failures that remain
    must be real source or code faults worth fixing.
15. All real acquisition uses the unified `archive.yml` workflow and its
    registered providers. No diagnostic/backfill side workflows, no manual
    receipt imports, no pre-collected payload archives, no one-off
    backlog-draining prerequisites.
16. An empty archive must bootstrap from nothing. Existing Releases, prior
    catalogs and git history may supplement discovery but are optional.
17. Do not inspect the private recovery vault, private device captures,
    ignored original artifacts or real private keys. Do not clone the
    validation repository locally.

## Truthfulness

18. Explain errors truthfully. Runtime source failures, pending batches and
    code crashes are different states. Never greenwash incomplete coverage,
    never inflate counts (multiple deliveries of one version are one version;
    URL-level availability is not distinct binaries).
19. Discovery and preservation are separate numbers. Observing a revision is
    not preserving it. Report both with provenance.
20. Be direct and concise in progress updates. Do not restate unresolved
    problems as if they were plans; fix them or say exactly what is blocking.

## Code quality

21. **Comments describe what the code does, never the project's history.**
    No comments explaining what changed, what was wrong before, what a review
    said, or how the current version differs from an earlier one. If a
    comment references the past, delete it.
22. No test/quality machinery displacing discovery work. No useless one-off
    tools, no masses of tests. Existing functional checks stay; add focused
    tests only where they protect real behavior.
23. Beautiful code: small deep modules, clear names, no dead paths, no
    speculative generality, no duplicated logic across scripts.

## Process

24. Use the question tool extensively before making decisions.
25. Rules belong in this file. Do not create rule documentation in random
    files; other documentation describes architecture and usage only.
26. Do not merge to production without explicit user authorization.
27. Do not comment on or close issues #23/#24/#25 and do not add automatic
    closing directives for them; the PR uses `Related: #23, #24, #25.` Monitor
    issues are ongoing reports: a passing parser or code check never closes one.
28. Reversible local edits, source-only validation deployments and pushing
    the PR branch are pre-authorized; no fresh permission question is needed
    for ordinary fixes.
