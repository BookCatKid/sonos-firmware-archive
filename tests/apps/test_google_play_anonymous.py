"""Device-only Play wire contracts; all replies and identifiers are fixtures."""
from contextlib import closing
import base64
import gzip
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from sonos_firmware.android_store import StoreError
from sonos_firmware.google_play import AnonymousPlay


@unittest.skipUnless(importlib.util.find_spec('goopdl'), 'install the google-play extra')
class AnonymousPlayTests(unittest.TestCase):
    def setUp(self):
        from goopdl.client import Config
        from goopdl.profiles import get_priority_profiles
        from goopdl.auth import _proto_bytes as b, _proto_varint as v
        self.b, self.v = b, v
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config(config_dir=Path(self.temp.name)/'private', arch='arm64',
                             profile=get_priority_profiles('arm64')[0][0])
        self.package = 'com.sonos.acr2'
        self.identifier = 0xf123456789abcdef
        self.calls, self.responses = [], []
        self.checkin = bytes([57])+self.identifier.to_bytes(8, 'little')+b(12, 'synthetic-consistency')
        self.upload = b(1, b(28, b(1, 'synthetic-config')))
        self.listing = b(11, b(2, b(1, b(1, self.package))+b(3, b(2, v(1, 123)+b(2, 'fixture')+v(3, 1)))))
        self.delivery = b(1, b(21, v(1, 1)+b(2,
            v(1, 9)+b(2, base64.b64encode(bytes(20)))+b(3, 'https://fixture.gvt1.com/base')+
            b(19, base64.b64encode(bytes(32)))+v(29, 123))))

    def request(self, method, url, **options):
        self.calls.append((method, url, options))
        body = {'/checkin': self.checkin, '/fdfe/uploadDeviceConfig': self.upload,
                '/fdfe/getItems': self.listing, '/fdfe/delivery': self.delivery}[url.removeprefix('https://play.googleapis.com')]
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = [body]
        self.responses.append(response)
        return closing(response)

    def test_direct_checkin_listing_and_delivery_need_no_dispenser_or_bearer(self):
        from goopdl.api import _first_int, _navigate
        client = AnonymousPlay(self.config, request=self.request)
        listing = client.details(self.package)
        delivery = client.delivery(self.package, listing.version_code)
        self.assertEqual((listing.package, listing.version_code, listing.version_string), (self.package, 123, 'fixture'))
        self.assertEqual(delivery.version_code, 123)
        self.assertEqual(delivery.download_url, 'https://fixture.gvt1.com/base')
        self.assertEqual(len(self.calls), 4)
        for _, url, options in self.calls:
            self.assertTrue(url.startswith('https://play.googleapis.com/'))
            self.assertNotIn('Authorization', options['headers'])
            self.assertFalse(options['allow_redirects'])
        headers = self.calls[2][2]['headers']
        header = gzip.decompress(base64.urlsafe_b64decode(headers['X-PS-RH']))
        self.assertEqual(_first_int(_navigate(header, 21), 6), self.identifier)
        self.assertIn('X-DFE-Item-Field-Mask', headers)
        self.assertNotIn('X-DFE-Item-Field-Mask', self.calls[3][2]['headers'])
        self.assertEqual(self.calls[3][2]['params'], {'doc': self.package, 'vc': '123', 'ot': '1'})
        for response in self.responses:
            response.close.assert_called_once()
        self.assertEqual(self.config.config_dir.stat().st_mode & 0o777, 0o700)
        self.assertEqual(client.cache.stat().st_mode & 0o777, 0o600)
        AnonymousPlay(self.config, request=self.request).details(self.package)
        self.assertEqual(len(self.calls), 5)

    def test_rate_limit_retains_timing_and_closes_response_without_saving_session(self):
        response = Mock(status_code=429, headers={'Retry-After': '7200'})
        request = Mock(return_value=closing(response))
        client = AnonymousPlay(self.config, request=request)
        with self.assertRaises(StoreError) as caught:
            client.details(self.package)
        self.assertEqual(caught.exception.status_code, 429)
        self.assertEqual(caught.exception.retry_after, 7200)
        request.assert_called_once()
        response.close.assert_called_once()
        response.iter_content.assert_not_called()
        self.assertFalse(client.cache.exists())


    def test_bounded_metadata_read_closes_response_and_does_not_save_invalid_device(self):
        response = Mock(status_code=200, headers={})
        response.iter_content.return_value = [bytes(2_000_001)]
        client = AnonymousPlay(self.config, request=lambda *args, **kwargs: closing(response))
        with self.assertRaisesRegex(StoreError, 'MetadataTooLarge'):
            client.details(self.package)
        response.close.assert_called_once()
        self.assertFalse(client.cache.exists())


    def test_existing_adapter_uses_native_delivery_with_integrity_and_provenance(self):
        from sonos_firmware import android_store
        from goopdl.api import PlayAPIError
        from goopdl.auth import DispenserUnavailableError
        error = PlayAPIError('private upstream detail', status_code=403)
        error.__cause__ = DispenserUnavailableError(403)
        sdk = Mock(config=self.config)
        sdk.delivery.side_effect = error
        client = AnonymousPlay(self.config, request=self.request)
        def download(specs):
            self.assertEqual(len(specs), 1)
            self.assertTrue(specs[0].integrity_required)
            self.assertEqual(specs[0].expected_size, 9)
            self.assertEqual(specs[0].sha256, base64.b64encode(bytes(32)).decode())
            specs[0].dest.write_bytes(b'synthetic')
        provenance = {}
        with patch.object(android_store, 'play_client', return_value=sdk), \
             patch('sonos_firmware.google_play.AnonymousPlay', return_value=client), \
             patch('goopdl.download.download_batch', side_effect=download):
            output, code = android_store.acquire_play(self.package, Path(self.temp.name)/'delivery',
                self.config.config_dir, version_code='123', provenance=provenance)
        self.assertEqual(code, '123')
        self.assertEqual((output/'base.apk').read_bytes(), b'synthetic')
        self.assertEqual(provenance['delivery']['backend'], 'google-play-device-anonymous')
