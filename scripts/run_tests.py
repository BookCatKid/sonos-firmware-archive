#!/usr/bin/env python3
"""Run the fixture suite with accidental network access treated as a failure."""
import argparse
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pattern', default='test*.py')
    parser.add_argument('-v', '--verbose', action='store_true')
    args = parser.parse_args()
    guard = str(ROOT / 'tools/offline_tests')
    sys.path[:0] = [guard, str(ROOT / 'src')]
    from network_guard import install
    with tempfile.TemporaryDirectory(prefix='sonos-offline-tests-') as temporary:
        events = Path(temporary) / 'network-events'
        os.environ['SONOS_TEST_NETWORK_EVENTS'] = str(events)
        os.environ['PYTHONPATH'] = os.pathsep.join([guard, str(ROOT / 'src'), os.environ.get('PYTHONPATH', '')])
        os.environ['GIT_ALLOW_PROTOCOL'] = 'file'
        install()
        suite = unittest.defaultTestLoader.discover(str(ROOT / 'tests'), pattern=args.pattern)
        result = unittest.TextTestRunner(verbosity=2 if args.verbose else 1).run(suite)
        attempts = events.read_text().splitlines() if events.exists() else []
        if attempts:
            print(f'Offline assertion failed: {len(attempts)} unmocked network/process attempts: '
                  + ', '.join(sorted(set(attempts))), file=sys.stderr)
        return int(not result.wasSuccessful() or bool(attempts))


if __name__ == '__main__':
    raise SystemExit(main())
