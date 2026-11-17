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
    status = getattr(error, 'status_code', None)
    # goopdl's CDN downloader detaches httpx errors and may retain the HTTP
    # status only in its safe error label. Preserve 429 without retaining URLs.
    if status is None and re.search(r'HTTP\s*429\b', str(error), re.IGNORECASE):
        status = 429
    return StoreError(type(error).__name__, status_code=status,
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


def play_version(package, auth_dir, arch, profile=None):
    try:
        details = play_client(auth_dir, arch, profile).details(package)
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
        code = int(version_code) if version_code else client.details(package).version_code
        delivery = client.delivery(package, code)
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
