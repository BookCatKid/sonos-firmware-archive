"""Regression tests for original Wayback-vs-direct Sonos provenance receipts."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("audit_gpl_legacy_provenance", ROOT / "scripts/gpl/audit_gpl_legacy_provenance.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class LegacyGPLCrosscheckTests(unittest.TestCase):
    def test_comparison_never_calls_a_difference_corruption(self):
        legacy = {"releases": [{"release": "7.3", "artifacts": [
            {"filename": "x.tgz", "local_sha256": "a"*64, "local_bytes": 12, "cdx_digest_match": False},
            {"filename": "gpl.html", "local_sha256": "b"*64, "local_bytes": 15, "cdx_digest_match": True},
            {"filename": "missing.tgz", "local_sha256": "c"*64, "local_bytes": 13, "cdx_digest_match": False},
        ]}]}
        direct = {"artifacts": {"7.3": {
            "x.tgz": {"published": True, "sha256": "a"*64, "bytes": 12},
            "gpl.html": {"published": True, "sha256": "d"*64, "bytes": 16},
        }}}
        result = MODULE.audit(legacy, direct)
        count = result["totals"]
        self.assertEqual((count["compared"], count["identical"], count["different"], count["pending"]), (2, 1, 1, 1))
        self.assertEqual(count["cdx_sha1_mismatches_with_identical_direct_bytes"], 1)
        e = result["versions"]["7.3"]["entries"]
        self.assertEqual(e["x.tgz"]["state"], "identical-direct-source")
        self.assertEqual(e["gpl.html"]["state"], "historical-content-differs")
        self.assertEqual(e["missing.tgz"]["state"], "awaiting-direct-published-source")
        self.assertTrue(result["no_wayback_requests"])

    def test_retained_legacy_records_have_explicit_comparison_states_without_network(self):
        if not (ROOT/'data/gpl/wayback-catalog.json').exists():
            self.skipTest('No legacy Wayback catalog; synthetic provenance comparisons still run')
        legacy = json.loads((ROOT / "data/gpl/wayback-catalog.json").read_text())
        direct = json.loads((ROOT / "data/gpl/live/artifacts.json").read_text())
        result = MODULE.audit(legacy, direct)
        for comparison in result['versions'].values():
            counts = comparison['counts']
            self.assertEqual(counts['legacy_records'], counts['compared']+counts['pending'])
            self.assertEqual(counts['compared'], counts['identical']+counts['different'])
            self.assertTrue(all(item['state'] in {'identical-direct-source', 'historical-content-differs',
                'awaiting-direct-published-source'} for item in comparison['entries'].values()))
