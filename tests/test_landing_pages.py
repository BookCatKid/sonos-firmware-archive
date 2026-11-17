"""Protocol-advertised pages feed the same durable discovery and publication flow."""
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import discovery, frontier, releases
from sonos_firmware import crawl
from sonos_firmware.catalog import validate_catalog
from sonos_firmware.crawl import crawl_manifests
from sonos_firmware.source_links import discover_urls
from test_app_archive import FakeGitHub
from test_firmware_archive import Response
from test_upd import section

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = 'https://update.sonos.com/firmware/feed/update.upm'
PAGE = 'https://new-downloads.sonos.com/software/start.html'
PAYLOAD = 'https://new-downloads.sonos.com/releases/100.2-345-1-300.upd'
XML = ('<update_manifest version="1.1"><image model="21" version="1.0-1">'+PAGE+
       '</image></update_manifest>').encode()


def missing(url):
    raise urllib.error.HTTPError(url, 404, 'fixture absence', {}, None)


class LandingPageTests(unittest.TestCase):
    def test_html_base_meta_refresh_and_script_links_share_web_discovery(self):
        body = b'''<base href="https://new-downloads.sonos.com/releases/">
            <a href=100.2-345-1-300.upd?x=1&amp;y=2#ignored>Download</a>
            <meta http-equiv="refresh" content="0; URL='next.html'">
            <script>location.href = "controller.apk";</script>'''
        self.assertEqual(discover_urls(PAGE, body), {
            PAYLOAD+'?x=1&y=2', 'https://new-downloads.sonos.com/releases/next.html',
            'https://new-downloads.sonos.com/releases/controller.apk'})

    def test_manifest_page_and_script_find_payload_without_inheriting_app_model(self):
        script = PAGE.replace('start.html', 'downloads.js')
        requests = []
        pages = {PAGE: b'<script src="downloads.js"></script>',
                 script: ('const firmware = "'+PAYLOAD+'";').encode()}
        def fetch(url):
            requests.append(url)
            return pages[url] if url in pages else missing(url)
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': MANIFEST, 'body': XML}], [], Path(temporary), fetch=fetch, delay=0)
            self.assertTrue(all(Path(row['snapshot']).is_file() for row in graph['documents']))
        self.assertEqual({row['url'] for row in graph['documents']}, {PAGE, script})
        self.assertEqual([(row.url, row.version, row.package_model) for row in graph['candidates']],
                         [(PAYLOAD, '100.2-345', 300)])
        self.assertEqual({row['kind'] for row in graph['failures']}, {'not-found'})
        self.assertNotIn(PAYLOAD, requests)

    def test_legacy_html_and_script_relative_locations_keep_other_links(self):
        for body in (b'<![unknown]><a href="next.html">Next</a>',
                     b'<script>window.location.href="next.html";</script>',
                     b'<a href="next.html?x=1&amp;y=2">Next</a>'):
            with self.subTest(body=body):
                expected = PAGE.replace('start.html', 'next.html')
                if b'?x=' in body:
                    expected += '?x=1&y=2'
                self.assertEqual(discover_urls(PAGE, body), {expected})

    def test_valueless_refresh_attributes_do_not_discard_other_links(self):
        for meta in ('<meta http-equiv>', '<meta http-equiv="refresh" content>'):
            with self.subTest(meta=meta):
                body = (meta+'<a href="'+PAYLOAD+'">Firmware</a>').encode()
                self.assertEqual(discover_urls(PAGE, body), {PAYLOAD})

    def test_escaped_script_urls_never_emit_a_truncated_prefix(self):
        for url in (PAYLOAD, 'https://new-downloads.sonos.com/_next/static/chunks/main.js'):
            with self.subTest(url=url):
                body = ('const location = "'+url+'?x=1\\u0026y=2";').encode()
                self.assertEqual(discover_urls(PAGE, body), {url+'?x=1&y=2'})

    def test_budget_resume_retains_a_page_discovered_at_the_end_of_the_batch(self):
        second = PAGE.replace('start.html', 'next.html')
        with tempfile.TemporaryDirectory() as temporary:
            first = crawl_manifests({}, [{'url': MANIFEST, 'body': XML}], [], Path(temporary),
                fetch=lambda url: b'<a href="next.html">Next</a>', delay=0, max_manifests=2)
            self.assertEqual(first['pending_manifest_urls'], [second])
            self.assertTrue(first['limits_reached'])
            def fetch(url):
                return ('<a href="'+PAYLOAD+'">Firmware</a>').encode() if url == second else missing(url)
            resumed = crawl_manifests({}, [{'url': url} for url in first['pending_manifest_urls']], [],
                Path(temporary), fetch=fetch, delay=0)
        self.assertEqual([row.url for row in resumed['candidates']], [PAYLOAD])
        self.assertFalse(resumed['limits_reached'])

    def test_page_redirect_yields_a_payload_lead_without_treating_bytes_as_html(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': MANIFEST, 'body': XML}], [], Path(temporary),
                fetch=lambda url: (None, PAYLOAD, None) if url == PAGE else missing(url), delay=0)
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])
        self.assertEqual(graph['documents'], [])
        self.assertTrue(any(row['reason'] == 'landing-page-redirect' for row in graph['edges']))

    def test_actual_page_retriever_does_not_read_redirected_payload_body(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = PAYLOAD
        response.read.side_effect = AssertionError('payload read into page cache')
        with tempfile.TemporaryDirectory() as temporary, patch.object(crawl, 'open_advertised') as factory:
            factory.return_value = response
            graph = crawl_manifests({}, [{'url': MANIFEST, 'body': XML}], [], Path(temporary),
                delay=0, max_manifests=2)
        response.read.assert_not_called()
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])
        self.assertEqual(factory.call_args.args[0].full_url, PAGE)

    def test_private_payloads_and_empty_pages_remain_explicit_gaps(self):
        for body in (b'<a href="https://127.0.0.1/firmware.upd">Download</a>', b'<html>No links</html>'):
            with self.subTest(body=body), tempfile.TemporaryDirectory() as temporary:
                graph = crawl_manifests({}, [{'url': PAGE, 'body': body}], [], Path(temporary),
                    fetch=lambda url: self.fail('external URL fetched'), delay=0)
                self.assertEqual(len(graph['documents']), 1)
                self.assertEqual(graph['candidates'], [])
                self.assertTrue(any(row['kind'] == 'unsupported' for row in graph['failures']))

    def test_page_requests_obey_host_pauses_and_keep_pending_work(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': PAGE}], [], Path(temporary),
                fetch=lambda url: self.fail('paused URL fetched'), delay=0,
                paused_hosts=('new-downloads.sonos.com',))
        self.assertEqual(graph['pending_manifest_urls'], [PAGE])

    def test_malformed_link_cannot_discard_other_download_leads(self):
        body = ('<a href="https://[invalid/firmware.upd">Bad</a>'
                '<a href="'+PAYLOAD+'">Good</a>').encode()
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': PAGE, 'body': body}], [], Path(temporary),
                fetch=missing, delay=0)
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])
        self.assertTrue(any('malformed URL' in row['error'] for row in graph['failures']))

    def test_store_image_links_are_followed_and_missing_external_stores_are_recorded(self):
        store = 'https://apps.apple.com/us/app/sonos/id123'
        xml = ('<update_manifest version="1.1"><image is_store="1">'+PAGE+'</image>'
               '<image is_store="1">'+store+'</image></update_manifest>').encode()
        def fetch(url):
            return ('<a href="'+PAYLOAD+'">Firmware</a>').encode() if url == PAGE else missing(url)
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': MANIFEST, 'body': xml}], [], Path(temporary),
                fetch=fetch, delay=0)
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])
        self.assertTrue(any(row['url'] == store and row['kind'] == 'not-found' for row in graph['failures']))

    def test_extensionless_xml_manifest_is_parsed_by_its_content(self):
        route = PAGE.rsplit('/', 1)[0]+'/manifest'
        xml = ('<update_manifest version="1.1"><image model="300" version="100.2-345">'+
               PAYLOAD+'</image></update_manifest>').encode()
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': route, 'body': xml}], [], Path(temporary),
                fetch=missing, delay=0)
        self.assertEqual(len(graph['manifests']), 1)
        self.assertEqual(graph['documents'], [])
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])

    def test_empty_archive_publishes_page_evidence_and_discovered_firmware(self):
        body = section(99, 0, b'synthetic discovered payload')
        github = FakeGitHub()
        spec = importlib.util.spec_from_file_location('landing_publish', ROOT/'scripts/discover_firmware.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        requests = []
        def fetch(url):
            requests.append(url)
            if url == MANIFEST:
                return XML
            if url == PAGE:
                return ('<a href="'+PAYLOAD+'">Firmware</a>').encode()
            return missing(url)
        def probe(candidate):
            return {**asdict(candidate), 'available': candidate.url == PAYLOAD,
                    'http_status': 200 if candidate.url == PAYLOAD else 404}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frontier, 'protocol_roots', return_value=[MANIFEST]), \
             patch.object(frontier.time, 'sleep'), \
             patch.object(releases, 'remote_assets', side_effect=github.assets), \
             patch.object(releases.subprocess, 'run', side_effect=github.command), \
             patch.object(discovery, 'open_advertised', return_value=Response(body)):
            root = Path(temporary)
            evidence, work = root/'observations', root/'worker'
            work.mkdir()
            state = frontier.discover({}, evidence, fetch=fetch, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), inference_limit=1, model_max=0)
            self.assertEqual(len(state['documents']), 1)
            self.assertEqual(github.releases, {})
            self.assertFalse(module.preserve(state, root/'data/catalog.json', root/'state.json',
                evidence, work, 'fresh/archive'))
            catalog = json.loads((root/'data/catalog.json').read_text())
            self.assertEqual(len(catalog['source_documents']), 1)
            document = catalog['source_documents'][0]
            self.assertEqual(document['source_url'], PAGE)
            self.assertEqual(document['publication']['status'], 'verified')
            self.assertEqual(document['metadata']['links'], [PAYLOAD])
            self.assertEqual(len(github.uploads), 3)
            self.assertEqual(catalog['packages'][0]['source_url'], PAYLOAD)
            self.assertEqual(validate_catalog(root), [])
            self.assertFalse(module.preserve(state, root/'data/catalog.json', root/'state.json',
                evidence, work, 'fresh/archive'))
            self.assertEqual(len(github.uploads), 3)
            requests.clear()
            resumed = frontier.discover(state, evidence, fetch=fetch, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), inference_limit=1, model_max=0)
            self.assertIn(PAGE, requests)
            self.assertEqual(len(resumed['documents']), 1)
