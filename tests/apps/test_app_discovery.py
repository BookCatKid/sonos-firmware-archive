import unittest

import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, next((ROOT / 'scripts').rglob(f'{name}.py')))
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DESKTOP = load("discover_desktop_installers")
MOBILE = load("discover_mobile_installers")
version_for_url = DESKTOP.version_for_url
mobile_family = MOBILE.family


class AppDiscoveryTests(unittest.TestCase):

    def test_desktop_version_is_parsed_from_both_naming_schemes(self):
        assert version_for_url("https://example/Sonos_90.0-79210.exe") == "90.0-79210"
        assert version_for_url("https://example/SonosDesktopController131.dmg") == "classic-131"

    def test_mobile_legacy_s1_and_modern_families_are_distinct(self):
        assert mobile_family("https://example/SonosAndroidController1305.apk") == "legacy"
        assert mobile_family("https://example/Sonos_57.23-80060.apk") == "s1"
        assert mobile_family("https://example/Sonos_89.00.49.apk") == "s2"
