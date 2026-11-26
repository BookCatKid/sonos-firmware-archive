"""Offline regression tests for direct-Sonos GPL preservation."""

import gzip
import io
import json
import tarfile
import tempfile
import unittest
import urllib.request
import urllib.error
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from unittest.mock import patch
from pathlib import Path

from sonos_firmware.gpl_archive import (
    HttpResult, classify_index, download_artifact,
    index_url, offline_candidates, parse_index, sha256_bytes,
    source_url, valid_version, verify_artifact,
    version_at,
)

ROOT = Path(__file__).resolve().parents[2]


class FakeResponse(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Type": "application/x-xz"}


class GPLArchiveTests(unittest.TestCase):
    def test_versions_are_strings_not_floats(self):
        self.assertEqual(index_url("14.12"), "https://www.sonos.com/documents/gpl/14.12/gpl.html")
        self.assertNotEqual(index_url("14.12"), index_url("14.2"))
        for version in ('0.0', '3.100', '1000.1000'):
            self.assertEqual(valid_version(version), version)
        for bad in ("nan", "1.", '-1.0', '3.-1', "3/../4", "3.1/", "3.1?x=1"):
            with self.subTest(version=bad), self.assertRaises(ValueError):
                valid_version(bad)


    def test_real_sonos_pages_and_direct_links(self):
        for version, expected in (("7.2", 28), ("14.18", 43), ("18.6", 59)):
            with self.subTest(version=version):
                body = (ROOT / "data/gpl/live/indexes" / f"{version}.html").read_bytes()
                links = parse_index(version, body)
                self.assertGreaterEqual(len(links), expected)
                self.assertTrue(any(link["origin"] == "sonos" for link in links))
        body = (ROOT / "data/gpl/live/indexes/18.6.html").read_bytes()
        links = parse_index("18.6", body)
        self.assertGreaterEqual(len([link for link in links if "+" in link["filename"]]), 5)
        for item in links:
            if "+" in item["filename"]:
                self.assertIn("%2B", item["fetch_url"])

    def test_indexes_reject_challenge_page(self):
        page = HttpResult(200, b"<html><title>Access Denied</title></html>", "text/html", index_url("14.2"))
        self.assertEqual(classify_index("14.2", page)["state"], "inconclusive")
        self.assertEqual(classify_index("14.2", HttpResult(403, b"<Error>AccessDenied</Error>", "application/xml", ""))["state"], "access-denied")
        self.assertEqual(classify_index("14.2", HttpResult(429, b"", "", ""))["state"], "rate-limited")


    def test_numeric_frontier_reaches_every_pair_without_size_caps(self):
        versions = {version_at(index) for index in range(45)}
        self.assertEqual(len(versions), 45)
        self.assertTrue({f'{major}.{minor}' for major in range(5) for minor in range(5)} <= versions)
        self.assertEqual(version_at(2000*2001//2+1000), '1000.1000')


    @staticmethod
    def tar_bytes():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:xz") as tar:
            raw = b"int main(void) { return 0; }\n"
            info = tarfile.TarInfo("git/source.c")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
        return buf.getvalue()

    def test_rejects_truncated_compressed_tar_footer_after_valid_members(self):
        """A listed tar member does not prove the compressed stream's checksum."""
        for compression, suffix in (("xz", "xz"), ("gz", "gz"), ("bz2", "bz2")):
            with self.subTest(compression=compression), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / f"source.tar.{suffix}"
                with tarfile.open(path, mode=f"w:{compression}") as archive:
                    raw = b"source code" * 200
                    member = tarfile.TarInfo("src/main.c")
                    member.size = len(raw)
                    archive.addfile(member, io.BytesIO(raw))
                intact = path.read_bytes()
                self.assertTrue(verify_artifact(path, "source")[0])
                path.write_bytes(intact[:-4])
                self.assertFalse(verify_artifact(path, "source")[0],
                                 "truncated compressed stream must fail even when tar headers remain readable")

    def test_throttled_download_retains_server_timing_and_closes_response(self):
        from sonos_firmware import retry
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        url = "https://www.sonos.com/documents/gpl/18.6/source.tar.xz"
        item = {"filename": "source.tar.xz", "kind": "source", "url": url,
                "fetch_url": url, "origin": "sonos"}
        for header in ("7200", format_datetime(now + timedelta(hours=2))):
            with self.subTest(header=header), tempfile.TemporaryDirectory() as directory:
                body = io.BytesIO(b"rate limited")
                error = urllib.error.HTTPError(url, 429, "slow down", {"Retry-After": header}, body)
                def limited(request, failure=error):
                    raise failure
                with patch.object(retry, '_now', return_value=now):
                    result = download_artifact(item, Path(directory), request=limited)
                self.assertEqual(result["retry_after"], 7200)
                self.assertTrue(body.closed)
                self.assertNotIn("rate limited", json.dumps(result))

    def test_metadata_maintenance_returns_retry_after_without_an_immediate_retry(self):
        from sonos_firmware import retry
        from sonos_firmware.gpl_archive import get_page
        now = datetime(2026, 11, 24, tzinfo=timezone.utc)
        url = index_url('18.6')
        for header in ('7200', format_datetime(now+timedelta(hours=2))):
            with self.subTest(header=header):
                body = io.BytesIO(b'temporary maintenance')
                error = urllib.error.HTTPError(url, 503, 'later', {'Retry-After': header}, body)
                with patch.object(retry, '_now', return_value=now), \
                     patch('urllib.request.urlopen', side_effect=error) as request, \
                     patch('sonos_firmware.gpl_archive.time.sleep') as sleep:
                    result = get_page(url)
                self.assertEqual(request.call_count, 1)
                sleep.assert_not_called()
                self.assertEqual(result.retry_after, 7200)
                self.assertEqual(classify_index('18.6', result)['retry_after'], 7200)
                self.assertTrue(body.closed)

    def test_partial_resume_sha_dedup_and_integrity(self):
        data = self.tar_bytes()
        url = "https://www.sonos.com/documents/gpl/18.6/source+git-r0-patched.tar.xz"
        link = {"filename": "source+git-r0-patched.tar.xz", "kind": "source", "url": url,
                "fetch_url": url, "origin": "sonos"}
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            scratch = store / "incomplete" / (sha256_bytes(source_url(url).encode()) + ".part")
            scratch.parent.mkdir(parents=True)
            scratch.write_bytes(data[:17])
            def resume(request):
                self.assertEqual(request.headers["Range"], "bytes=17-")
                return FakeResponse(data[17:], 206, {"Content-Type": "application/x-xz", "Content-Range": f"bytes 17-{len(data)-1}/{len(data)}", "Content-Length": str(len(data)-17)})
            first = download_artifact(link, store, request=resume)
            self.assertEqual(first["state"], "verified")
            self.assertEqual(first["sha256"], sha256_bytes(data))
            self.assertEqual(first["members"], 1)
            self.assertFalse(scratch.exists())
            def again(request):
                self.assertNotIn("Range", request.headers)
                return FakeResponse(data, 200, {"Content-Type": "application/x-xz", "Content-Length": str(len(data))})
            second = download_artifact(link, store, request=again)
            self.assertEqual(second["sha256"], first["sha256"])
            self.assertEqual(len(list((store / "sha256").rglob(first["sha256"]))), 1)

    def test_wrong_range_does_not_corrupt_partial(self):
        data = self.tar_bytes()
        url = "https://www.sonos.com/documents/gpl/18.6/source.tar.xz"
        item = {"filename":"source.tar.xz","kind":"source","url":url,"fetch_url":url,"origin":"sonos"}
        with tempfile.TemporaryDirectory() as directory:
            store = Path(directory)
            scratch = store / "incomplete" / (sha256_bytes(source_url(url).encode()) + ".part")
            scratch.parent.mkdir(parents=True)
            scratch.write_bytes(data[:7])
            def wrong(request):
                return FakeResponse(data[7:], 206, {"Content-Range": "bytes 0-100/200"})
            result = download_artifact(item, store, request=wrong)
            self.assertEqual(result["state"], "invalid-resume-range")
            self.assertEqual(scratch.read_bytes(), data[:7])


if __name__ == "__main__":
    unittest.main()


class GPLArchiveIntegrityTests(unittest.TestCase):

    def test_direct_receipts_match_advertised_artifact_identities(self):
        catalog = json.loads((ROOT / "data/gpl/live/catalog.json").read_text())
        receipts = json.loads((ROOT / "data/gpl/live/artifacts.json").read_text())
        for version, items in receipts["artifacts"].items():
            record = catalog['releases'][version]
            links = record.get('observed_links', record['links'])
            expected = {'gpl.html'} | {item['filename'] for item in links}
            expected.update('gpl-index--' + snapshot['sha256'][:16] + '.html'
                            for snapshot in record.get('index_snapshots', [])[1:])
            for filename, item in items.items():
                with self.subTest(version=version, filename=filename):
                    self.assertIn(item['filename'], expected)
                    if item.get('kind') in ('source', 'attribution'):
                        self.assertIn((item['filename'], item.get('source_url') or item.get('fetch_url')),
                                      {(link['filename'], link['url']) for link in links})
                    if "sha256" not in item:
                        # Recorded download failures (invalid-artifact, http-error,
                        # network-error, ...) carry no content hash by design; the
                        # receipt must still name its terminal state.
                        self.assertTrue(item.get("state"))
                        continue
                    self.assertEqual(len(item["sha256"]), 64)
                    self.assertGreater(item["bytes"], 0)
                    if item.get("published"):
                        self.assertTrue(item.get("release_url"))
                    # Real payload verification belongs to the scheduled CI
                    # worker. Local tests inspect receipts and generated fixtures.

    def test_uimage_header_and_payload_crc_are_checked(self):
        import importlib.util
        import struct
        import zlib
        spec = importlib.util.spec_from_file_location("analyze_gpl_firmware", ROOT / "scripts/gpl/analyze_gpl_firmware.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        payload = gzip.compress(b"Linux version 2.6.35 (unit test)\x00")
        header = struct.pack(">7I4B32s", 0x27051956, 0, 12345, len(payload), 0, 0, zlib.crc32(payload), 5, 7, 2, 1, b"Linux-2.6.35-yocto-standard")
        crc = zlib.crc32(header)
        header = header[:4] + struct.pack(">I", crc) + header[8:]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "kernel.uImage"
            path.write_bytes(header + payload)
            result = module.inspect_uimage(path)
            self.assertTrue(result["header_crc_valid"] and result["payload_crc_valid"])
            self.assertIn("Linux version 2.6.35 (unit test)", result["linux_version_strings"])
            path.write_bytes(header + payload[:-1] + b"?")
            with self.assertRaises(ValueError):
                module.inspect_uimage(path)


class GPLDiscoveryStateTests(unittest.TestCase):

    def test_probe_log_does_not_create_spurious_independent_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory) / "data/gpl/live"
            folder.mkdir(parents=True)
            (folder / "discovery.json").write_text('{"url":"https://www.sonos.com/documents/gpl/15.5/gpl.html"}')
            self.assertNotIn("15.5", offline_candidates(Path(directory)))


class GPLMultiReleaseProgressTests(unittest.TestCase):
    def test_next_archives_all_due_incomplete_versions(self):
        import argparse
        import importlib.util
        from unittest.mock import patch

        spec = importlib.util.spec_from_file_location("archive_gpl_next", ROOT / "scripts/gpl/archive_gpl_direct.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        releases = {}
        for version, filename in (("6.4", "done.tar.xz"), ("7.0", "first.tar.xz"), ("7.1", "second.tar.xz")):
            releases[version] = {"state": "verified", "links": [{
                "filename": filename, "origin": "sonos", "kind": "source",
                "url": f"https://www.sonos.com/documents/gpl/{version}/{filename}",
                "fetch_url": f"https://www.sonos.com/documents/gpl/{version}/{filename}",
            }]}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "catalog.json").write_text(json.dumps({"schema_version": 1, "releases": releases}))
            (root / "artifacts.json").write_text(json.dumps({"schema_version": 1, "artifacts": {
                "6.4": {"done.tar.xz": {"filename": "done.tar.xz", "state": "published", "published": True,
                        "source_url": "https://www.sonos.com/documents/gpl/6.4/done.tar.xz"}}
            }}))
            args = argparse.Namespace(output=root, next=True, version=[], include_upstream=False,
                                      retry_errors=False, continue_errors=True, filename=[],
                                      publish=False, store=root / "blobs",
                                      delay=2.5, repo="owner/repo")
            calls = []
            def fake_download(link, store):
                calls.append(link["filename"])
                return {"state": "http-error", "filename": link["filename"], "http_status": 403}
            with patch.object(module, "download_artifact", side_effect=fake_download), patch.object(module, "pause"):
                self.assertEqual(module.archive(args), 0)
                self.assertEqual(calls, ["first.tar.xz", "second.tar.xz"])
                saved = json.loads((root / "artifacts.json").read_text())["artifacts"]
                self.assertTrue(saved["6.4"]["done.tar.xz"]["published"])
                self.assertEqual(saved["7.0"]["first.tar.xz"]["http_status"], 403)
                self.assertEqual(saved["7.1"]["second.tar.xz"]["http_status"], 403)
            # No quota field is consulted; an empty due queue is a normal success.
            again = module.archive(args)
            self.assertEqual(again, 0)
