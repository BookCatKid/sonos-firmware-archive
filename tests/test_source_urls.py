"""New first-party locations propagate without editing host or path lists."""
import io
import tempfile
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from sonos_firmware import archive_index, discovery, installers, source_urls
from sonos_firmware.crawl import crawl_manifests


class SourceURLTests(unittest.TestCase):
    def test_new_sonos_host_and_path_are_followed_from_a_manifest(self):
        root = 'https://update.sonos.com/firmware/current/update.upm'
        other = 'https://new-distribution.sonos.com/new-layout/update.upm'
        body = ('<update_manifest version="1.1"><link>'+other+'</link></update_manifest>').encode()
        child = b'<update_manifest version="1.1"><image model="17" version="1.0-1">1.0-1-1-17.upd</image></update_manifest>'
        requests = []
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': root, 'body': body}], [], Path(temporary),
                fetch=lambda url: requests.append(url) or child, delay=0)
        self.assertEqual(requests, [other])
        self.assertEqual([row.url for row in graph['candidates']], [other.replace('update.upm', '1.0-1-1-17.upd')])
        self.assertEqual(graph['failures'], [])

    def test_domain_index_discovers_unlisted_hosts_and_directories(self):
        firmware = 'https://unknown-history.sonos.com/old-layout/1.0-1-1-17.upd'
        installer = 'https://unknown-apps.sonos.com/new-layout/Sonos_100.2.EXE'
        requests = []
        def fetch(url):
            requests.append(url)
            return [['timestamp', 'original', 'digest'], ['20200101000000', firmware, 'fw'],
                    ['20200101000000', installer, 'app']]
        result = archive_index.discover_sources({}, fetch=fetch, pages=1)
        query = parse_qs(urlsplit(requests[0]).query)
        self.assertEqual((query['url'], query['matchType']), (['sonos.com/'], ['domain']))
        self.assertIn(firmware, result['artifacts'])
        apps = archive_index.discover_sources({}, fetch=fetch, pages=1, extensions=('exe',),
                                             allowed=installers.official_source)
        self.assertIn(installer, apps['artifacts'])

    def test_old_host_cursors_reset_when_scope_expands_without_losing_captures(self):
        url = 'https://update.sonos.com/firmware/old.upd'
        prior = {'schema_version': 2, 'cursors': {'update.sonos.com': 'old-scope'},
                 'artifacts': {url: {'timestamp': '20200101000000', 'digest': 'original'}}}
        requests = []
        result = archive_index.discover_sources(prior, fetch=lambda url: requests.append(url) or [], pages=1)
        self.assertNotIn('resumeKey', requests[0])
        self.assertEqual(result['cursors'], {'sonos.com': None})
        self.assertEqual(len(result['artifacts'][url]['captures']), 1)

    def test_url_boundary_rejects_lookalikes_credentials_and_nonstandard_ports(self):
        for url in ('https://sonos.com.evil.test/file.upd', 'https://evilsonos.com/file.upd',
                    'https://sonos.com@evil.test/file.upd', 'https://@update.sonos.com/file.upd',
                    'https://user:pass@update.sonos.com/file.upd', 'https://update.sonos.com:8443/file.upd',
                    'http://update.sonos.com/file.upd', 'file:///tmp/file.upd',
                    'https://127.0.0.1/file.upd', 'https://new\n.sonos.com/file.upd'):
            with self.subTest(url=url), patch.object(source_urls._OPENER, 'open') as network:
                self.assertFalse(source_urls.sonos_url(url))
                with self.assertRaises(ValueError):
                    source_urls.open_sonos(url)
                network.assert_not_called()

    def test_redirects_are_guarded_before_following_and_responses_are_checked(self):
        url = 'https://new-downloads.sonos.com/elsewhere/image.upd'
        request = urllib.request.Request('https://update.sonos.com/firmware/image.upd')
        guard = source_urls.SonosRedirect()
        self.assertEqual(guard.redirect_request(request, None, 302, 'Found', {}, url).full_url, url)
        with self.assertRaises(urllib.error.URLError):
            guard.redirect_request(request, None, 302, 'Found', {}, 'https://outside.test/image.upd')
        response = io.BytesIO(b'fixture')
        response.geturl = lambda: 'https://outside.test/image.upd'
        with patch.object(source_urls._OPENER, 'open', return_value=response):
            with self.assertRaises(ValueError):
                source_urls.open_sonos(request)
        self.assertTrue(response.closed)

    def test_download_and_installer_acquisition_use_the_shared_boundary(self):
        url = 'https://new-downloads.sonos.com/new-layout/image.upd'
        with tempfile.TemporaryDirectory() as temporary, patch.object(source_urls._OPENER, 'open') as network:
            result = discovery.download({'url': 'https://outside.test/image.upd', 'filename': 'image.upd'}, temporary)
            self.assertFalse(result['downloaded'])
            network.assert_not_called()
            candidate = {'url': 'https://outside.test/Sonos.exe', 'platform': 'windows'}
            with self.assertRaises(ValueError):
                installers.acquire(candidate, Path(temporary)/'installer.exe')
            network.assert_not_called()
            response = io.BytesIO(b'fixture')
            response.headers = {}
            response.geturl = lambda: url
            network.return_value = response
            result = discovery.download({'url': url, 'filename': 'image.upd'}, temporary)
            self.assertTrue(result['downloaded'])
            self.assertEqual(result['fetched_from'], url)
