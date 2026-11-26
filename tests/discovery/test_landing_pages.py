"""Protocol-advertised pages feed the same durable discovery and publication flow."""
import importlib.util
import json
import tempfile
import unittest
import urllib.error
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from tests.discovery.test_frontier import scan_frontier
from sonos_firmware import discovery, frontier, releases, release_series
from sonos_firmware import crawl
from sonos_firmware.catalog import validate_catalog
from sonos_firmware.crawl import crawl_manifests
from tests.apps.test_app_archive import FakeGitHub
from tests.firmware.test_firmware_archive import Response
from tests.firmware.test_upd import section

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = 'https://update.sonos.com/firmware/feed/update.upm'
PAGE = 'https://new-downloads.sonos.com/software/start.html'
PAYLOAD = 'https://new-downloads.sonos.com/releases/100.2-345-1-300.upd'
XML = ('<update_manifest version="1.1"><image model="21" version="1.0-1">'+PAGE+
       '</image></update_manifest>').encode()


def missing(url):
    raise urllib.error.HTTPError(url, 404, 'fixture absence', {}, None)


class LandingPageTests(unittest.TestCase):
    def test_store_navigation_and_namespace_urls_do_not_expand_the_firmware_frontier(self):
        store = 'https://apps.apple.com/us/app/sonos/id123'
        noise = ['https://apps.apple.com/al/iphone/today', 'https://www.sonos.com/legal/privacy',
                 'https://schema.org', 'https://other.example/unrelated-game.html']
        body = ('<a href="'+noise[0]+'">Choose region</a><a href="'+noise[1]+'">Privacy</a>'
                '<script type="application/ld+json">{"@context":"'+noise[2]+'"}</script>'
                '<a href="'+noise[3]+'">You might also like</a>'
                '<a href="'+PAYLOAD+'">Firmware</a>').encode()
        xml = ('<update_manifest version="1.1"><image is_store="1">'+store+'</image></update_manifest>').encode()
        calls = []
        def fetch(url):
            calls.append(url)
            return body if url == store else missing(url)
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': MANIFEST, 'body': xml}], [], Path(temporary), fetch=fetch, delay=0)
        self.assertFalse(set(noise) & set(calls))
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])

    def test_download_buttons_can_delegate_to_an_opaque_external_document(self):
        external = 'https://distribution.example/opaque-route'
        body = ('<a href="'+external+'">Download previous firmware versions</a>').encode()
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': PAGE, 'body': body}], [], Path(temporary), delay=0,
                fetch=lambda url: ('<a href="'+PAYLOAD+'">Download</a>').encode() if url == external else missing(url))
        self.assertTrue(any(row['url'] == external for row in graph['documents']))
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])


    def test_scope_rebuild_unions_every_retained_manifest_and_document_revision(self):
        page = 'https://distribution.example/start.html'
        old = 'https://distribution.example/old.html'
        noise = 'https://unrelated.example/privacy'
        state = {'manifests': [
            {'url': MANIFEST, 'metadata': {'landing_links': [page]}},
            {'url': MANIFEST, 'metadata': {'landing_links': []}}],
            'documents': [
                {'url': page, 'metadata': {'link_scope': 'software-distribution', 'links': [old]}},
                {'url': page, 'metadata': {'link_scope': 'software-distribution', 'links': []}}],
            'pending_manifest_urls': [old, noise]}
        with tempfile.TemporaryDirectory() as temporary:
            reachable = frontier._distribution_scope(state, Path(temporary))
        self.assertIn(page, reachable)
        self.assertIn(old, reachable)
        self.assertNotIn(noise, reachable)


    def test_escaped_browser_redirects_keep_queries_and_remove_fragments(self):
        target = 'https://distribution.example/opaque?x=1&y=2'
        script = r'window.location.href="https:\/\/distribution.example\/opaque?x=1\u0026y=2#section";'.encode()
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': PAGE, 'body': script}], [], Path(temporary), delay=0,
                fetch=lambda url: ('<a href="'+PAYLOAD+'">Download</a>').encode() if url == target else missing(url))
        self.assertTrue(any(row['url'] == target for row in graph['documents']))
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])

    def test_nextjs_manifests_follow_chunks_outside_the_saved_scripts_directory(self):
        from sonos_firmware.source_links import distribution_url, distribution_urls
        script = 'https://distribution.example/_next/static/build/_buildManifest.js'
        chunk = 'https://distribution.example/_next/static/chunks/page.js'
        self.assertEqual(distribution_urls(script, b'self.manifest={page:["static/chunks/page.js"]}'), {chunk})
        self.assertTrue(distribution_url(script, chunk))
        self.assertFalse(distribution_url(script, 'https://distribution.example/legal/privacy'))


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


    def test_actual_page_retriever_does_not_read_redirected_payload_body(self):
        from unittest.mock import MagicMock
        response = MagicMock()
        response.__enter__.return_value = response
        response.url = PAYLOAD
        response.read.side_effect = AssertionError('payload read into page cache')
        def fetch_page(request, **kwargs):
            if request.full_url != PAGE:
                raise urllib.error.HTTPError(request.full_url, 404, 'not in fixture', {}, None)
            return response
        with tempfile.TemporaryDirectory() as temporary, patch.object(
                crawl, 'open_advertised', side_effect=fetch_page) as factory:
            graph = crawl_manifests({}, [{'url': MANIFEST, 'body': XML}], [], Path(temporary), delay=0)
        response.read.assert_not_called()
        self.assertEqual([row.url for row in graph['candidates']], [PAYLOAD])
        self.assertTrue(any(call.args[0].full_url == PAGE for call in factory.call_args_list))


    def test_empty_archive_publishes_page_evidence_and_discovered_firmware(self):
        body = section(99, 0, b'synthetic discovered payload')
        github = FakeGitHub()
        spec = importlib.util.spec_from_file_location('landing_publish', ROOT/'scripts/firmware/discover_firmware.py')
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
             patch.object(release_series, 'matching_tags', side_effect=github.matching_tags), \
             patch.object(releases, 'public_release', side_effect=github.public_release), \
             patch.object(releases, 'upload_streaming', side_effect=github.upload_streaming), \
             patch.object(discovery, 'open_advertised', return_value=Response(body)):
            root = Path(temporary)
            evidence, work = root/'observations', root/'worker'
            work.mkdir()
            state = scan_frontier({}, evidence, fetch=fetch, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), work_units=1, )
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
            resumed = scan_frontier(state, evidence, fetch=fetch, probe_fn=probe,
                metadata_fetch=lambda: (b'', {}), work_units=1, )
            self.assertIn(PAGE, requests)
            self.assertEqual(len(resumed['documents']), 1)
