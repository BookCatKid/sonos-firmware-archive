"""Regression tests for original Wayback-vs-direct Sonos provenance receipts."""
import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("audit_gpl_legacy_provenance", ROOT / "scripts/audit_gpl_legacy_provenance.py")
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

    def test_real_7_3_filenames_and_hashes_match_direct_sonos_without_network(self):
        legacy = json.loads((ROOT / "data/gpl/wayback-catalog.json").read_text())
        direct = json.loads((ROOT / "data/gpl/live/artifacts.json").read_text())
        result = MODULE.audit(legacy, direct)
        v = result["versions"]["7.3"]
        self.assertEqual(v["counts"]["compared"], 22)
        self.assertEqual(v["counts"]["identical"], 21)
        self.assertEqual(v["counts"]["different"], 1)
        self.assertEqual(v["counts"]["cdx_sha1_mismatches_with_identical_direct_bytes"], 16)
        self.assertEqual(v["entries"]["gpl.html"]["state"], "historical-content-differs")
        self.assertTrue(all(item["state"] == "identical-direct-source" for name, item in v["entries"].items() if name != "gpl.html"))
