import importlib.util
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from sonos_firmware import releases

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "upload_raw_assets", ROOT / "scripts/upload_raw_assets.py"
)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)

PAYLOADS = {f'fixture-{kind}.bin': kind.encode() for kind in ('kernel', 'rootfs', 'preinstall')}
TAG = 'firmware-fixture'
ITEMS = {name: {'filename': name, 'package_id': 'fixture', 'release_tag': TAG, 'release_asset': name,
                'bytes': len(body), 'sha256': hashlib.sha256(body).hexdigest()} for name, body in PAYLOADS.items()}


class UploadResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        directory = self.root / 'raw/fixture'
        directory.mkdir(parents=True)
        (self.root / 'data').mkdir()
        (self.root / 'data/catalog.json').write_text(json.dumps({'raw_images': list(ITEMS.values())}))
        patcher = mock.patch.object(MODULE, 'ROOT', self.root)
        patcher.start()
        self.addCleanup(patcher.stop)
        sleeper = mock.patch.object(releases.time, 'sleep')
        sleeper.start()
        self.addCleanup(sleeper.stop)
        for name, item in ITEMS.items():
            (directory / name).write_bytes(PAYLOADS[name])
        self.argv = [
            "upload_raw_assets.py",
            "--directory", str(self.root / 'raw'),
            "--package-id", "fixture",
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
             mock.patch.object(releases, "public_release", return_value={"id": 1}), \
             mock.patch.object(releases, "remote_assets", return_value=remote), \
             mock.patch.object(releases, "verified_remote_digest",
                               side_effect=lambda asset: asset["digest"].removeprefix("sha256:")), \
             mock.patch.object(releases, "upload_asset") as upload_asset, \
             mock.patch.object(releases, "upload_streaming") as upload_streaming:
            result = MODULE.main()
        return result, upload_asset, upload_streaming

    def test_existing_verified_assets_are_skipped(self):
        result, upload_asset, upload_streaming = self._run(set(ITEMS))
        self.assertEqual(result, 0)
        upload_asset.assert_not_called()
        upload_streaming.assert_not_called()

    def test_missing_local_files_can_reconcile_verified_remote_assets(self):
        for path in (self.root / 'raw/fixture').iterdir():
            path.unlink()
        self.assertEqual(self._run(set(ITEMS))[0], 0)

    def test_missing_local_files_and_missing_remote_assets_cannot_claim_publication(self):
        for path in (self.root / 'raw/fixture').iterdir():
            path.unlink()
        with self.assertRaisesRegex(ValueError, 'missing or does not match'):
            self._run(set())

    def test_corrupt_same_size_local_files_are_not_uploaded(self):
        for path in (self.root / 'raw/fixture').iterdir():
            path.write_bytes(b'x' * path.stat().st_size)
        with self.assertRaisesRegex(ValueError, 'missing or does not match'):
            self._run(set())

    def test_requested_package_cannot_succeed_with_no_expected_assets(self):
        self.argv[-1] = 'unrecognized-package'
        with self.assertRaisesRegex(ValueError, 'no cataloged raw assets'):
            self._run(set())

    def test_existing_mismatched_asset_errors_without_clobber(self):
        result, upload_asset, upload_streaming = self._run(set(ITEMS))
        # poison one remote digest
        with mock.patch("sys.argv", self.argv), \
             mock.patch.object(releases, "public_release", return_value={"id": 1}), \
             mock.patch.object(releases, "remote_assets", return_value={
                 name: {"size": item["bytes"] + 1, "digest": "sha256:" + item["sha256"],
                        "browser_download_url": "https://example.invalid/" + name}
                 for name, item in ITEMS.items()}), \
             mock.patch.object(releases, "verified_remote_digest",
                               side_effect=lambda asset: asset["digest"].removeprefix("sha256:")), \
             mock.patch.object(releases, "upload_asset") as ua2, \
             mock.patch.object(releases, "upload_streaming") as us2:
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
        with mock.patch("sys.argv", self.argv),              mock.patch.object(releases, "public_release", return_value={"id": 1}),              mock.patch.object(releases, "remote_assets", side_effect=[{}, {}, full_remote]),              mock.patch.object(releases, "verified_remote_digest",
                               side_effect=lambda asset: asset["digest"].removeprefix("sha256:")),              mock.patch.object(releases, "upload_asset") as upload_asset,              mock.patch.object(releases, "upload_streaming") as upload_streaming:
            result = MODULE.main()
        self.assertEqual(result, 0)
        self.assertEqual(upload_asset.call_count + upload_streaming.call_count, len(ITEMS))


if __name__ == "__main__":
    unittest.main()
