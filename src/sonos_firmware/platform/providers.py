"""Source adapters: translate one worker context into source-specific operations."""
import json
from .model import Context


def firmware(c: Context):
    report = c.work / 'discovery.json'
    roots = tuple(arg for root in c.options.get('artifact_roots', []) for arg in
                  ('--artifact-root', json.dumps({**root, 'refresh_hours': c.target.interval_hours})))
    # Work on verified/preserved inputs first. The speculative numeric frontier
    # can run until wind-down without preventing publication and extraction.
    return [
        c.task('archive-historical-firmware', '--work-dir', c.work / 'history', '--repository', c.repository,
               report=str(c.work / 'history/replay.json')),
        c.task('extract-firmware', '--work-dir', c.work / 'extract', '--repository', c.repository,
               after_failure=True, report=str(c.work / 'extract/extraction.json')),
        c.script('firmware/discover_firmware.py', '--evidence-dir', c.observations / 'manifests',
                 '--work-dir', c.work / 'publish', '--report', report,
                 '--publish', '--repository', c.repository, *roots,
                 after_failure=True, report=str(report)),
    ]


def desktop(c: Context):
    discovery = c.root / 'data/apps/desktop-discovery.json'
    return [c.script('apps/discover_desktop_installers.py', '--output', discovery,
                     '--archive-root', c.root,
                     '--refresh-hours', c.target.interval_hours,
                     '--observations', c.observations / 'package-index', report=str(discovery)),
            c.script('apps/archive_desktop_installers.py', '--discovery', discovery,
                     '--repo', c.repository,
                     '--refresh-hours', c.target.interval_hours,
                     '--historical-refresh-hours', c.options.get('historical_refresh_hours', 168),
                     report=str(c.root / ('data/apps/' + c.target.provider + '-archive.json')), after_failure=True),
            installer_history(c, 'desktop')]


def mobile(c: Context):
    discovery = c.root / 'data/apps/mobile-discovery.json'
    return [c.script('apps/discover_mobile_installers.py', '--output', discovery, '--archive-root', c.root, report=str(discovery)),
            c.script('apps/archive_mobile_installers.py', '--discovery', discovery,
                     '--repo', c.repository,
                     '--refresh-hours', c.target.interval_hours,
                     '--historical-refresh-hours', c.options.get('historical_refresh_hours', 168),
                     report=str(c.root / ('data/apps/' + c.target.provider + '-archive.json')), after_failure=True),
            installer_history(c, 'mobile')]


def installer_history(c, kind):
    return c.task('archive-installer-history', '--kind', kind, '--work-dir', c.work/'history',
                  '--repository', c.repository,
                  '--refresh-hours', c.target.interval_hours,
                  '--historical-refresh-hours', c.options.get('historical_refresh_hours', 168), after_failure=True,
                  report=str(c.work/'history/replay.json'))


def web(c: Context):
    return [c.script('apps/archive_web_apps.py', '--capture-dir', c.work / 'captures', '--upload', '--repo', c.repository,
                     report=str(c.root / 'data/apps/web-app-archive.json'))]


def apple(c: Context):
    current = c.work / 'current.json'
    report = c.work / 'discovery.json'
    return [c.script('apps/check_new_apple_apps.py', '--output', report, '--discovery-output', current,
                     accepted_codes=(0, 2), report=str(report)),
            c.task('store-apple-metadata', '--current', current)]


def android_history(c: Context):
    # Acquisition is scheduled independently of a listing's observed-change flag.
    return [c.script('apps/sweep_android_store_history.py', '--apkeep', c.options.get('apkeep', 'apkeep'),
                     '--repo', c.repository,
                     '--refresh-hours', c.target.interval_hours,
                     '--observations', c.observations / 'apkpure',
                     report=str(c.root / 'data/apps/android-store-history-sweep.json'))]


def google_play(c: Context):
    return [c.script('apps/sweep_android_store_history.py', '--source', 'google-play-direct',
                     '--repo', c.repository,
                     '--metadata-hints', c.root / 'data/apps/android-store-history-sweep.json',
                     '--refresh-hours', c.target.interval_hours,
                     report=str(c.root / 'data/apps/google-play-sweep.json'))]


def google_play_listings(c: Context):
    current, report = c.work / 'current.json', c.work / 'discovery.json'
    return [c.script('apps/check_new_google_play_apps.py', '--output', report, '--discovery-output', current,
                     accepted_codes=(0, 2), report=str(report)),
            c.task('store-google-play-metadata', '--current', current)]


def release_audit(c: Context):
    report = c.root / 'data/platform/audits/release-assets.json'
    return [c.script('archive/audit_release_assets.py', '--repo', c.repository, '--output', report,
                     '--recover-documents', report=str(report))]


def gpl_indexes(c: Context):
    selection = ('--version', c.options['version']) if c.options.get('version') else ('--all-known',)
    return [c.script('gpl/archive_gpl_direct.py', 'indexes', *selection, '--delay', 2.5),
            c.script('gpl/archive_gpl_direct.py', 'report', after_failure=True)]


def gpl(c: Context):
    return [c.task('scan-preserved-binaries', '--work-dir', c.work/'binary-scan', '--repository', c.repository,
                   report=str(c.root/'data/gpl/live/binary-evidence.json')),
            c.script('gpl/discover_gpl_sitemaps.py', '--delay', 2.5,
                     report=str(c.root / 'data/gpl/live/site-evidence.json'), after_failure=True),
            c.script('gpl/archive_gpl_direct.py', 'discover', '--delay', 2.5,
                     report=str(c.root / 'data/gpl/live/discovery.json'), after_failure=True),
            c.script('gpl/archive_gpl_direct.py', 'report', after_failure=True)]


def gpl_archive(c: Context):
    return [c.script('gpl/archive_gpl_direct.py', 'archive', '--next', '--include-upstream',
                     '--continue-errors', '--publish', '--store', c.work / 'objects',
                     '--delay', 2.5, '--repo', c.repository),
            c.script('gpl/archive_gpl_direct.py', 'report', after_failure=True)]


def gpl_audit(c: Context):
    # Repair before reporting. The stream auditor is the work that reduces the
    # pending set; the coverage check is the gate on the result. Ordering the
    # gate first would let a large unattempted backlog skip the repair entirely.
    return [c.script('gpl/audit_gpl_published_streams.py', '--store', c.work / 'audit',
                     '--delay', 2.5,
                     report=str(c.root/'data/gpl/live/stream-audit.json')),
            c.script('gpl/audit_gpl_link_coverage.py',
                     '--output', c.root / 'data/gpl/live/link-coverage.json',
                     after_failure=True,
                     report=str(c.root/'data/gpl/live/link-coverage.json')),
            c.script('gpl/archive_gpl_direct.py', 'report', after_failure=True)]


# Built-in provider configuration is closed over real, supported knobs.
# Unknown keys are rejected instead of silently accepted (including retired
# work quotas). Third-party providers define their own option contracts.
SUPPORTED_OPTIONS = {
    'firmware': {'artifact_roots'},
    'desktop': {'historical_refresh_hours'},
    'mobile': {'historical_refresh_hours'},
    'android-history': {'apkeep'},
    'gpl-indexes': {'version'},
    **{provider: set() for provider in (
        'web', 'apple-listings', 'google-play', 'gpl', 'gpl-archive',
        'gpl-audit', 'google-play-listings', 'release-audit')},
}


def validate_options(provider, options):
    allowed = SUPPORTED_OPTIONS.get(provider)
    if allowed is None:
        return
    unknown = set(options) - allowed
    if unknown:
        raise ValueError(f"unsupported {provider} provider option(s): {', '.join(sorted(unknown))}")


BUILTINS = {'firmware': firmware, 'desktop': desktop, 'mobile': mobile, 'web': web,
            'apple-listings': apple, 'android-history': android_history, 'google-play': google_play,
            'google-play-listings': google_play_listings, 'release-audit': release_audit,
            'gpl-indexes': gpl_indexes,
            'gpl': gpl, 'gpl-archive': gpl_archive, 'gpl-audit': gpl_audit}
