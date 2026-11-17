"""One local planning and CI execution interface for every archive target."""
import argparse
import json
from pathlib import Path

from .git_state import commit_metadata, repository_name
from .registry import targets
from .runner import atomic_json, is_due, plan, run


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sonos-archive')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest='command', required=True)
    listing = sub.add_parser('targets', help='list configured target adapters without acquisition')
    listing.add_argument('--json', action='store_true')
    listing.add_argument('--matrix', action='store_true')
    listing.add_argument('--due', action='store_true')
    listing.add_argument('--select', default='all')
    execution = sub.add_parser('run', help='plan locally or execute an archive target in CI')
    execution.add_argument('target')
    execution.add_argument('--plan', action='store_true')
    execution.add_argument('--execute', action='store_true')
    execution.add_argument('--if-due', action='store_true')
    execution.add_argument('--repository')
    execution.add_argument('--report', type=Path)
    execution.add_argument('--set', action='append', default=[], metavar='NAME=JSON')
    commit = sub.add_parser('commit', help='validate and commit a CI target metadata checkpoint')
    commit.add_argument('target')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    try:
        registry = targets(root)
        if args.command == 'targets':
            selected = list(registry.values()) if args.select == 'all' else [registry[args.select]]
            selected = [t for t in selected if not args.due or is_due(root, t)]
            if args.matrix:
                print(json.dumps({'include': [{'target': t.name, 'provider': t.provider, 'lock': t.lock or t.name,
                    'java': 'java' in (*t.capabilities, *t.optional_capabilities),
                    'java_optional': 'java' in t.optional_capabilities,
                    'apkeep': 'apkeep' in (*t.capabilities, *t.optional_capabilities),
                    'apkeep_optional': 'apkeep' in t.optional_capabilities,
                    'goopdl': 'goopdl' in (*t.capabilities, *t.optional_capabilities),
                    'goopdl_optional': 'goopdl' in t.optional_capabilities} for t in selected]}))
            elif args.json:
                print(json.dumps([{'name': t.name, 'description': t.description,
                                  'capabilities': t.capabilities, 'optional_capabilities': t.optional_capabilities,
                                  'manual': t.manual} for t in selected], indent=2))
            else:
                for target in selected:
                    print(f'{target.name}: {target.description}')
            return 0
        target = registry[args.target]
        if args.command == 'commit':
            commit_metadata(root, target)
            return 0
        options = {}
        for option in args.set:
            name, value = option.split('=', 1)
            if any(word in name.lower() for word in ('password', 'token', 'secret', 'private_key')):
                raise ValueError('credentials must come from the worker environment, not command options')
            options[name] = json.loads(value)
        repository = args.repository or repository_name(root)
        if args.plan or not args.execute:
            report = plan(target, root, repository, options)
        elif args.if_due and not is_due(root, target):
            report = {'schema_version': 1, 'target': target.name, 'status': 'skipped', 'executed': False}
        else:
            report = run(target, root, repository, options=options)
        if args.report:
            atomic_json(args.report, report)
        print(json.dumps(report, indent=2))
        return 1 if report['status'] in {'failed', 'blocked', 'partial'} else 0
    except (KeyError, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
