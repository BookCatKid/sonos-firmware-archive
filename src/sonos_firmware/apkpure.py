"""Discover APKPure's advertised versions and ABI variants before acquisition.

The public metadata protocol is also used by Obtainium's APKPure adapter.
Returned rows are leads, not proof of a preserved or downloadable APK.
"""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit
from xml.etree import ElementTree

from curl_cffi.requests import Session

from .android_store import StoreError
from .storage import atomic_json

API = 'https://tapi.pureapk.com/v3/get_app_his_version'
HEADERS = {'Ual-Access-Businessid': 'projecta',
           'Ual-Access-ProjectA': '{"device_info":{"os_ver":"29"}}'}
SDK_INDEX = 'https://dl.google.com/android/repository/repository2-1.xml'


def _http_status(response, kind='APKPureHTTPError'):
    if 200 <= response.status_code < 300:
        return
    value = response.headers.get('Retry-After')
    try:
        delay = float(value)
    except (ValueError, TypeError):
        try:
            timestamp = parsedate_to_datetime(value)
            delay = max(0, (timestamp - datetime.now(timezone.utc)).total_seconds())
        except (ValueError, TypeError, OverflowError):
            delay = None
    raise StoreError(kind, status_code=response.status_code, retry_after=delay)


def parse_versions(document, package, *, sdk=29):
    if (not isinstance(document, dict) or document.get('retcode') not in (0, '0') or
            not isinstance(document.get('version_list'), list)):
        raise StoreError('InvalidAPKPureVersionList')
    variants, errors, sdk_hints = {}, [], set()
    for rank, row in enumerate(document['version_list']):
        try:
            if not isinstance(row, dict) or row.get('package_name') != package:
                raise ValueError('PackageMismatch')
            code = str(row.get('version_code', ''))
            if not code.isdecimal() or int(code) <= 0:
                raise ValueError('InvalidVersionCode')
            name = row.get('version_name') or code
            asset = row.get('asset') or {}
            url = asset.get('url')
            parts = urlsplit(url)
            if (not isinstance(url, str) or parts.scheme not in ('http', 'https') or
                    not parts.hostname or parts.username or parts.password):
                raise ValueError('InvalidAssetURL')
            architectures = row.get('native_code') or []
            if not isinstance(architectures, list) or not all(isinstance(a, str) for a in architectures):
                raise ValueError('InvalidArchitectures')
            arch = ','.join(sorted(set(architectures))) or 'universal'
            variant = str(row.get('version_id') or 'asset') + '-' + hashlib.sha256(url.encode()).hexdigest()
            lead = {'package': package, 'version_code': code, 'version_name': str(name),
                    'version_selector': str(name), 'arch': arch, 'variant': variant,
                    'asset_url': url, 'asset_type': str(asset.get('type', '')).upper(),
                    'rank': rank, 'discovered_from': 'apkpure-api', 'sdk_context': sdk}
            for field in ('sdk_version', 'target_sdk_version'):
                value = str(row.get(field, ''))
                if value.isdecimal() and int(value) > 0:
                    sdk_hints.add(int(value))
                    lead[field] = value
            variants[(code, arch, variant, url)] = lead
        except (ValueError, TypeError, AttributeError) as error:
            errors.append({'row': rank, 'error': str(error) if isinstance(error, ValueError) else 'MalformedVariant'})
    return {'variants': list(variants.values()), 'errors': errors, 'sdk_hints': sorted(sdk_hints)}


def discover_versions(package, *, sdk=29, observations=None, timeout=120):
    url = API + '?' + urlencode({'package_name': package, 'hl': 'en'})
    try:
        with Session(impersonate='chrome') as session:
            headers = {**HEADERS, 'Ual-Access-ProjectA': json.dumps(
                {'device_info': {'os_ver': str(sdk)}}, separators=(',', ':'))}
            response = session.get(url, headers=headers, timeout=timeout)
            _http_status(response)
            document = response.json()
        cache_error = None
        if observations is not None:
            # Metadata only. Cache the full response outside Git so a parser
            # update can reinterpret unfamiliar fields without another request.
            try:
                atomic_json(Path(observations) / (f'{package}--sdk-{sdk}.json'), document)
            except OSError as error:
                cache_error = {'source': 'observation-cache', 'error': type(error).__name__}
        result = parse_versions(document, package, sdk=sdk)
        if cache_error:
            result['errors'].append(cache_error)
        return result
    except StoreError:
        raise
    except Exception as error:
        raise StoreError(type(error).__name__) from None


def parse_sdk_levels(xml):
    levels = set()
    for package in ElementTree.fromstring(xml):
        if not package.attrib.get('path', '').startswith('platforms;android-'):
            continue
        for node in package.iter():
            value = (node.text or '').strip()
            if node.tag.rsplit('}', 1)[-1] == 'api-level' and value.isdecimal() and int(value) > 0:
                levels.add(int(value))
    if not levels:
        raise StoreError('InvalidAndroidSDKIndex')
    return sorted(levels)


def discover_sdk_levels(*, observations=None, timeout=30):
    try:
        with Session(impersonate='chrome') as session:
            response = session.get(SDK_INDEX, timeout=timeout)
            _http_status(response, 'AndroidSDKIndexHTTPError')
            xml = response.text
        errors = []
        if observations is not None:
            try:
                atomic_json(Path(observations)/'android-sdk-index.json', {'xml': xml})
            except OSError as error:
                errors.append({'source': 'observation-cache', 'error': type(error).__name__})
        return {'levels': parse_sdk_levels(xml), 'errors': errors}
    except StoreError:
        raise
    except Exception as error:
        raise StoreError(type(error).__name__) from None


def acquire_variant(url, asset_type, output):
    extension = {'APK': '.apk', 'XAPK': '.xapk'}.get(asset_type)
    if extension is None:
        raise StoreError('UnsupportedAPKPureAssetType')
    parts = urlsplit(url)
    if parts.scheme not in ('https', 'http') or not parts.hostname or parts.username or parts.password:
        raise StoreError('InvalidAPKPureAssetURL')
    destination = Path(output) / ('delivery' + extension)
    try:
        with Session(impersonate='chrome') as session:
            response = session.get(url, stream=True, timeout=None)
            try:
                _http_status(response)
                with destination.open('wb') as outgoing:
                    for chunk in response.iter_content(chunk_size=1024*1024):
                        outgoing.write(chunk)
            finally:
                response.close()
        return destination
    except StoreError:
        raise
    except Exception as error:
        raise StoreError(type(error).__name__) from None
