import unittest

from sonos_firmware.metadata_sweep import BASELINE, PROFILE_FIELDS, build_profiles


class MetadataSweepTests(unittest.TestCase):
    def test_profile_plan_is_deterministic_unique_and_bounded(self):
        catalog = {
            "packages": [{"version": "97.1-80312"}],
            "evidence": [{"version": "86.8-78270"}],
        }
        first = build_profiles(catalog, 800)
        second = build_profiles(catalog, 800)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 800)
        self.assertEqual(
            len({tuple(item[field] for field in PROFILE_FIELDS) for item in first}), 800
        )
        self.assertEqual(first[0], BASELINE)
        self.assertIn(
            {"cmaj": 97, "cmin": 1, "cbld": 80312, "subm": 1, "rev": 1, "reg": 2},
            first,
        )


if __name__ == "__main__":
    unittest.main()
