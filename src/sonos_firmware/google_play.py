"""Direct device-only Play requests behind the existing goopdl delivery interface.

Wire-field reference: Aurora GPlayApi 2f03e8babee8adf791d4f3a3b17c268a704be76c,
lib/src/main/proto/v2 and lib/src/main/kotlin/com/aurora/gplayapi/session.
No account, bearer token, dispenser, purchase or executable payload is involved
in discovery. Session identifiers stay in the worker's private directory.
"""
from contextlib import contextmanager, closing
from pathlib import Path
from types import SimpleNamespace
import base64
import gzip
import hashlib
import json
import os
import time
import uuid

from .android_store import StoreError
from .retry import retry_after_seconds

PROTOCOL_REVISION = '2f03e8babee8adf791d4f3a3b17c268a704be76c'
BASE = 'https://play.googleapis.com'


@contextmanager
def _http(method, url, **options):
    from curl_cffi import requests
    with requests.Session(impersonate='chrome_android', default_headers=False) as session:
        with closing(session.request(method, url, **options)) as response:
            yield response


class AnonymousPlay:
    def __init__(self, config, *, request=None):
        from goopdl import auth
        from goopdl.profiles import find_profile, get_priority_profiles
        if (config.credentials is not None or config.proxy or config.dispenser_url or
                config.config_dir is None or not config.config_dir.is_absolute()):
            raise StoreError('UnsupportedAnonymousSessionConfiguration')
        if request is None and os.environ.get('GITHUB_ACTIONS') != 'true':
            raise StoreError('AnonymousSessionAcquisitionRequiresCI')
        selected = find_profile(config.profile, config.arch) if config.profile else next(
            iter(get_priority_profiles(config.arch)), None)
        if selected is None:
            raise StoreError('UnknownGooglePlayProfile')
        self.config = config
        self.profile, self.locale = auth._device_context(selected, config.country)
        self.request = request or _http
        identity = json.dumps([self.profile, self.locale, PROTOCOL_REVISION], sort_keys=True)
        self.cache = Path(config.config_dir)/('anonymous-device-'+hashlib.sha256(identity.encode()).hexdigest()+'.json')

    def _read(self, method, path, *, headers, data=None, params=None):
        with self.request(method, BASE+path, headers=headers, data=data, params=params,
                          timeout=30, stream=True, allow_redirects=False) as response:
            if response.status_code != 200:
                raise StoreError('GooglePlayAnonymousRequestFailed', status_code=response.status_code,
                                 retry_after=retry_after_seconds(response))
            body = bytearray()
            for chunk in response.iter_content():
                if len(body)+len(chunk) > 2_000_000:
                    raise StoreError('GooglePlayAnonymousMetadataTooLarge')
                body.extend(chunk)
        return bytes(body)

    def _device(self):
        from goopdl import auth
        with auth._use_scope(auth._Scope(config_dir=self.config.config_dir, credentials=None,
                                         explicit_credentials=True)):
            with auth._pool_lock(self.config.arch, self.config.country, self.config.profile,
                                 raise_on_error=True) as acquired:
                if not acquired:
                    raise StoreError('GooglePlayAnonymousCacheBusy')
                if self.cache.exists():
                    cached = json.loads(self.cache.read_text())
                    device = cached.get('device', {})
                    if (0 <= time.time()-cached.get('created_at', 0) < 86400 and
                            isinstance(device.get('gsfId'), str) and device['gsfId'] and
                            isinstance(device.get('deviceConfigToken'), str) and device['deviceConfigToken']):
                        return device
                configuration = auth._device_configuration(self.profile)
                headers = {'app': 'com.google.android.gms', 'Host': 'android.clients.google.com',
                           'User-Agent': auth._finsky_user_agent(self.profile),
                           'Content-Type': 'application/x-protobuffer'}
                response = self._read('POST', '/checkin', headers=headers,
                    data=auth._checkin_request(self.profile, configuration, self.locale))
                identifier = auth._field(response, 7)
                if not isinstance(identifier, int) or not 0 < identifier < 2**64:
                    raise StoreError('InvalidGooglePlayAnonymousCheckin')
                device = {'gsfId': format(identifier, 'x'), 'deviceConfigToken': '',
                          'deviceCheckInConsistencyToken': auth._response_string(response, 12)}
                headers = auth.build_headers(auth._session_bundle(device, self.profile, ''), self.config.country)
                headers.pop('Authorization', None)
                headers.update({'Host': 'android.clients.google.com', 'Content-Type': 'application/x-protobuf'})
                uploaded = self._read('POST', '/fdfe/uploadDeviceConfig', headers=headers,
                                      data=auth._proto_bytes(1, configuration))
                device['deviceConfigToken'] = auth._response_string(uploaded, 1, 28, 1)
                if not device['deviceConfigToken']:
                    raise StoreError('InvalidGooglePlayAnonymousConfiguration')
                auth._atomic_pool_json(self.cache, {'created_at': time.time(), 'device': device})
                return device

    def _headers(self, *, fields=False):
        from goopdl.auth import _proto_bytes as b, _proto_varint as v
        device = self._device()
        profile = self.profile
        millis = time.time_ns()//1_000_000
        timestamp = v(1, millis//1000)+v(2, (millis % 1000)*1_000_000)
        def compressed(value):
            return base64.urlsafe_b64encode(gzip.compress(value, mtime=0)).decode()
        timestamps = b(6, b(1, b(1, timestamp))+b(2, timestamp))
        country = self.locale.split('_')[-1]
        entry = b(1, country)+b(2, timestamp)
        locality = v(2, 1)+v(3, 2)+b(4, '')+b(8, entry)+b(9, entry)+v(11, 0)
        android = (v(1, int(profile.get('Build.VERSION.SDK_INT') or 34))+
                   b(2, profile.get('Build.ID', ''))+b(3, profile.get('Build.VERSION.RELEASE', ''))+
                   v(4, int(profile.get('Vending.versionCode') or 82201710)))
        agent = b''.join(b(index, profile.get(name, '')) for index, name in (
            (1, 'Build.DEVICE'), (2, 'Build.HARDWARE'), (3, 'Build.MODEL'), (5, 'Build.PRODUCT')))
        agent += (b(4, 'Finsky/'+profile.get('Vending.versionString', ''))+
                  v(6, int(device['gsfId'], 16))+b(7, profile.get('Build.FINGERPRINT', '')))
        header = (b(1, b(1, compressed(timestamps)))+b(11, b(1, compressed(locality)))+
                  b(12, v(1, 5))+b(14, '')+b(20, b(1, android)+b(2, b'')+v(3, 1))+
                  b(21, agent)+b(27, b(1, str(uuid.uuid4()))+v(2, 2)))
        language = self.locale.replace('_', '-')
        result = {'User-Agent': f"Dalvik/2.1.0 (Linux; U; Android {profile.get('Build.VERSION.SDK_INT', '')}; "
                  f"{profile.get('Build.BRAND', '')} {profile.get('Build.MODEL', '')} Build/{profile.get('Build.ID', '')})",
                  'Accept-Language': language, 'X-DFE-UserLanguages': language,
                  'X-PS-RH': compressed(header), 'Content-Type': 'application/x-protobuf'}
        if fields:
            mask = bytes([254])+bytes([255])*15+bytes([1])
            result['X-DFE-Item-Field-Mask'] = base64.b64encode(b(3, mask)+b(4, mask)).decode().rstrip('=')
        return result

    def details(self, package):
        from goopdl.auth import _proto_bytes as b
        from goopdl.api import _all_bytes, _first_int, _first_string, _navigate, validate_package
        validate_package(package)
        raw = self._read('POST', '/fdfe/getItems', headers=self._headers(fields=True),
                         data=b(2, b(1, b(1, b(1, package)))))
        for item in _all_bytes(_navigate(raw), 11):
            metadata = _navigate(item, 2)
            name = _first_string(_navigate(_all_bytes(metadata, 1)[0]), 1) if _all_bytes(metadata, 1) else ''
            if name != package:
                continue
            generic = _all_bytes(metadata, 3)
            version = _navigate(generic[0], 2) if generic else []
            code = _first_int(version, 1) or 0
            if code > 0:
                return SimpleNamespace(package=name, version_code=code, version_string=_first_string(version, 2),
                                       offer_type=_first_int(version, 3) or 1)
        raise StoreError('GooglePlayAnonymousListingUnavailable')

    def delivery(self, package, version_code):
        from goopdl.api import _first_int, _navigate, _parse_delivery, validate_package
        validate_package(package)
        if not isinstance(version_code, int) or isinstance(version_code, bool) or version_code < 1:
            raise StoreError('InvalidGooglePlayVersionCode')
        raw = self._read('GET', '/fdfe/delivery', headers=self._headers(),
                         params={'doc': package, 'vc': str(version_code), 'ot': '1'})
        if _first_int(_navigate(raw, 1, 21), 1) != 1:
            raise StoreError('GooglePlayAnonymousDeliveryUnavailable')
        return _parse_delivery(raw)
