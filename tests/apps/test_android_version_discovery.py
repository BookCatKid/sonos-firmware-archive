"""Errors and incomplete APKPure listings must not become version selectors."""
import importlib.util
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location('apk_version_discovery',
    Path(__file__).resolve().parents[2]/'scripts/apps/sweep_android_store_history.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
PACKAGE = 'com.sonos.acr2'


class AndroidVersionDiscoveryTests(unittest.TestCase):

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
