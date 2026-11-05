import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upload_raw_assets", ROOT / "scripts/upload_raw_assets.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

CATALOG = json.loads((ROOT / "data/catalog.json").read_text(encoding="utf-8"))
ITEMS = {
    item["filename"]: item
    for item in CATALOG["raw_images"]
    if item.get("package_id") == "86.8-78270-1-8" and item.get("release_tag") and item.get("release_asset")
}
TAG = next(iter(ITEMS.values()))["release_tag"]


class UploadResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        directory = Path(self.tmp.name) / "86.8-78270-1-8"
        directory.mkdir()
        for name, item in ITEMS.items():
            (directory / name).write_bytes(b"x" * min(item["bytes"], 16))
        self.argv = [
            "upload_raw_assets.py",
            "--directory", self.tmp.name,
            "--package-id", "86.8-78270-1-8",
        ]

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, assets_by_name):
        remote = {
            name: {"size": item["bytes"], "digest": "sha256:" + item["sha256"],
                   "browser_download_url": "https://example.invalid/" + name}
            for name, item in ITEMS.items()
            if name in assets_by_name
        }
        with mock.patch("sys.argv", self.argv), \
             mock.patch.object(MODULE, "public_release", return_value={"id": 1}), \
             mock.patch.object(MODULE, "remote_assets", return_value=remote), \
             mock.patch.object(MODULE, "verified_remote_digest",
                               side_effect=lambda asset: asset["digest"].removeprefix("sha256:")), \
             mock.patch.object(MODULE, "upload_asset") as upload_asset, \
             mock.patch.object(MODULE, "upload_streaming") as upload_streaming:
            result = MODULE.main()
        return result, upload_asset, upload_streaming

    def test_existing_verified_assets_are_skipped(self):
        result, upload_asset, upload_streaming = self._run(set(ITEMS))
        self.assertEqual(result, 0)
        upload_asset.assert_not_called()
        upload_streaming.assert_not_called()

    def test_existing_mismatched_asset_errors_without_clobber(self):
        result, upload_asset, upload_streaming = self._run(set(ITEMS))
        # poison one remote digest
        with mock.patch("sys.argv", self.argv), \
             mock.patch.object(MODULE, "public_release", return_value={"id": 1}), \
             mock.patch.object(MODULE, "remote_assets", return_value={
                 name: {"size": item["bytes"] + 1, "digest": "sha256:" + item["sha256"],
                        "browser_download_url": "https://example.invalid/" + name}
                 for name, item in ITEMS.items()}), \
             mock.patch.object(MODULE, "verified_remote_digest",
                               side_effect=lambda asset: asset["digest"].removeprefix("sha256:")), \
             mock.patch.object(MODULE, "upload_asset") as ua2, \
             mock.patch.object(MODULE, "upload_streaming") as us2:
            with self.assertRaises(RuntimeError):
                MODULE.main()
        ua2.assert_not_called()
        us2.assert_not_called()

    def test_absent_assets_are_uploaded(self):
        full_remote = {
            name: {"size": item["bytes"], "digest": "sha256:" + item["sha256"],
                   "browser_download_url": "https://example.invalid/" + name}
            for name, item in ITEMS.items()
        }
        with mock.patch("sys.argv", self.argv),              mock.patch.object(MODULE, "public_release", return_value={"id": 1}),              mock.patch.object(MODULE, "remote_assets", side_effect=[{}, {}, full_remote]),              mock.patch.object(MODULE, "verified_remote_digest",
                               side_effect=lambda asset: asset["digest"].removeprefix("sha256:")),              mock.patch.object(MODULE, "upload_asset") as upload_asset,              mock.patch.object(MODULE, "upload_streaming") as upload_streaming:
            result = MODULE.main()
        self.assertEqual(result, 0)
        self.assertEqual(upload_asset.call_count + upload_streaming.call_count, len(ITEMS))


if __name__ == "__main__":
    unittest.main()
