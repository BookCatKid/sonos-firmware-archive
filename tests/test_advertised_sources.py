"""Advertised CDNs survive discovery, restarts and acquisition without URL lists."""
import base64
import io
import json
import struct
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from sonos_firmware import discovery, firmware_archive, frontier, installers, source_urls
from sonos_firmware.crawl import crawl_manifests
from sonos_firmware.package_history import ManifestHistory
from sonos_firmware.metadata import UPDATE_METADATA_URL
from test_desktop_index_discovery import MODULE as desktop
from test_firmware_archive import captured
from test_upd import section

ROOT = 'https://update.sonos.com/current/update.upm'
PAGE = 'https://distribution.example/start.html'
PAYLOAD = 'https://another-cdn.example/1.0-1-1-17.upd'
WITNESS = {'repository': 'microsoft/winget-pkgs', 'git_blob': 'a'*40,
           'path': 'manifests/s/Sonos/Controller/1/Sonos.installer.yaml'}
PUBLIC_DNS = [(2, 1, 6, '', ('8.8.8.8', 443))]


def response(body, url):
    result = io.BytesIO(body)
    result.geturl = lambda: url
    result.url, result.status, result.headers = url, 200, {}
    return result


class AdvertisedSourceTests(unittest.TestCase):
    def test_external_url_requires_a_trusted_source_and_public_destination(self):
        with patch.object(source_urls._OPENER, 'open') as network:
            for url, witness in ((PAYLOAD, None), (PAYLOAD, PAGE),
                                 ('https://127.0.0.1/file.upd', ROOT),
                                 ('https://224.0.0.1/file.upd', ROOT),
                                 ('https://user:pass@cdn.example/file.upd', ROOT),
                                 ('http://cdn.example/file.upd', ROOT),
                                 (PAYLOAD, {**WITNESS, 'repository': 'someone/else'}),
                                 (PAYLOAD, {**WITNESS, 'path': 'manifests/s/Unrelated/client.yaml'})):
                with self.subTest(url=url, witness=witness), self.assertRaises(ValueError):
                    source_urls.open_advertised(url, advertised_by=witness)
            network.assert_not_called()

    def test_private_dns_and_mixed_dns_are_rejected_before_connecting(self):
        for addresses in ([('127.0.0.1', 443)], [('8.8.8.8', 443), ('10.0.0.1', 443)]):
            rows = [(2, 1, 6, '', address) for address in addresses]
            with patch.object(source_urls.socket, 'getaddrinfo', return_value=rows), \
                 patch.object(source_urls._OPENER, 'open') as network, self.assertRaises(ValueError):
                source_urls.open_advertised(PAYLOAD, advertised_by=ROOT)
            network.assert_not_called()

    def test_redirect_authority_survives_multiple_cdns_and_strips_credentials(self):
        request = urllib.request.Request(ROOT, headers={'Authorization': 'fixture', 'Cookie': 'fixture',
                                                       'Proxy-Authorization': 'fixture'})
        request._sonos_advertised_source = True
        guard = source_urls.SonosRedirect()
        with patch.object(source_urls.socket, 'getaddrinfo', return_value=PUBLIC_DNS):
            child = guard.redirect_request(request, None, 302, 'Found', {}, PAGE)
            final = guard.redirect_request(child, None, 302, 'Found', {}, PAYLOAD)
            self.assertTrue(final._sonos_advertised_source)
            self.assertIsNone(child.get_header('Authorization'))
            self.assertIsNone(child.get_header('Cookie'))
            self.assertFalse(any(name.lower() == 'proxy-authorization' for name, _ in child.header_items()))
            with self.assertRaises(ValueError):
                guard.redirect_request(final, None, 302, 'Found', {}, 'https://10.0.0.1/file.upd')

    def test_https_uses_validated_socket_address_and_original_tls_hostname(self):
        from unittest.mock import Mock
        sock, context = Mock(), Mock()
        with patch.object(source_urls.socket, 'getaddrinfo', side_effect=[PUBLIC_DNS,
                [(2, 1, 6, '', ('127.0.0.1', 443))]]) as resolve, \
             patch.object(source_urls.socket, 'socket', return_value=sock):
            connection = source_urls._PublicHTTPSConnection('distribution.example', context=context)
            connection.connect()
        self.assertEqual(resolve.call_count, 1)
        sock.connect.assert_called_once_with(('8.8.8.8', 443))
        context.wrap_socket.assert_called_once_with(sock, server_hostname='distribution.example')

    def test_browser_metadata_binds_dns_and_disables_proxy_resolution(self):
        from curl_cffi import CurlOpt
        with patch.object(source_urls.socket, 'getaddrinfo', return_value=PUBLIC_DNS):
            options = source_urls.curl_source_options(PAGE, ROOT)
        self.assertEqual(options[CurlOpt.RESOLVE], ['distribution.example:443:8.8.8.8'])
        self.assertEqual(options[CurlOpt.PROXY], '')

    def test_advertised_mode_does_not_survive_reuse_of_a_request_in_strict_mode(self):
        request = urllib.request.Request(ROOT)
        captured = []
        def open_request(req, **kwargs):
            captured.append(req)
            return response(b'fixture', ROOT)
        with patch.object(source_urls._OPENER, 'open', side_effect=open_request):
            source_urls.open_advertised(request).close()
            source_urls.open_sonos(request).close()
        self.assertIsNot(captured[0], request)
        self.assertFalse(getattr(request, '_sonos_advertised_source', False))
        self.assertTrue(captured[0]._sonos_advertised_source)
        self.assertFalse(captured[1]._sonos_advertised_source)

    def test_manifest_page_chain_survives_frontier_restart_and_preserves_origin(self):
        xml = ('<update_manifest version="1.1"><image is_store="1">'+PAGE+
               '</image></update_manifest>').encode()
        pages = {ROOT: xml, PAGE: ('<a href="'+PAYLOAD+'">Download</a>').encode()}
        requests = []
        def fetch(url):
            requests.append(url)
            return pages[url]
        def probe(candidate):
            return {**asdict(candidate), 'available': True, 'http_status': 200}
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frontier, 'protocol_roots', return_value=[ROOT]), patch.object(frontier.time, 'sleep'):
            first = frontier.discover({}, Path(temporary), metadata_fetch=lambda: (b'', {}),
                fetch=fetch, probe_fn=probe, max_manifests=1, inference_limit=1)
            self.assertEqual(first['pending_manifest_urls'], [PAGE])
            resumed = frontier.discover(json.loads(json.dumps(first)), Path(temporary),
                metadata_fetch=lambda: (b'', {}), fetch=fetch, probe_fn=probe, max_manifests=1, inference_limit=1)
            row = next(row for row in resumed['candidates'] if row['url'] == PAYLOAD)
            self.assertEqual(row['advertised_by'], ROOT)
            body = section(99, 0, b'synthetic external firmware')
            with patch.object(source_urls.socket, 'getaddrinfo', return_value=PUBLIC_DNS), \
                 patch.object(source_urls._OPENER, 'open', return_value=response(body, PAYLOAD)):
                capture = discovery.download(row, Path(temporary)/'worker')
            self.assertTrue(capture['downloaded'], capture.get('download_error'))
            firmware_archive.register_capture(Path(temporary), captured(row, body))
            saved = firmware_archive.catalog_at(Path(temporary)/'data/catalog.json')['packages'][0]
            self.assertEqual(saved['advertised_sources'][PAYLOAD], ROOT)
            third = frontier.discover(json.loads(json.dumps(resumed)), Path(temporary),
                metadata_fetch=lambda: (b'', {}), fetch=fetch, probe_fn=probe, max_manifests=1, inference_limit=1)
        self.assertIn(PAYLOAD, third['probes'])
        self.assertNotIn(PAYLOAD, requests)  # Discovery never GETs the firmware.

    def test_unattested_external_seed_cannot_enter_the_crawl(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': PAGE}], [], Path(temporary),
                fetch=lambda url: self.fail('unattested external seed fetched'), delay=0)
        self.assertEqual(graph['documents'], [])
        self.assertEqual(graph['failures'][0]['kind'], 'unsupported')

    def test_external_manifest_keeps_the_original_first_party_authority(self):
        manifest = PAGE.replace('start.html', 'update.upm')
        root = ('<update_manifest version="1.1"><link>'+manifest+'</link></update_manifest>').encode()
        child = ('<update_manifest version="1.1"><image model="17" version="1.0-1">'+
                 PAYLOAD+'</image></update_manifest>').encode()
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': ROOT, 'body': root}], [], Path(temporary),
                fetch=lambda url: child, delay=0)
        self.assertEqual(graph['candidates'][0].advertised_by, ROOT)
        self.assertEqual(next(row for row in graph['manifests'] if row['url'] == manifest)['advertised_by'], ROOT)

    def test_challenged_external_document_is_retained_for_a_later_worker(self):
        xml = ('<update_manifest version="1.1"><image is_store="1">'+PAGE+
               '</image></update_manifest>').encode()
        def challenged(url):
            raise urllib.error.HTTPError(url, 429, 'retry later', {}, None)
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frontier, 'protocol_roots', return_value=[ROOT]), patch.object(frontier.time, 'sleep'):
            with patch.object(frontier, 'crawl_manifests', wraps=crawl_manifests):
                first = frontier.discover({}, Path(temporary), metadata_fetch=lambda: (b'', {}),
                    fetch=lambda url: xml if url == ROOT else challenged(url), max_manifests=2, inference_limit=1)
            self.assertIn(PAGE, first['pending_manifest_urls'])
            self.assertEqual(first['advertised_sources'][PAGE], ROOT)
            next_worker = frontier.discover(json.loads(json.dumps(first)), Path(temporary),
                metadata_fetch=lambda: (b'', {}), max_manifests=1, inference_limit=1,
                fetch=lambda url: ('<a href="'+PAYLOAD+'">Download</a>').encode(),
                probe_fn=lambda c: {**asdict(c), 'available': True, 'http_status': 200})
        self.assertEqual(next_worker['probes'][PAYLOAD]['advertised_by'], ROOT)

    def test_ups_external_base_enters_expanding_model_exploration(self):
        uri = b'https://metadata-cdn.example/firmware/^1.0-1\0'
        ups = struct.pack('<IIII', 0x35167F49, 7, len(uri)+16, 0)+uri
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'missing manifest', {}, None)
        seen = []
        with tempfile.TemporaryDirectory() as temporary, \
             patch.object(frontier, 'protocol_roots', return_value=[]), patch.object(frontier.time, 'sleep'):
            state = frontier.discover({}, Path(temporary), metadata_fetch=lambda: (ups, {}),
                fetch=missing, model_max=0, inference_limit=1,
                probe_fn=lambda c: seen.append(c) or {**asdict(c), 'available': True, 'http_status': 200})
        self.assertEqual(seen[0].url, 'https://metadata-cdn.example/firmware/1.0-1-1-0.upd')
        self.assertEqual(seen[0].advertised_by, UPDATE_METADATA_URL)
        self.assertEqual(state['candidates'][0]['advertised_by'], UPDATE_METADATA_URL)

    def test_historical_package_metadata_authorizes_an_external_installer(self):
        url = 'https://package-cdn.example/Sonos_1.exe'
        text = 'PackageVersion: 1\nInstallers:\n  - InstallerUrl: '+url+'\n'
        with tempfile.TemporaryDirectory() as temporary:
            history = ManifestHistory(Path(temporary)/'history.json', lambda endpoint: None)
            history.blob({'path': WITNESS['path'], 'sha': WITNESS['git_blob']},
                         {'content': base64.b64encode(text.encode()).decode()})
            item = next(iter(history.state['candidates'].values()))
            candidates = {}
            desktop.add_candidate(candidates, family='s2', platform='windows', version='1', url=url,
                sha256=None, source=item['sources'][0], advertised_by=item['advertised_by'])
            candidate = next(iter(candidates.values()))
            with patch.object(source_urls.socket, 'getaddrinfo', return_value=PUBLIC_DNS), \
                 patch.object(source_urls._OPENER, 'open', side_effect=lambda req, **kw: response(b'MZ fixture', url)):
                self.assertEqual(desktop.probe(candidate)['http_status'], 200)
                destination = Path(temporary)/'installer.exe'
                self.assertEqual(installers.acquire(candidate, destination), url)
                self.assertEqual(destination.read_bytes(), b'MZ fixture')
        self.assertIsNone(desktop.counterpart(candidate))

    def test_homebrew_cask_can_advertise_an_external_installer(self):
        url = 'https://package-cdn.example/Sonos_1.dmg'
        content = ('version "1"\nurl "'+url+'"\n').encode()
        with patch.object(desktop, 'HOMEBREW_PATHS', {'Casks/s/sonos.rb': 's2'}), \
             patch.object(desktop, 'gh_lines', return_value=['b'*40]), \
             patch.object(desktop.urllib.request, 'urlopen', return_value=io.BytesIO(content)):
            candidates = {}
            self.assertEqual(desktop.homebrew_candidates(candidates), [])
        row = next(iter(candidates.values()))
        self.assertTrue(source_urls.advertised_url(url, row['advertised_by']))
        self.assertEqual(row['advertised_by']['git_commit'], 'b'*40)

    def test_current_redirect_records_an_external_installer_without_downloading_it(self):
        from unittest.mock import Mock
        url = 'https://package-cdn.example/Sonos_1.exe'
        session = Mock()
        session.get.return_value = Mock(status_code=302, headers={'location': url})
        with patch.object(desktop, 'DIRECT_ENDPOINTS', {'s2-windows': ROOT}), \
             patch.object(desktop.tls_requests, 'Session', return_value=session):
            rows = desktop.check_direct_redirects(include_history=False)
        self.assertEqual(rows[0]['location'], url)
        self.assertEqual(session.get.call_count, 1)

    def test_cdn_response_with_an_unexpected_private_final_location_is_closed(self):
        result = response(b'fixture', 'https://127.0.0.1/private')
        with patch.object(source_urls.socket, 'getaddrinfo', return_value=PUBLIC_DNS), \
             patch.object(source_urls._OPENER, 'open', return_value=result), self.assertRaises(ValueError):
            source_urls.open_advertised(PAYLOAD, advertised_by=ROOT)
        self.assertTrue(result.closed)
