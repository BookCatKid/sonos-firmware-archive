"""Offline regression tests for direct-Sonos GPL preservation."""

import gzip
import io
import json
import tarfile
import tempfile
import unittest
import urllib.request
from pathlib import Path

from sonos_firmware.gpl_archive import (
    HttpResult, candidates, capture_response, classify_index, download_artifact,
    file_sha256, index_url, offline_candidates, parse_index, sha256_bytes,
    source_url, valid_version, verify_artifact,
)

ROOT = Path(__file__).resolve().parents[1]


class FakeResponse(io.BytesIO):
    def __init__(self, data, status=200, headers=None):
        super().__init__(data)
        self.status = status
        self.headers = headers or {"Content-Type": "application/x-xz"}


class GPLArchiveTests(unittest.TestCase):
    def test_versions_are_strings_not_floats(self):
        self.assertEqual(index_url("14.12"), "https://www.sonos.com/documents/gpl/14.12/gpl.html")
        self.assertNotEqual(index_url("14.12"), index_url("14.2"))
        for bad in ("nan", "0.0", "1.", "3.100", "3/../4", "3.1/", "3.1?x=1"):
            with self.subTest(version=bad), self.assertRaises(ValueError):
                valid_version(bad)

    def test_plus_encoding_is_not_query_form_encoding(self):
        url = "https://www.sonos.com/documents/gpl/18.6/libdash-2.2+gitAUTOINC+eb7a09ce37-r0-patched.tar.xz"
        canonical = source_url(url)
        self.assertEqual(canonical, url.replace("+", "%2B"))
        self.assertEqual(source_url(canonical), canonical)
        for other in ("https://evil.example/documents/gpl/18.6/a.tar.xz",
                      "https://www.sonos.com/documents/gpl/18.6/%2Fetc.tar.xz",
                      "https://www.sonos.com/documents/gpl/18.6/%2E%2E",
                      "https://www.sonos.com/documents/gpl/18.6/a.tar.xz?x=y"):
            with self.subTest(url=other), self.assertRaises(ValueError):
                source_url(other)

    def test_real_sonos_pages_and_direct_links(self):
        for version, expected in (("7.2", 28), ("14.18", 43), ("18.6", 59)):
            with self.subTest(version=version):
                body = (ROOT / "data/gpl/live/indexes" / f"{version}.html").read_bytes()
                links = parse_index(version, body)
                self.assertEqual(len(links), expected)
                self.assertTrue(any(link["origin"] == "sonos" for link in links))
        body = (ROOT / "data/gpl/live/indexes/18.6.html").read_bytes()
        links = parse_index("18.6", body)
        self.assertEqual(len([link for link in links if "+" in link["filename"]]), 5)
        for item in links:
            if "+" in item["filename"]:
                self.assertIn("%2B", item["fetch_url"])

    def test_indexes_reject_challenge_page(self):
        page = HttpResult(200, b"<html><title>Access Denied</title></html>", "text/html", index_url("14.2"))
        self.assertEqual(classify_index("14.2", page)["state"], "inconclusive")
        self.assertEqual(classify_index("14.2", HttpResult(403, b"<Error>AccessDenied</Error>", "application/xml", ""))["state"], "access-denied")
        self.assertEqual(classify_index("14.2", HttpResult(429, b"", "", ""))["state"], "rate-limited")

    def test_verified_page_written_to_index_once(self):
        raw = b'<h1>Sonos GPL/LGPL Downloads</h1><a href="busybox.tgz">busybox</a>'
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            result = capture_response("11.3", HttpResult(200, raw, "text/html", index_url("11.3")), path)
            self.assertEqual(result["state"], "verified")
            self.assertEqual(result["sha256"], sha256_bytes(raw))
            self.assertEqual((path / "indexes/11.3.html").read_bytes(), raw)

    def test_candidate_order_with_evidence_and_neighbors(self):
        result = candidates({"14.12", "14.18"}, major_max=15, minor_max=20, evidence={"15.7": {"firmware.bin"}})
        self.assertEqual(result[0]["version"], "15.7")
        self.assertEqual(result[0]["evidence"], ["firmware.bin"])
        self.assertEqual(set(item["version"] for item in result if item["priority"] == 1), {"14.11", "14.13", "14.17", "14.19"})
        self.assertNotIn("14.12", [item["version"] for item in result])

    def test_offline_evidence_from_firmware_reference(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "docs"
            path.mkdir()
            (path / "notes.md").write_text("Firmware references https://www.sonos.com/documents/gpl/22.17/gpl.html")
            evidence = offline_candidates(Path(directory))
            self.assertIn("docs/notes.md", evidence["22.17"])

    def test_bad_artifact_and_pdf(self):
        with tempfile.TemporaryDirectory() as directory:
            f = Path(directory) / "x.tar.xz"
            f.write_bytes(b"Akamai challenge")
            self.assertFalse(verify_artifact(f, "source")[0])
            f.write_bytes(b"%PDF-1.7\nstartxref\n0\n%%EOF\n")
            self.assertTrue(verify_artifact(f, "attribution")[0])
            self.assertFalse(verify_artifact(f, "other")[0])

    @staticmethod
    def tar_bytes():
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:xz") as tar:
            raw = b"int main(void) { return 0; }\n"
            info = tarfile.TarInfo("git/source.c")
            info.size = len(raw)
            tar.addfile(info, io.BytesIO(raw))
        return buf.getvalue()

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
            first = download_artifact(link, store, min_free_bytes=0, request=resume)
            self.assertEqual(first["state"], "verified")
            self.assertEqual(first["sha256"], sha256_bytes(data))
            self.assertEqual(first["members"], 1)
            self.assertFalse(scratch.exists())
            def again(request):
                self.assertNotIn("Range", request.headers)
                return FakeResponse(data, 200, {"Content-Type": "application/x-xz", "Content-Length": str(len(data))})
            second = download_artifact(link, store, min_free_bytes=0, request=again)
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
            result = download_artifact(item, store, min_free_bytes=0, request=wrong)
            self.assertEqual(result["state"], "invalid-resume-range")
            self.assertEqual(scratch.read_bytes(), data[:7])


if __name__ == "__main__":
    unittest.main()


class GPLArchiveIntegrityTests(unittest.TestCase):
    def test_every_retained_direct_index_has_exact_integrity_and_links(self):
        catalog = json.loads((ROOT / "data/gpl/live/catalog.json").read_text())
        self.assertEqual(len(catalog["releases"]), 39)
        for version, record in catalog["releases"].items():
            with self.subTest(version=version):
                self.assertEqual(record["state"], "verified")
                body = (ROOT / "data/gpl/live/indexes" / f"{version}.html").read_bytes()
                self.assertEqual(sha256_bytes(body), record["sha256"])
                self.assertEqual(len(body), record["bytes"])
                self.assertEqual(parse_index(version, body), record["links"])
                self.assertEqual(len(set(x["filename"] for x in record["links"])), len(record["links"]))

    def test_direct_receipts_are_consistent_and_locally_verifiable(self):
        catalog = json.loads((ROOT / "data/gpl/live/catalog.json").read_text())
        receipts = json.loads((ROOT / "data/gpl/live/artifacts.json").read_text())
        for version, items in receipts["artifacts"].items():
            expected = {"gpl.html"} | {item["filename"] for item in catalog["releases"][version]["links"]}
            for filename, item in items.items():
                with self.subTest(version=version, filename=filename):
                    self.assertIn(filename, expected)
                    self.assertEqual(item["filename"], filename)
                    self.assertEqual(len(item["sha256"]), 64)
                    self.assertGreater(item["bytes"], 0)
                    if item.get("published"):
                        self.assertTrue(item.get("release_url"))
                    blob = ROOT / "artifacts/gpl-direct/sha256" / item["sha256"][:2] / item["sha256"]
                    if blob.exists():
                        self.assertEqual(file_sha256(blob), item["sha256"])
                        self.assertEqual(blob.stat().st_size, item["bytes"])

    def test_uimage_header_and_payload_crc_are_checked(self):
        import importlib.util
        import struct
        import zlib
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("analyze_gpl_firmware", ROOT / "scripts/analyze_gpl_firmware.py")
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

    def test_immutable_publication_renames_preexisting_conflicting_asset(self):
        import importlib.util
        import hashlib
        from unittest.mock import patch
        spec = importlib.util.spec_from_file_location("archive_gpl_direct", ROOT / "scripts/archive_gpl_direct.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        blob_bytes = b"sample-source-archive"
        digest = hashlib.sha256(blob_bytes).hexdigest()
        tag = "gpl-18.6"
        assets = [{"name":"example.tar.xz","size":2,"digest":"sha256:" + "a"*64,"browser_download_url":"https://example.invalid/legacy"}]
        uploaded = []
        def fake_gh(*args, **kwargs):
            if args[0] == "api":
                return json.dumps({"assets": assets})
            if args[:2] == ("release", "upload"):
                published_name = Path(args[3]).name
                uploaded.append(published_name)
                assets.append({"name":published_name,"size":len(blob_bytes),"digest":"sha256:" + digest,
                               "browser_download_url":"https://example.invalid/direct"})
                return ""
            raise AssertionError(args)
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "sha256"
            path.write_bytes(blob_bytes)
            with patch.object(module, "run_gh", side_effect=fake_gh):
                result = module.publish({"filename":"example.tar.xz","version":"18.6","bytes":len(blob_bytes),"sha256":digest},path,"owner/repo")
        self.assertTrue(result["original_name_conflict"])
        self.assertEqual(result["release_asset"], f"direct-{digest[:12]}-example.tar.xz")
        self.assertEqual(uploaded, [result["release_asset"]])
        self.assertEqual(assets[0]["name"], "example.tar.xz")
