"""Plan locally; execute source adapters in an ephemeral CI worker."""
from __future__ import annotations

import json
import importlib.util
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import Context
from .registry import providers
from .providers import validate_options
from ..storage import atomic_json
from ..deadline import winding_down


def state_path(root, target):
    return root / 'data/platform/targets' / (target.name + '.json')


def is_due(root, target, now=None):
    if target.manual:
        return False
    path = state_path(root, target)
    if not path.exists():
        return True
    value = json.loads(path.read_text())
    next_due = value.get('next_due')
    return not next_due or datetime.fromisoformat(next_due) <= (now or datetime.now(timezone.utc))


def plan(target, root, repository, options=None, provider_map=None):
    settings = {**target.options, **(options or {})}
    validate_options(target.provider, settings)
    context = Context(root, Path('$RUNNER_TEMP') / target.name, repository, target, True,
                      settings,
                      Path('$RUNNER_TEMP') / 'sonos-discovery-metadata' / target.name)
    implementation = (provider_map or providers()).get(target.provider)
    if implementation is None:
        raise ValueError(f'unknown provider: {target.provider}')
    return {'schema_version': 1, 'target': target.name, 'provider': target.provider,
            'status': 'planned', 'executed': False, 'capabilities': list(target.capabilities),
            'optional_capabilities': list(target.optional_capabilities),
            'metadata': list(target.metadata), 'steps': [asdict(step) for step in implementation(context)]}


def missing_capabilities(target, options):
    missing = []
    for capability in target.capabilities:
        if capability == 'goopdl' and importlib.util.find_spec('goopdl') is None:
            missing.append(capability)
        if capability in {'java', 'apkeep'} and not shutil.which(options.get(capability, capability)):
            missing.append(capability)
    return missing


def redact(text, environment):
    values = []
    for name, value in environment.items():
        if any(token in name.upper() for token in ('TOKEN', 'PASSWORD', 'PRIVATE_KEY', 'SECRET')) and len(value) >= 8:
            values.extend([value, *(line for line in value.splitlines() if len(line) >= 8)])
    for value in sorted(set(values), key=len, reverse=True):
        text = text.replace(value, '[REDACTED]')
    return text


def _source_incomplete(document):
    if not isinstance(document, dict):
        return False
    return (document.get('status') in {'partial', 'incomplete', 'error', 'blocked', 'failed', 'unavailable'} or
            bool(document.get('incomplete') or document.get('discovery_incomplete') or
                 document.get('errors') or document.get('probe_errors') or document.get('skipped')))


def _superseded_workflow(root, environment):
    revision = environment.get('GITHUB_WORKFLOW_SHA', '')
    if (environment.get('GITHUB_EVENT_NAME') not in {'push', 'schedule'} or
            not environment.get('GITHUB_WORKFLOW_REF', '').endswith('/.github/workflows/archive.yml@refs/heads/main') or
            not re.fullmatch(r'(?:[0-9a-f]{40}|[0-9a-f]{64})', revision)):
        return False
    path = '.github/workflows/archive.yml'
    try:
        blobs = subprocess.check_output(['git', 'rev-parse', revision+':'+path, 'HEAD:'+path],
            cwd=root, text=True, stderr=subprocess.DEVNULL, timeout=10).splitlines()
    except (OSError, subprocess.SubprocessError):
        return False
    return len(blobs) == 2 and blobs[0] != blobs[1]


def run(target, root, repository, *, environment=None, options=None, provider_map=None, execute=None):
    environment = dict(os.environ if environment is None else environment)
    if environment.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('artifact acquisition runs in CI; use --plan for local target development')
    if _superseded_workflow(root, environment):
        # The current main push schedules every automatic target. A queued old
        # definition must not occupy its lock or overwrite newer target state.
        return {'schema_version': 1, 'target': target.name, 'provider': target.provider,
                'status': 'skipped', 'executed': False, 'reason': 'workflow definition superseded'}
    options = {**target.options, **(options or {})}
    validate_options(target.provider, options)
    started = datetime.now(timezone.utc)
    report = {'schema_version': 1, 'target': target.name, 'provider': target.provider,
              'status': 'ok', 'executed': True, 'started_at': started.isoformat(),
              'steps': [], 'errors': [], 'pending': []}
    missing = missing_capabilities(target, options)
    if missing:
        report.update(status='blocked', executed=False, pending=missing)
    else:
        implementation = (provider_map or providers()).get(target.provider)
        if implementation is None:
            raise ValueError(f'unknown provider: {target.provider}')
        parent = environment.get('RUNNER_TEMP')
        with tempfile.TemporaryDirectory(prefix=f'sonos-{target.name}-', dir=parent) as temporary:
            work = Path(temporary)
            child_env = {**environment, 'TMPDIR': str(work), 'TMP': str(work), 'TEMP': str(work)}
            observations = Path(parent) / 'sonos-discovery-metadata' / target.name if parent else work / 'observations'
            context = Context(root, work, repository, target, True, options, observations)
            failed = False
            for step in implementation(context):
                record = {'name': step.name, 'status': 'skipped'}
                label = redact(f'[{target.name}] {step.name}', environment)
                if winding_down():
                    report['steps'].append(record)
                    report['pending'].append({'step': step.name, 'reason': 'four-hour soft wind-down requested'})
                    print(f'{label}: deferred at soft wind-down', file=sys.stderr, flush=True)
                    continue
                if failed and not step.after_failure:
                    report['steps'].append(record)
                    print(f'{label}: skipped after an earlier failure', file=sys.stderr, flush=True)
                    continue
                print(f'{label}: starting', file=sys.stderr, flush=True)
                log = work / (str(len(report['steps'])) + '.log')
                step_started = time.time_ns()
                try:
                    if execute:
                        code, output = execute(step, context, child_env)
                        output_tail = redact(output, environment)[-4000:]
                        has_traceback = 'Traceback (most recent call last)' in output
                    else:
                        with log.open('w') as stream:
                            process = subprocess.Popen(step.command, cwd=root, env=child_env,
                                                       stdout=stream, stderr=subprocess.STDOUT)
                            process.wait()
                        code = process.returncode
                        output_tail = ''
                        has_traceback = False
                        with log.open(errors='replace') as stream:
                            for line in stream:
                                has_traceback |= 'Traceback (most recent call last)' in line
                                output_tail = (output_tail + redact(line, environment))[-4000:]
                    record.update(exit_code=code, status='ok' if code in step.accepted_codes else 'failed',
                                  output_tail=output_tail)
                    if code not in step.accepted_codes:
                        failed = True
                        record['failure_kind'] = 'process-exit'
                        report['errors'].append({'step': step.name, 'exit_code': code, 'kind': 'process-exit'})
                    if step.report and Path(step.report).exists():
                        native = json.loads(Path(step.report).read_text())
                        record['source_status'] = native.get('status') if isinstance(native, dict) else None
                        if _source_incomplete(native):
                            if (code == 1 and code not in step.accepted_codes and
                                    Path(step.report).stat().st_mtime_ns >= step_started and
                                    not has_traceback):
                                record['failure_kind'] = 'source-reported-incomplete'
                                report['errors'][-1]['kind'] = record['failure_kind']
                            record['status'] = 'partial' if code in step.accepted_codes else 'failed'
                            report['pending'].append({'step': step.name, 'reason': 'source reports incomplete work'})
                except Exception as error:
                    failed = True
                    record.update(status='failed', failure_kind='exception', error=redact(str(error), environment))
                    report['errors'].append({'step': step.name, 'kind': 'exception', 'error': record['error']})
                report['steps'].append(record)
                print(f'{label}: {record["status"]}', file=sys.stderr, flush=True)
            if report['errors']:
                report['status'] = 'partial' if any(s['status'] in {'ok', 'partial'} for s in report['steps']) else 'failed'
            elif report['pending']:
                report['status'] = 'partial'
    finished = datetime.now(timezone.utc)
    report['finished_at'] = finished.isoformat()
    # Incomplete runs retry promptly; successful runs use the target's cadence.
    interval = target.interval_hours if report['status'] == 'ok' else min(1, target.interval_hours)
    report['next_due'] = (finished + timedelta(hours=interval)).isoformat()
    atomic_json(state_path(root, target), report)
    return report
