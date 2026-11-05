# Current checks daily, historical recovery weekly

The daily monitor keeps the current firmware feed, release notes, official
Windows/macOS download redirects, current Sonos-hosted APK redirect and Google
Play listings. No source is permanently dropped. Apple listings remain every
six hours. The hourly public web workflow is the sole web owner: the daily check
and duplicate preservation path are removed.

A second trigger on the same monitor workflow runs the full historical pass
weekly, Sunday at07:43UTC. It retains historical WinGet/Homebrew versions,
Wayback installer/APK discovery, old missing URL retries on both firmware hosts,
the full known-folder speaker-model sweep and full GitHub release integrity audit.
The weekly run produces the same reports and feeds the same existing automatic
preservation workflow, so recovered historical files still get saved after a
future deployment. Manual monitor runs default to current-only, with an explicit
include_history switch. Manual preservation regenerates current-only reports.

Weekly rather than monthly is a conservative reduction: old hosted files can
reappear or disappear, and today's historical research found real missing files.
It reduces repetition without leaving a month-long recovery gap. Daily firmware
feeds still check all packages currently advertised, including old milestone
packages returned by a current list.

Measured baseline: the captured daily run61 checked2,025directory URLs and549
missing URLs on each of two hosts, plus current packages and controls. Moving
those repeated historical probes to weekly avoids at least18,738such URL checks
per ordinary seven-day week (6 × (2,025 + 1,098)), using that fixed baseline.
Actual totals vary with the catalog and retries; this is not a whole-job cap.
Historical desktop discovery also walked source history and probed140URLs in
that run; APK discovery probed34. Their daily current-only modes skip the history
walks and only resolve/probe current official links and desktop counterparts.

This PR is based on main and is not deployed while unmerged. It overlaps the
monitor arguments and workflow steps touched by #15/#17 and the separately
reviewed message text in #16. Preserve #15's modern feed, #17's discovery/error
safeguards and #16's plain messages when combining branches. In particular,
#17's historical/sibling traversal belongs in the weekly historical scope;
daily must still follow current-feed packages and new folders. Do not blindly
choose one side of a workflow conflict.

No forum scraper, forum schedule or forum trial is included here. The local
forum experiment is separate. No existing cron has been changed live, and no
archive upload or workflow dispatch was performed to test this change.
