# Public firmware discovery coverage

This extends the existing monitor, not a second schedule. It is a local proposal
until reviewed and published. It does not enumerate all files on Sonos's servers.
Unknown campaign addresses, unpublished files, and account-only responses remain
outside its visibility. It never guesses opaque folder codes.

The monitor starts with the existing anonymous modern feed and synthetic legacy
profile, catalog manifest URLs, package folders, and config/discovery-seeds.json.
Every seed is an exact previously successful official URL. Seeded chip-firmware files are checked directly even when no sibling list exists. New public links can
be added with their source evidence, without changing crawler code.

For every encountered package folder, including Gold, the crawler requests the
literal sibling update.upm. Successful lists contribute their contents, explicit
list links and additional exact package folders to a deduplicated queue. It
resolves relative filenames and base URLs against the source list. v1 lists
without supported_models use model IDs actually present in their image rows as
an explicitly labeled inference; the directory sweep separately covers IDs0-80
by default. Inferences are availability-checked, never treated as confirmed files.
A campaign's folder version need not match a hosted file's version: package
filenames supply the version for speaker expansion.

The existing directory sweep consumes the traversal report and verified seed
folders, so newly learned folders are checked in the same run. It includes
speaker-only and Gold folders as well as desktop folders. This finds files that
are hosted but not offered to those models in the update list. It does not guess
arbitrary chip-firmware versions or filenames absent all evidence.

Raw lists (including malformed received bodies), SHA256 receipts, parent/source
edges, warnings, failed URLs, pending URLs and limits go into the run artifacts.
The existing preservation path accepts successful official lists and artifacts;
its existing separate approval/deployment requirements are unchanged. The
historical allowlisted GitHub mirror may supply discovery evidence but is not
silently passed to the official-manifest upload path. Successful official lists
are imported by the existing archiver, retaining URLs for subsequent runs.

Defaults:256list requests,2MB per list,0.25second spacing on newly fetched lists,
8,000directory probes, model IDs0-80, sequential directory probes with0.25second
spacing. Lists and directories stop on403/429. Package probes are sequential and paced too, and a source challenge skips later source checks. Exhausted limits and unexpected
fetch/parse/unsupported-host failures mark the run incomplete, not clean. Ordinary
404s are retained as missing endpoints, not proof that no historical list exists.
Unsupported schema versions remain visible and make discovery incomplete.

No new cron is added. This proposal modifies only the workflow step arguments
and evidence artifacts, not message text. PR16's separately approved plain-language
message changes must be preserved when assembling the eventual review branch.

Deployment can use the repository's existing auto-preserve workflow to download
and publish detected public originals. This local implementation has not run that
workflow, uploaded files, pushed a branch, opened a new PR or merged anything.
Review the intended deployment and publication scope before enabling it.

Validation:58unit tests pass; catalog verify passes; all workflow YAML parses.
Offline replay of29captured lists and13verified folder seeds covers all150
previously recovered file URLs through list entries, direct seeds or bounded
model expansion. This is a regression test of known examples, not a new live
full scrape or a count of new files. No network sweep was run for validation.

Checked-in public list fixtures, receipts and recovered URLs let the150-file
regression run entirely offline. No device or account data is included.
