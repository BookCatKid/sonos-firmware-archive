import unittest
from pathlib import Path

from sonos_firmware.catalog import validate_catalog


class CatalogTests(unittest.TestCase):
    def test_repository_catalog(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(validate_catalog(root), [])


if __name__ == "__main__":
    unittest.main()

