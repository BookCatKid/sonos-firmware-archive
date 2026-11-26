"""Advertised CDNs survive discovery, restarts and acquisition without URL lists."""
import io
import json
import tempfile
import unittest
import urllib.error
import urllib.request
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from tests.discovery.test_frontier import scan_frontier
from sonos_firmware import discovery, firmware_archive, frontier, source_urls
from sonos_firmware.crawl import crawl_manifests
from tests.firmware.test_firmware_archive import captured
from tests.firmware.test_upd import section

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
            with patch('sonos_firmware.crawl.winding_down', side_effect=lambda: bool(requests)):
                first = scan_frontier({}, Path(temporary), metadata_fetch=lambda: (b'', {}),
                    fetch=fetch, probe_fn=probe, work_units=1)
            self.assertEqual(first['pending_manifest_urls'], [PAGE])
            resumed = scan_frontier(json.loads(json.dumps(first)), Path(temporary),
                metadata_fetch=lambda: (b'', {}), fetch=fetch, probe_fn=probe, work_units=1, )
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
            third = scan_frontier(json.loads(json.dumps(resumed)), Path(temporary),
                metadata_fetch=lambda: (b'', {}), fetch=fetch, probe_fn=probe, work_units=8)
        self.assertIn(PAYLOAD, third['probes'])
        self.assertNotIn(PAYLOAD, requests)  # Discovery never GETs the firmware.

    def test_unattested_external_seed_cannot_enter_the_crawl(self):
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': PAGE}], [], Path(temporary),
                fetch=lambda url: self.fail('unattested external seed fetched'), delay=0)
        self.assertEqual(graph['documents'], [])
        self.assertEqual(graph['failures'][0]['kind'], 'unsupported')


    def test_cdn_response_with_an_unexpected_private_final_location_is_closed(self):
        result = response(b'fixture', 'https://127.0.0.1/private')
        with patch.object(source_urls.socket, 'getaddrinfo', return_value=PUBLIC_DNS), \
             patch.object(source_urls._OPENER, 'open', return_value=result), self.assertRaises(ValueError):
            source_urls.open_advertised(PAYLOAD, advertised_by=ROOT)
        self.assertTrue(result.closed)
