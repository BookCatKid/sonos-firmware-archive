"""Errors and incomplete APKPure listings must not become version selectors."""
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('apk_version_discovery',
    Path(__file__).resolve().parents[1]/'scripts/sweep_android_store_history.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PACKAGE = 'com.sonos.acr2'


class AndroidVersionDiscoveryTests(unittest.TestCase):
    def test_structured_listing_preserves_all_distinct_selectors(self):
        payload = {'source': 'APKPure', 'apps': {PACKAGE: {'available_versions':
            [{'version': selector} for selector in ('1.0', '2.0', '1.0', 'v3.0-beta')]}}}
        with patch.object(MODULE, 'run_apkeep', return_value=subprocess.CompletedProcess(
                ['apkeep'], 0, json.dumps(payload))) as tool:
            self.assertEqual(MODULE.discover_versions('apkeep', PACKAGE), ['1.0', '2.0', 'v3.0-beta'])
        command = tool.call_args.args[0]
        self.assertEqual(command[command.index('-o')+1], 'output_format=json')
        self.assertIn('--list-versions', command)

    def test_tool_errors_empty_or_malformed_lists_are_discovery_failures(self):
        payloads = [
            '| Invalid app response for com.sonos.acr2. Skipping...',
            json.dumps({'source': 'APKPure', 'apps': {PACKAGE: {'error': 'Invalid app response.'}}}),
            json.dumps({'source': 'APKPure', 'apps': {PACKAGE: {'available_versions': []}}}),
            json.dumps({'source': 'APKPure', 'apps': {PACKAGE: {'available_versions': [{'version': 'bad selector'}]}}}),
            json.dumps({'source': 'APKPure', 'apps': {PACKAGE: {'available_versions': [None]}}}),
            json.dumps({'source': 'other', 'apps': {PACKAGE: {'available_versions': [{'version': '1.0'}]}}}),
        ]
        for body in payloads:
            with self.subTest(body=body), patch.object(MODULE, 'run_apkeep',
                    return_value=subprocess.CompletedProcess(['apkeep'], 0, body)):
                with self.assertRaisesRegex(RuntimeError, 'could not validate APKPure'):
                    MODULE.discover_versions('apkeep', PACKAGE)
