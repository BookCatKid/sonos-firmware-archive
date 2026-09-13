import importlib.util
import unittest
from pathlib import Path


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "diagnose_smapi_playback.py"
SPEC = importlib.util.spec_from_file_location("diagnose_smapi_playback", SCRIPT)
diagnostic = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(diagnostic)


class SmapiDiagnosticTests(unittest.TestCase):
    def test_classifies_apple_music_without_retaining_uri(self):
        self.assertEqual(
            diagnostic.classify_source("x-sonos-http:track.mp4?sid=204&flags=0&sn=1"),
            "apple_music_smapi",
        )
        self.assertEqual(diagnostic.classify_source("x-sonos-http:track.mp4?sid=201"), "other_smapi_http")

    def test_parses_wireless_counters(self):
        result = diagnostic.parse_network_status(
            "IEEE channel: 9\nNoise Floor: -107 dBm\nNoise Floor: -108 dBm\n"
            "PHY errors since last reading/reset: 4710118\nOFDM ANI level: 1\n"
        )
        self.assertEqual(result["phy_errors"], 4710118)
        self.assertEqual(result["noise_floor_dbm"], [-107, -108])
        self.assertEqual(result["channel"], 9)

    def test_detects_timeline_stall_while_transport_claims_playing(self):
        previous = {
            "transport_state": "PLAYING",
            "transport_status": "OK",
            "track_number": 4,
            "position_seconds": 30,
            "duration_seconds": 180,
        }
        current = {**previous, "position_seconds": 30}
        self.assertIn("timeline_stall", [event["kind"] for event in diagnostic.automatic_events(previous, current)])

    def test_detects_premature_track_change(self):
        previous = {
            "transport_state": "PLAYING",
            "transport_status": "OK",
            "track_number": 4,
            "position_seconds": 30,
            "duration_seconds": 180,
        }
        current = {**previous, "track_number": 5, "position_seconds": 0}
        self.assertIn(
            "premature_track_change",
            [event["kind"] for event in diagnostic.automatic_events(previous, current)],
        )


if __name__ == "__main__":
    unittest.main()
