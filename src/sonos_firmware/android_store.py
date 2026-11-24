"""Anonymous Google Play acquisition, isolated from verification/publication.

Only this module knows goopdl's pinned delivery-to-download interface. Signed
CDN URLs, cookies and dispenser tokens never cross this interface.
"""
from pathlib import Path
from itertools import zip_longest
import re
import subprocess
import json
import math
import os
import signal
from contextlib import contextmanager, closing

GOOPDL_REVISION = 'b66bf60d686fd93724b7c9f1963fe8bf4070b89d'


def store_apps(root):
    """Product identities and signer trust anchors, never a version URL list."""
    apps = json.loads((Path(root) / 'config/android-apps.json').read_text())
    for package, info in apps.items():
        if (not re.fullmatch(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+', package) or
                not re.fullmatch(r'[a-z0-9-]+', info['family']) or
                not re.fullmatch(r'[0-9a-f]{64}', info['signer_sha256'])):
            raise ValueError('invalid Android app identity or signer')
    return apps


class StoreError(RuntimeError):
    def __init__(self, kind, *, status_code=None, retry_after=None, observed_version_code=None):
        super().__init__(kind)
        self.status_code = status_code
        self.observed_version_code = (str(observed_version_code) if
            str(observed_version_code).isdecimal() and int(observed_version_code) > 0 else None)
        try:
            delay = float(retry_after)
            self.retry_after = delay if math.isfinite(delay) and delay >= 0 else None
        except (TypeError, ValueError):
            self.retry_after = None


def safe_error(error):
    """Keep retry signals, never protocol messages containing signed URLs."""
    if isinstance(error, StoreError):
        return error
    kind = type(error).__name__
    cause = error.__cause__
    if cause is not None and type(cause).__module__ == 'goopdl.auth':
        kind = {
            'DispenserUnavailableError': 'GooglePlayTokenDispenserUnavailable',
            'DispenserRateLimitedError': 'GooglePlayTokenDispenserRateLimited',
        }.get(type(cause).__name__, kind)
    status = getattr(error, 'status_code', None)
    # goopdl's CDN downloader detaches httpx errors and may retain the HTTP
    # status only in its safe error label. Preserve 429 without retaining URLs.
    if status is None and re.search(r'HTTP\s*429\b', str(error), re.IGNORECASE):
        status = 429
    return StoreError(kind, status_code=status,
                      retry_after=getattr(error, 'retry_after', None))


def play_profiles():
    from goopdl.profiles import get_priority_profiles
    # All compatible profiles, not goopdl's small default probe subset.
    groups = [[{'arch': arch, 'profile': name} for name, _ in get_priority_profiles(arch)]
              for arch in ('arm64', 'armv7', 'x86_64', 'x86', 'tv')]
    return [profile for group in zip_longest(*groups) for profile in group if profile is not None]


def run_apkeep(command, *, timeout=None):
    """Apkeep sometimes reports HTTP errors despite a successful exit status."""
    result = subprocess.run(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout)
    if re.search(r'HTTP\s*429\b|too many requests|rate[- ]limit(?:ed|ing)', result.stdout, re.IGNORECASE):
        match = re.search(r'retry.after\s*[:=]\s*(\d+)', result.stdout, re.IGNORECASE)
        raise StoreError('APKPureRateLimited', status_code=429,
                         retry_after=float(match.group(1)) if match else None)
    if result.returncode:
        raise StoreError('APKPureToolFailed')
    return result


def run_acquisition(command, *, cwd, timeout):
    """Bound a worker attempt, including any apkeep/Java child processes."""
    with subprocess.Popen(command, cwd=cwd, text=True, stdout=subprocess.PIPE,
                          stderr=subprocess.STDOUT, start_new_session=True) as process:
        try:
            output, _ = process.communicate(timeout=timeout)
            return subprocess.CompletedProcess(command, process.returncode, output)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            return subprocess.CompletedProcess(command, 124, 'StoreAcquisitionTimedOut')


def play_client(auth_dir, arch, profile=None):
    from goopdl.client import Client, Config
    directory = Path(auth_dir).resolve()
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    directory.chmod(0o700)
    # Explicit anonymous mode also ignores account credentials in the environment.
    return Client(Config(config_dir=directory, credentials=None, arch=arch, profile=profile))


def refresh_anonymous_session(config, *, request=None):
    """Retry a dispenser refusal with our TLS transport and the same device.

    Keep goopdl's scoped, private token cache; no global transport replacement,
    personal account, alternate dispenser or default-device GET is involved.
    The private cache helpers are covered by the pinned SDK contract tests.
    """
    from goopdl import auth
    from goopdl.profiles import find_profile, get_priority_profiles, patch_profile_country
    from .retry import retry_after_seconds
    if (config.credentials is not None or config.proxy or config.dispenser_url or
            config.config_dir is None or not config.config_dir.is_absolute()):
        raise StoreError('UnsupportedAnonymousSessionConfiguration')
    if request is None and os.environ.get('GITHUB_ACTIONS') != 'true':
        raise StoreError('AnonymousSessionAcquisitionRequiresCI')
    selected = find_profile(config.profile, config.arch) if config.profile else next(
        iter(get_priority_profiles(config.arch)), None)
    if selected is None:
        raise StoreError('UnknownGooglePlayProfile')
    name, profile = selected
    payload = patch_profile_country(profile, config.country) if config.country else profile
    if request is None:
        request = _request_aurora_session
    with request(auth.DEFAULT_DISPENSER_URL, json=payload, headers={
        # Aurora AuthProvider posts the active device properties with its app UA.
        # https://gitlab.com/AuroraOSS/AuroraStore/-/blob/master/app/src/main/java/com/aurora/store/data/providers/AuthProvider.kt
        'User-Agent': 'com.aurora.store-4.8.4-76', 'Content-Type': 'application/json'},
        timeout=30, allow_redirects=False, stream=True) as response:
        status = response.status_code
        if status != 200:
            raise StoreError('GooglePlayTokenDispenserRateLimited' if status == 429 else
                             'GooglePlayTokenDispenserUnavailable', status_code=status,
                             retry_after=retry_after_seconds(response))
        body = bytearray()
        for chunk in response.iter_content(chunk_size=8192):
            body.extend(chunk)
            if len(body) > 131072:
                raise StoreError('InvalidGooglePlayAnonymousSession')
        data = json.loads(body)
        if (not isinstance(data, dict) or
                any(not isinstance(data.get(field), str) or not data[field].strip()
                    for field in ('authToken', 'gsfId'))):
            raise StoreError('InvalidGooglePlayAnonymousSession')
    data['_device_profile'] = profile.get('UserReadableName', name)
    with auth._use_scope(auth._Scope(config_dir=config.config_dir, credentials=None,
                                     explicit_credentials=True)):
        with auth._pool_lock(config.arch, config.country, config.profile, raise_on_error=True) as acquired:
            if not acquired:
                raise StoreError('GooglePlayAnonymousCacheBusy')
            auth.save_auth(data, config.arch, config.country, config.profile)
            pool = auth._load_pool_unlocked(config.arch, config.country, config.profile)
            # The just-minted device/session stays in this profile's pool only.
            auth._save_pool([data, *pool], config.arch, config.country, config.profile)


@contextmanager
def _request_aurora_session(url, **options):
    from curl_cffi import requests
    with requests.Session(impersonate='chrome_android', default_headers=False) as session:
        with closing(session.post(url, **options)) as response:
            yield response


def _anonymous_call(client, method, *args):
    try:
        return getattr(client, method)(*args)
    except Exception as error:
        failure = safe_error(error)
        if str(failure) != 'GooglePlayTokenDispenserUnavailable' or failure.status_code != 403:
            raise
    refresh_anonymous_session(client.config)
    return getattr(client, method)(*args)


def play_version(package, auth_dir, arch, profile=None):
    try:
        details = _anonymous_call(play_client(auth_dir, arch, profile), 'details', package)
        if details.package != package or details.version_code <= 0:
            raise StoreError('InvalidGooglePlayListing')
        return {'version_code': str(details.version_code), 'version_name': details.version_string}
    except Exception as error:
        raise safe_error(error) from None


def acquire_play(package, output, auth_dir, arch='arm64', profile=None, version_code=None):
    from goopdl.api import _build_specs
    from goopdl.download import DownloadSpec, download_batch
    try:
        client = play_client(auth_dir, arch, profile)
        code = int(version_code) if version_code else _anonymous_call(client, 'details', package).version_code
        delivery = _anonymous_call(client, 'delivery', package, code)
        if delivery.version_code != code:
            raise StoreError(f'GooglePlayVersionMismatch: requested {code}, returned {delivery.version_code}',
                             observed_version_code=delivery.version_code)
        output = Path(output)
        output.mkdir(parents=True, exist_ok=True)
        # The pinned helper builds base, split and dex metadata specs with
        # upstream integrity checks and safe filenames.
        specs = _build_specs(package, code, output, delivery, False, True, dm=True)
        # The upstream helper gives every asset pack the same filename. Keep
        # all advertised extras, including multiple packs of the same type.
        for index, extra in enumerate(delivery.additional_files):
            name = f'extra-{index}-{extra.type_label}-{extra.version_code}{extra.extension}'
            specs.append(DownloadSpec(url=extra.url, dest=output/name, cookies=extra.cookies,
                label=name, gzipped=extra.gzipped, expected_size=max(extra.size, 0)))
        download_batch(specs)
        base = output / 'base.apk'
        specs[0].dest.rename(base)
        return output, str(code)
    except Exception as error:
        raise safe_error(error) from None
