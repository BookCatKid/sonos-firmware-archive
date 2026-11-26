"""Fetch exact public archive replays without silently choosing another capture."""
import re
import urllib.request
from datetime import timezone
from email.utils import parsedate_to_datetime
from urllib.parse import urlsplit, urlunsplit

from .discovery import USER_AGENT, official_firmware_url


class ReplayPaused(ValueError):
    """A replay access challenge should pause this host for the current run."""
    pause_replay = True
    pause_host = 'web.archive.org'


def capture_host(capture):
    provider = (capture or {}).get('provider', 'wayback')
    if provider not in {'wayback', 'commoncrawl'}:
        raise ValueError('unsupported archive capture provider')
    return 'data.commoncrawl.org' if provider == 'commoncrawl' else 'web.archive.org'


def canonical_source(url, allowed=official_firmware_url):
    parsed = urlsplit(url)
    if (parsed.scheme not in {'http', 'https'} or parsed.username or parsed.password or
            parsed.port not in {None, 80 if parsed.scheme == 'http' else 443}):
        raise ValueError('replay source must be an approved public URL')
    result = urlunsplit(('https', parsed.hostname, parsed.path, parsed.query, ''))
    if not allowed(result):
        raise ValueError('replay source uses an unsupported host or path')
    return result


def replay_url(original, capture, *, allowed=official_firmware_url):
    host = capture_host(capture)
    timestamp = capture.get('timestamp')
    if not isinstance(timestamp, str) or not re.fullmatch(r'\d{14}', timestamp):
        raise ValueError('replay requires an exact capture timestamp')
    source = capture.get('original_url') or original
    if canonical_source(source, allowed) != canonical_source(original, allowed):
        raise ValueError('capture belongs to another source URL')
    if host == 'data.commoncrawl.org':
        from .commoncrawl import capture_url
        return capture_url(capture)
    return f'https://web.archive.org/web/{timestamp}id_/{source}'


def validate_replay(url, original, timestamp, *, allowed=official_firmware_url):
    parsed = urlsplit(url)
    if (parsed.scheme != 'https' or parsed.hostname != 'web.archive.org' or
            parsed.port not in {None, 443} or parsed.username or parsed.password or parsed.fragment):
        raise ValueError('replay left the approved archive endpoint')
    match = re.fullmatch(r'/web/(\d{14})id_/(https?://.+)', parsed.path)
    if not match or match[1] != timestamp:
        raise ValueError('replay selected another capture or rewriting mode')
    source = match[2] + ('?' + parsed.query if parsed.query else '')
    if canonical_source(source, allowed) != canonical_source(original, allowed):
        raise ValueError('replay redirected to another source URL')


class _ReplayRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, original, timestamp, allowed=official_firmware_url):
        self.original, self.timestamp = original, timestamp
        self.allowed = allowed

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        validate_replay(newurl, self.original, self.timestamp, allowed=self.allowed)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def open_capture(original, capture, *, timeout=60, allowed=official_firmware_url):
    if capture_host(capture) == 'data.commoncrawl.org':
        from .commoncrawl import open_capture as open_commoncrawl
        return open_commoncrawl(original, capture, timeout=timeout, allowed=allowed)
    url = replay_url(original, capture, allowed=allowed)
    opener = urllib.request.build_opener(_ReplayRedirect(original, capture['timestamp'], allowed))
    response = opener.open(urllib.request.Request(url, headers={'User-Agent': USER_AGENT}), timeout=timeout)
    try:
        if getattr(response, 'status', 200) != 200:
            raise ValueError('archive replay did not return a complete HTTP 200 response')
        validate_replay(response.geturl(), original, capture['timestamp'], allowed=allowed)
        date = response.headers.get('Memento-Datetime')
        if date and parsedate_to_datetime(date).astimezone(timezone.utc).strftime('%Y%m%d%H%M%S') != capture['timestamp']:
            raise ValueError('archive response identifies another capture date')
        if 'html' in response.headers.get('Content-Type', '').lower():
            raise ReplayPaused('archive returned an HTML page instead of firmware or manifest bytes')
        return response
    except Exception:
        response.close()
        raise
