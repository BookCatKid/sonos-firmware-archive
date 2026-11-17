#!/usr/bin/env python3
"""Discover Android store versions and resume verified acquisition automatically."""
from __future__ import annotations

import argparse
import json
import math
import re
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sonos_firmware import retry
from sonos_firmware.android_store import play_profiles, play_version, safe_error, run_apkeep, store_apps, StoreError, run_acquisition
from sonos_firmware.releases import receipt_matches_repository
from sonos_firmware.storage import atomic_json
from sonos_firmware.delivery_archive import delivery_profiles
from sonos_firmware.apkpure import (
    discover_versions as discover_apkpure, parse_versions as parse_apkpure,
    discover_sdk_levels, parse_sdk_levels,
)

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = tuple(store_apps(ROOT))


def discover_versions(apkeep: str, package: str, *, timeout=None) -> list[str]:
    with tempfile.TemporaryDirectory(prefix='sonos-apkpure-list-') as temporary:
        result = run_apkeep(
            [apkeep, '-a', package, '-d', 'apk-pure', '-o', 'output_format=json', '--list-versions', temporary],
            timeout=timeout,
        )
    try:
        document = json.loads(result.stdout)
        listing = document['apps'][package]
        versions = listing['available_versions']
        if document.get('source') != 'APKPure' or listing.get('error') or not isinstance(versions, list) or not versions:
            raise ValueError('missing or empty APKPure version list')
        selectors = []
        for row in versions:
            selector = row['version']
            if not isinstance(selector, str) or not re.fullmatch(r'[A-Za-z0-9.-]+', selector):
                raise ValueError('invalid APKPure version selector')
            selectors.append(selector)
    except (ValueError, KeyError, TypeError) as error:
        raise StoreError(f'could not validate APKPure version list for {package}') from error
    return list(dict.fromkeys(selectors))


def identity(row):
    return (row['package'], str(row['version_selector']), row.get('arch'), row.get('profile'), row.get('variant'))


def profile_identity(row):
    fields = [row['package'], row.get('arch', ''), row.get('profile', '')]
    if row.get('metadata_source'):
        fields.append(row['metadata_source'])
    if row.get('sdk') is not None:
        fields.append('sdk=' + str(row['sdk']))
    return '|'.join(fields)


def cooldown(state, error):
    """Pause a whole source on 429; switching jobs must not bypass throttling."""
    status = error.get('status_code')
    if status != 429:
        return False
    failures = int(state.get('cooldown_failures', 0)) + 1
    fallback = 60 * 2 ** min(failures - 1, 5)
    try:
        delay = float(error['retry_after']) if error.get('retry_after') is not None else fallback
    except (TypeError, ValueError):
        delay = fallback
    if not math.isfinite(delay) or delay < 0:
        delay = fallback
    now = datetime.now(timezone.utc)
    # Honor representable server cooldowns; invalid or overflowing values use
    # the retry backoff without preventing the ledger checkpoint.
    try:
        next_request = now + timedelta(seconds=max(1, delay))
    except OverflowError:
        next_request = now + timedelta(seconds=fallback)
    state.update(cooldown_failures=failures, next_request_at=next_request.isoformat())
    return True


def wait_for_source(state, deadline, save):
    while not retry.due({'next_attempt_at': state.get('next_request_at')}):
        remaining = (datetime.fromisoformat(state['next_request_at']) - datetime.now(timezone.utc)).total_seconds()
        save()
        if remaining >= deadline - time.monotonic():
            return False
        time.sleep(min(30, max(0, remaining)))
    return time.monotonic() < deadline


def sdk_contexts(state, observations, deadline, save, errors):
    """Refresh public SDK metadata independently of APKPure's rate limit."""
    record = state.setdefault('sdk_index', {})
    levels = {29, *record.get('levels', [])}
    cached = observations/'android-sdk-index.json' if observations else None
    if cached and cached.exists():
        try:
            levels.update(parse_sdk_levels(json.loads(cached.read_text())['xml']))
        except (OSError, ValueError, TypeError, KeyError, SyntaxError, StoreError):
            pass
    index_deadline = min(deadline, time.monotonic()+30)
    if retry.due(record):
        while wait_for_source(record, index_deadline, save):
            try:
                result = discover_sdk_levels(observations=observations,
                    timeout=max(1, index_deadline-time.monotonic()))
                levels.update(result['levels'])
                issues = result.get('errors', [])
                record.update(retry.checkpoint(record, success=not issues, success_hours=168),
                              levels=sorted(levels), cooldown_failures=0)
                record.pop('error', None)
                record.pop('source_errors', None)
                if issues:
                    record.update(error='AndroidSDKIndexObservationIncomplete', source_errors=issues)
                save()
                break
            except Exception as error:
                sanitized = safe_error(error)
                record.update(retry.checkpoint(record), error=str(sanitized))
                if cooldown(record, {'status_code': sanitized.status_code, 'retry_after': sanitized.retry_after}):
                    record['next_attempt_at'] = record['next_request_at']
                    save()
                    continue
                save()
                break
    if record.get('error'):
        errors.append({'source': 'android-sdk-index', 'error': record['error']})
    for observation in state.get('discovery', {}).values():
        levels.update(observation.get('sdk_hints', []))
    levels.update(state.get('sdk_hints', []))
    # The documented client context is a bootstrap, never a ceiling. Prefer
    # newer published contexts, while retaining older compatibility histories.
    return [29, *sorted(levels-{29}, reverse=True)]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', choices=('apkpure-recovery', 'google-play-direct'), default='apkpure-recovery')
    parser.add_argument('--apkeep', default='apkeep')
    parser.add_argument('--repo', default='BookCatKid/sonos-firmware-archive')
    parser.add_argument('--limit', type=int, default=24, help='acquisition work budget per run, never a lifetime limit')
    parser.add_argument('--discovery-limit', type=int)
    parser.add_argument('--refresh-hours', type=float, default=6)
    parser.add_argument('--seconds', type=float, default=1200, help='worker budget including rate-limit waits')
    parser.add_argument('--package', choices=PACKAGES, action='append')
    parser.add_argument('--receipt', type=Path, default=ROOT / 'data/apps/android-store-archive.json')
    parser.add_argument('--ledger', type=Path)
    parser.add_argument('--observations', type=Path, help='ephemeral metadata cache, retained by the common worker')
    parser.add_argument('--metadata-hints', type=Path, help='optional discovered numeric codes from another store ledger')
    args = parser.parse_args()
    if args.discovery_limit is None:
        args.discovery_limit = 4 if args.source == 'google-play-direct' else 24
    if min(args.limit, args.discovery_limit, args.seconds, args.refresh_hours) <= 0:
        parser.error('budgets and refresh interval must be positive')
    if args.ledger is None:
        args.ledger = ROOT / ('data/apps/google-play-sweep.json' if args.source == 'google-play-direct'
                              else 'data/apps/android-store-history-sweep.json')
    packages = args.package or list(PACKAGES)
    receipt = json.loads(args.receipt.read_text()) if args.receipt.exists() else {'artifacts': []}
    state = json.loads(args.ledger.read_text()) if args.ledger.exists() else {}
    if state.get('repository') not in (None, args.repo):
        # Leads are reusable in a fork; cooldowns and observations are too, but
        # source successes must not suppress publication into a new repository.
        for attempt in state.get('attempts', []):
            attempt.pop('next_attempt_at', None)
    state.update(schema_version=1, repository=args.repo, scope=args.source)
    attempts = state.setdefault('attempts', [])
    attempted = {identity(row): row for row in attempts}
    queue = {identity(row): row for row in state.get('pending_selectors', [])}
    archived = set()
    for item in receipt.get('artifacts', []):
        if item.get('source_kind') != args.source or not receipt_matches_repository(item, args.repo):
            continue
        for profile in delivery_profiles(item) or [{}]:
            for selector in {item.get('source_version_selector'), *item.get('source_version_selectors', [])}:
                if selector:
                    archived.add((item['package'], str(selector), profile.get('arch'), profile.get('device'), profile.get('variant')))
    for attempt in attempts:
        if identity(attempt) not in archived:
            queue.setdefault(identity(attempt), {key: value for key, value in attempt.items() if key not in {
                'checked', 'checked_at', 'status', 'error', 'next_attempt_at', 'consecutive_failures', 'exit_code'}})
    discovery = state.setdefault('discovery', {})
    observed = state.setdefault('observed_variants', {})
    errors = []
    deadline = time.monotonic() + args.seconds

    def save():
        pending = [row for key, row in queue.items() if key not in archived]
        state.update(updated=datetime.now(timezone.utc).isoformat(), attempts=attempts,
                     pending_selectors=pending, attempt_total=len(attempts),
                     archived_total=sum(row['status'] == 'archived' for row in attempts),
                     failed_total=sum(row['status'] == 'failed' for row in attempts),
                     discovery_errors=errors, pending_total=len(pending),
                     status='partial' if errors or pending or state.get('sdk_index', {}).get('error') or
                         any(row.get('error') for row in discovery.values()) else 'ok')
        atomic_json(args.ledger, state)

    def remember(leads):
        keys = []
        sdk_hints = set(state.get('sdk_hints', []))
        for lead in leads:
            key = lead['package'] + ':' + lead['variant']
            observation = observed.setdefault(key, {})
            contexts = set(observation.get('sdk_contexts', []))
            contexts.add(lead.get('sdk_context', 29))
            observation.update(lead, sdk_contexts=sorted(contexts))
            sdk_hints.update(int(lead[field]) for field in ('sdk_version', 'target_sdk_version')
                             if str(lead.get(field, '')).isdecimal() and int(lead[field]) > 0)
            queue.setdefault(identity(lead), {}).update(observation)
            keys.append(key)
        state['sdk_hints'] = sorted(sdk_hints)
        return keys

    # Older ledgers embedded complete API rows under every context. Normalize
    # them once, preserving code hints and per-context provenance without copies.
    for record in discovery.values():
        variants = [lead for lead in record.get('versions', []) if lead.get('variant')]
        if variants:
            record['variants'] = remember(variants)
            record['versions'] = [lead for lead in record['versions'] if not lead.get('variant')]
    for lead in list(observed.values()):
        queue.setdefault(identity(lead), {}).update(lead)

    with tempfile.TemporaryDirectory(prefix='sonos-store-sweep-') as temporary:
        auth_dir = Path(temporary) / 'anonymous-auth'
        if args.source == 'google-play-direct':
            profiles = play_profiles()
            discovery_jobs = [{**profile, 'package': package} for profile in profiles for package in packages]
        else:
            profiles = []
            levels = sdk_contexts(state, args.observations, deadline, save, errors)
            discovery_jobs = [{'package': package, 'metadata_source': 'apkeep'} for package in packages]
            discovery_jobs += [{'package': package, 'metadata_source': 'apkpure-api', 'sdk': sdk}
                               for sdk in levels for package in packages]
            for package in packages:
                # Migrate the previous single listing source without forgetting
                # its cadence or observations.
                prior = discovery.pop(package + '||', None)
                if prior is not None:
                    discovery.setdefault(package + '|||apkeep', prior)
                prior = discovery.pop(package+'|||apkpure-api', None)
                if prior is not None:
                    discovery.setdefault(package+'|||apkpure-api|sdk=29', prior)
                for sdk in levels:
                    cached = args.observations/f'{package}--sdk-{sdk}.json' if args.observations else None
                    legacy = args.observations/(package+'.json') if args.observations and sdk == 29 else None
                    if cached and not cached.exists() and legacy:
                        cached = legacy
                    if cached and cached.exists():
                        try:
                            parsed = parse_apkpure(json.loads(cached.read_text()), package, sdk=sdk)
                            remember(parsed['variants'])
                        except (OSError, ValueError, TypeError, StoreError):
                            pass
        chosen = retry.select(discovery_jobs, discovery, key=profile_identity,
                              limit=args.discovery_limit, state=state, queue='store-discovery')
        for job in chosen:
            key = profile_identity(job)
            while wait_for_source(state, deadline, save):
                try:
                    parse_errors = []
                    if args.source == 'google-play-direct':
                        version = play_version(job['package'], auth_dir, job['arch'], job['profile'])
                        leads = [{**job, **version, 'version_selector': version['version_code'],
                                  'discovered_from': 'google-play-profile'}]
                    elif job['metadata_source'] == 'apkpure-api':
                        result = discover_apkpure(job['package'], sdk=job['sdk'], observations=args.observations,
                                                 timeout=max(1, deadline-time.monotonic()))
                        leads, parse_errors = result['variants'], result['errors']
                    else:
                        leads = [{**job, 'version_selector': selector, 'rank': rank} for rank, selector in enumerate(
                            discover_versions(args.apkeep, job['package'], timeout=max(1, deadline-time.monotonic())))]
                    for lead in leads:
                        queue.setdefault(identity(lead), {}).update(lead)
                    refresh = 168 if job.get('sdk') is not None and not leads and not parse_errors else args.refresh_hours
                    record = {**retry.checkpoint(discovery.get(key), success=not parse_errors, success_hours=refresh)}
                    if job.get('metadata_source') == 'apkpure-api':
                        record.update(variants=remember(leads), sdk_hints=result.get('sdk_hints', []),
                                      compatible_variant_count=len(leads))
                    else:
                        record['versions'] = leads
                    discovery[key] = record
                    if parse_errors:
                        discovery[key].update(error='APKPureDiscoveryIncomplete', source_errors=parse_errors)
                        errors.append({'package': job['package'], 'error': 'APKPureDiscoveryIncomplete'})
                    state['cooldown_failures'] = 0
                    save()
                    break
                except Exception as error:
                    sanitized = safe_error(error)
                    record = {'package': job['package'], 'error': str(sanitized),
                              'status_code': sanitized.status_code, 'retry_after': sanitized.retry_after}
                    if cooldown(state, record):
                        discovery[key] = {**discovery.get(key, {}), **retry.checkpoint(discovery.get(key)), 'error': record['error'],
                                          'next_attempt_at': state['next_request_at']}
                        save()
                        continue
                    discovery[key] = {**discovery.get(key, {}), **retry.checkpoint(discovery.get(key)), 'error': record['error']}
                    errors.append(record)
                    save()
                    break
            if not retry.due({'next_attempt_at': state.get('next_request_at')}):
                break
        # Reuse saved observations even when a current listing is down. Existing
        # receipts supply historical numeric hints, never required bootstrap data.
        if args.source == 'google-play-direct':
            hint_rows = []
            if args.metadata_hints and args.metadata_hints.exists():
                try:
                    hints = json.loads(args.metadata_hints.read_text())
                    hint_rows = [*hints.get('observed_variants', {}).values(),
                        *hints.get('pending_selectors', []), *hints.get('attempts', []),
                        *(lead for record in hints.get('discovery', {}).values() for lead in record.get('versions', []))]
                    if any(not isinstance(row, dict) for row in hint_rows):
                        errors.append({'error': 'InvalidStoreMetadataHintRows'})
                        hint_rows = [row for row in hint_rows if isinstance(row, dict)]
                except (OSError, ValueError, TypeError, AttributeError):
                    errors.append({'error': 'InvalidStoreMetadataHints'})
            codes = {(item['package'], str(item['version_code'])) for item in
                     [*hint_rows, *receipt.get('artifacts', []), *(capture['artifact'] for capture in receipt.get('pending_captures', []))]
                     if item.get('package') in packages and str(item.get('version_code', '')).isdecimal()
                     and int(item['version_code']) > 0}
            for package, code in sorted(codes):
                for profile in profiles:
                    lead = {'package': package, 'version_selector': code, **profile}
                    queue.setdefault(identity(lead), lead)
        for record in discovery.values():
            for lead in record.get('versions', []):
                queue.setdefault(identity(lead), {}).update(lead)
        save()
        pending = [row for key, row in queue.items() if row['package'] in packages and key not in archived]
        pending.sort(key=lambda row: (row.get('discovered_from') not in {
            'google-play-profile', 'google-play-delivery', 'apkpure-api'}, row.get('rank', 0), row['package']))
        chosen = retry.select(pending, attempted, key=identity, limit=args.limit, state=state, queue='store-acquisition')
        for job in chosen:
            key = identity(job)
            while wait_for_source(state, deadline, save):
                result_path = Path(temporary) / 'result.json'
                result_path.unlink(missing_ok=True)
                command = [sys.executable, str(ROOT / 'scripts/archive_android_store_apps.py'),
                           '--source', args.source, '--package', job['package'], '--receipt', str(args.receipt),
                           '--repo', args.repo, '--result', str(result_path)]
                if args.source == 'google-play-direct':
                    command += ['--version-code', job['version_selector'], '--play-arch', job['arch'],
                                '--play-profile', job['profile'], '--auth-dir', str(auth_dir)]
                else:
                    command += ['--version-selector', job['version_selector'], '--apkeep', args.apkeep]
                    if job.get('asset_url'):
                        command += ['--asset-url', job['asset_url'], '--asset-type', job['asset_type'],
                                    '--version-code', job['version_code'], '--variant-arch', job['arch'],
                                    '--variant', job['variant']]
                completed = run_acquisition(command, cwd=ROOT, timeout=max(1, deadline-time.monotonic()))
                result = json.loads(result_path.read_text()) if result_path.exists() else {}
                record = {**job, **retry.checkpoint(attempted.get(key), success=completed.returncode == 0),
                          'checked': datetime.now(timezone.utc).isoformat(),
                          'exit_code': completed.returncode,
                          'status': 'archived' if completed.returncode == 0 else 'failed'}
                source_errors = result.get('errors', [])
                if args.source == 'google-play-direct':
                    for error in source_errors:
                        offered = error.get('observed_version_code')
                        if offered and str(offered).isdecimal() and int(offered) > 0:
                            lead = {**job, 'version_selector': str(offered),
                                    'discovered_from': 'google-play-delivery'}
                            lead.pop('version_name', None)
                            lead.pop('version_code', None)
                            queue.setdefault(identity(lead), {}).update(lead)
                throttled = next((error for error in source_errors if error.get('status_code') == 429), None)
                if completed.returncode:
                    record['error'] = ('; '.join(error['error'] for error in source_errors) if source_errors else
                                       ('GooglePlayAcquisitionFailed' if args.source == 'google-play-direct' else completed.stdout[-4000:]))
                    if throttled:
                        cooldown(state, throttled)
                        record.update(status='pending', next_attempt_at=state['next_request_at'])
                else:
                    archived.add(key)
                    state['cooldown_failures'] = 0
                    print(f"archived {job['package']}@{job['version_selector']}", flush=True)
                if key in attempted:
                    attempts[attempts.index(attempted[key])] = record
                else:
                    attempts.append(record)
                attempted[key] = record
                save()
                if not throttled:
                    break
            if not retry.due({'next_attempt_at': state.get('next_request_at')}):
                break
    if args.source == 'apkpure-recovery':
        # Fresh metadata can reveal a SDK not in the publisher index yet. It
        # must count as pending work even when this worker has spent its budget.
        existing = {profile_identity(job) for job in discovery_jobs}
        for sdk in state.get('sdk_hints', []):
            for package in packages:
                job = {'package': package, 'metadata_source': 'apkpure-api', 'sdk': sdk}
                if profile_identity(job) not in existing:
                    discovery_jobs.append(job)
                    existing.add(profile_identity(job))
    state['discovery_pending_total'] = sum(profile_identity(job) not in discovery for job in discovery_jobs)
    save()
    if state['discovery_pending_total'] or not retry.due({'next_attempt_at': state.get('next_request_at')}):
        state['status'] = 'partial'
        atomic_json(args.ledger, state)
    return int(state['status'] != 'ok')


if __name__ == '__main__':
    raise SystemExit(main())
