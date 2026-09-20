import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load(name: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


DESKTOP = load("discover_desktop_installers")
MOBILE = load("discover_mobile_installers")
family_for_wayback_url = DESKTOP.family_for_wayback_url
version_for_url = DESKTOP.version_for_url
mobile_family = MOBILE.family


def test_desktop_families_are_not_inferred_as_all_modern():
    assert family_for_wayback_url("https://example/SonosDesktopController92.exe") == "legacy"
    assert family_for_wayback_url("https://example/SonosDesktopController1123.exe") == "s1"
    assert family_for_wayback_url("https://example/SonosDesktopController131.exe") == "s2"
    assert family_for_wayback_url("https://example/Sonos_57.22-59130.dmg") == "s1"
    assert family_for_wayback_url("https://example/Sonos_90.0-79210.dmg") == "s2"


def test_desktop_version_is_parsed_from_both_naming_schemes():
    assert version_for_url("https://example/Sonos_90.0-79210.exe") == "90.0-79210"
    assert version_for_url("https://example/SonosDesktopController131.dmg") == "classic-131"


def test_mobile_legacy_s1_and_modern_families_are_distinct():
    assert mobile_family("https://example/SonosAndroidController1305.apk") == "legacy"
    assert mobile_family("https://example/Sonos_57.23-80060.apk") == "s1"
    assert mobile_family("https://example/Sonos_89.00.49.apk") == "s2"
