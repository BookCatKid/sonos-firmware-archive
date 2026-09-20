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
GOOGLE_PLAY = load("discover_google_play_listings")
ASSET_AUDIT = load("audit_release_assets")
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


def test_google_play_updated_date_parser_handles_listing_markup():
    listing = (
        '<div>Updated on</div><div class="xg1aie">Sep 14, 2026</div>'
        '<div>Developer contact</div>'
    )
    assert GOOGLE_PLAY.parse_updated_on(listing) == "Sep 14, 2026"


def test_google_play_updated_date_parser_fails_closed():
    assert GOOGLE_PLAY.parse_updated_on("no update metadata") is None


def test_android_store_receipt_exposes_every_uploaded_asset_to_audit():
    assets = ASSET_AUDIT.recorded_assets(ROOT)
    android = {
        key: value for key, value in assets.items()
        if key[0].startswith("apps-google-play-recovery-")
    }
    assert len(android) == 15
    assert all(record["bytes"] > 0 and len(record["sha256"]) == 64
               for record in android.values())
