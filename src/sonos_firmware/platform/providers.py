"""Source adapters: translate one worker context into source-specific operations."""
import json
from .model import Context


def firmware(c: Context):
    report = c.work / 'discovery.json'
    roots = tuple(arg for root in c.options.get('artifact_roots', []) for arg in
                  ('--artifact-root', json.dumps({**root, 'refresh_hours': c.target.interval_hours})))
    return [
        c.script('discover_firmware.py', '--evidence-dir', c.observations / 'manifests', '--work-dir', c.work / 'publish',
                 '--report', report, '--seconds', c.options.get('seconds', 1200),
                 '--profile-limit', c.options.get('profile_limit', 8),
                 '--profile-count', c.options.get('profile_count', 1200),
                 '--model-max', c.options.get('model_max', 255),
                 '--inference-limit', c.options.get('inference_limit', 128),
                 '--publish', '--repository', c.repository, '--max-packages', c.options.get('limit', 24), *roots, report=str(report)),
        c.task('extract-firmware', '--work-dir', c.work / 'extract', '--repository', c.repository,
               '--max-packages', c.options.get('extraction_limit', 24),
               '--inventory-limit', c.options.get('inventory_limit', 4),
               '--inventory-limits', json.dumps(c.options.get('inventory_limits', {})),
               after_failure=True, report=str(c.work / 'extract/extraction.json')),
    ]


def desktop(c: Context):
    discovery = c.root / 'data/apps/desktop-discovery.json'
    return [c.script('discover_desktop_installers.py', '--output', discovery, report=str(discovery)),
            c.script('archive_desktop_installers.py', '--discovery', discovery,
                     '--repo', c.repository, '--limit', c.options.get('limit', 12),
                     '--refresh-hours', c.target.interval_hours,
                     '--historical-refresh-hours', c.options.get('historical_refresh_hours', 168),
                     report=str(c.root / ('data/apps/' + c.target.provider + '-archive.json')), after_failure=True)]


def mobile(c: Context):
    discovery = c.root / 'data/apps/mobile-discovery.json'
    return [c.script('discover_mobile_installers.py', '--output', discovery, report=str(discovery)),
            c.script('archive_mobile_installers.py', '--discovery', discovery,
                     '--repo', c.repository, '--limit', c.options.get('limit', 12),
                     '--refresh-hours', c.target.interval_hours,
                     '--historical-refresh-hours', c.options.get('historical_refresh_hours', 168),
                     report=str(c.root / ('data/apps/' + c.target.provider + '-archive.json')), after_failure=True)]


def web(c: Context):
    report = c.work / 'discovery.json'
    return [c.script('check_new_web_apps.py', '--output', report, accepted_codes=(0, 2), report=str(report)),
            c.script('archive_web_apps.py', '--capture-dir', c.work / 'captures', '--upload', '--repo', c.repository)]


def apple(c: Context):
    current = c.work / 'current.json'
    report = c.work / 'discovery.json'
    return [c.script('check_new_apple_apps.py', '--output', report, '--discovery-output', current,
                     accepted_codes=(0, 2), report=str(report)),
            c.task('store-apple-metadata', '--current', current)]


def android_history(c: Context):
    # Acquisition is scheduled independently of a listing's observed-change flag.
    return [c.script('sweep_android_store_history.py', '--apkeep', c.options.get('apkeep', 'apkeep'),
                     '--repo', c.repository, '--limit', c.options.get('limit', 24),
                     report=str(c.root / 'data/apps/android-store-history-sweep.json'))]


def google_play(c: Context):
    return [c.script('archive_android_store_apps.py', '--source', 'google-play-direct',
                     '--accept-google-play-tos', '--repo', c.repository,
                     '--apkeep', c.options.get('apkeep', 'apkeep'))]


def google_play_listings(c: Context):
    current, report = c.work / 'current.json', c.work / 'discovery.json'
    return [c.script('check_new_google_play_apps.py', '--output', report, '--discovery-output', current,
                     accepted_codes=(0, 2), report=str(report)),
            c.task('store-google-play-metadata', '--current', current)]


def release_audit(c: Context):
    report = c.root / 'data/platform/audits/release-assets.json'
    return [c.script('audit_release_assets.py', '--repo', c.repository, '--output', report, report=str(report))]


def gpl_indexes(c: Context):
    selection = ('--version', c.options['version']) if c.options.get('version') else ('--all-known',)
    return [c.script('archive_gpl_direct.py', 'indexes', *selection, '--delay', 2.5),
            c.script('archive_gpl_direct.py', 'report', after_failure=True)]


def gpl(c: Context):
    return [c.script('discover_gpl_sitemaps.py', '--limit', c.options.get('sitemap_limit', 8), '--delay', 2.5,
                     report=str(c.root / 'data/gpl/live/site-evidence.json')),
            c.script('archive_gpl_direct.py', 'discover', '--limit', c.options.get('limit', 96), '--delay', 2.5,
                     report=str(c.root / 'data/gpl/live/discovery.json')),
            c.script('archive_gpl_direct.py', 'report', after_failure=True)]


def gpl_archive(c: Context):
    return [c.script('archive_gpl_direct.py', 'archive', '--next', '--include-upstream',
                     '--continue-errors', '--publish', '--store', c.work / 'objects',
                     '--reserve-bytes', 2_000_000_000, '--limit', c.options.get('limit', 90),
                     '--delay', 2.5, '--repo', c.repository),
            c.script('archive_gpl_direct.py', 'report', after_failure=True)]


def gpl_audit(c: Context):
    return [c.script('audit_gpl_published_streams.py', '--store', c.work / 'audit',
                     '--reserve-bytes', 2_000_000_000, '--limit', c.options.get('limit', 25), '--delay', 2.5),
            c.script('archive_gpl_direct.py', 'report', after_failure=True)]


BUILTINS = {'firmware': firmware, 'desktop': desktop, 'mobile': mobile, 'web': web,
            'apple-listings': apple, 'android-history': android_history, 'google-play': google_play,
            'google-play-listings': google_play_listings, 'release-audit': release_audit,
            'gpl-indexes': gpl_indexes,
            'gpl': gpl, 'gpl-archive': gpl_archive, 'gpl-audit': gpl_audit}
