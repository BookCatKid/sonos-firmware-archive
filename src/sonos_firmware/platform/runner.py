"""Plan locally; execute source adapters in an ephemeral CI worker."""
from __future__ import annotations

import json
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .model import Context
from .registry import providers
from ..storage import atomic_json


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
    context = Context(root, Path('$RUNNER_TEMP') / target.name, repository, target, True,
                      {**target.options, **(options or {})},
                      Path('$RUNNER_TEMP') / 'sonos-discovery-metadata' / target.name)
    implementation = (provider_map or providers()).get(target.provider)
    if implementation is None:
        raise ValueError(f'unknown provider: {target.provider}')
    return {'schema_version': 1, 'target': target.name, 'provider': target.provider,
            'status': 'planned', 'executed': False, 'capabilities': list(target.capabilities),
            'optional_capabilities': list(target.optional_capabilities),
            'metadata': list(target.metadata), 'steps': [asdict(step) for step in implementation(context)]}


def missing_capabilities(target, environment, options):
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


def run(target, root, repository, *, environment=None, options=None, provider_map=None, execute=None):
    environment = dict(os.environ if environment is None else environment)
    if environment.get('GITHUB_ACTIONS') != 'true':
        raise ValueError('artifact acquisition runs in CI; use --plan for local target development')
    options = {**target.options, **(options or {})}
    started = datetime.now(timezone.utc)
    report = {'schema_version': 1, 'target': target.name, 'provider': target.provider,
              'status': 'ok', 'executed': True, 'started_at': started.isoformat(),
              'steps': [], 'errors': [], 'pending': []}
    missing = missing_capabilities(target, environment, options)
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
                if failed and not step.after_failure:
                    report['steps'].append(record)
                    print(f'{label}: skipped after an earlier failure', file=sys.stderr, flush=True)
                    continue
                print(f'{label}: starting', file=sys.stderr, flush=True)
                log = work / (str(len(report['steps'])) + '.log')
                try:
                    if execute:
                        code, output = execute(step, context, child_env)
                    else:
                        with log.open('w') as stream:
                            completed = subprocess.run(step.command, cwd=root, env=child_env,
                                stdout=stream, stderr=subprocess.STDOUT, timeout=options.get('step_timeout', 14400))
                        code, output = completed.returncode, log.read_text(errors='replace')
                    record.update(exit_code=code, status='ok' if code in step.accepted_codes else 'failed',
                                  output_tail=redact(output, environment)[-4000:])
                    if code not in step.accepted_codes:
                        failed = True
                        report['errors'].append({'step': step.name, 'exit_code': code})
                    if step.report and Path(step.report).exists():
                        native = json.loads(Path(step.report).read_text())
                        if _source_incomplete(native):
                            record['status'] = 'partial' if code in step.accepted_codes else 'failed'
                            report['pending'].append({'step': step.name, 'reason': 'source reports incomplete work'})
                except Exception as error:
                    failed = True
                    record.update(status='failed', error=redact(str(error), environment))
                    report['errors'].append({'step': step.name, 'error': record['error']})
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
