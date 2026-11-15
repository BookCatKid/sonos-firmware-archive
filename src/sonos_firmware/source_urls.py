"""One first-party URL boundary for discovered Sonos sources and redirects."""
import re
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def sonos_url(url):
    """Accept HTTPS Sonos domains without pinning a host or directory inventory."""
    if not isinstance(url, str) or any(ord(char) <= 32 or ord(char) == 127 for char in url):
        return False
    try:
        parsed = urlsplit(url)
        host, port = parsed.hostname or '', parsed.port
    except ValueError:
        return False
    return (parsed.scheme == 'https' and port in {None, 443} and
            parsed.username is None and parsed.password is None and
            (host == 'sonos.com' or host.endswith('.sonos.com')) and
            all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', part) for part in host.split('.')))


class SonosRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not sonos_url(newurl):
            raise urllib.error.URLError(f'redirect outside Sonos source domains: {newurl}')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OPENER = urllib.request.build_opener(SonosRedirect())


def open_sonos(request, *, timeout=60):
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    if not sonos_url(url):
        raise ValueError('source URL must use an HTTPS Sonos domain')
    response = _OPENER.open(request, timeout=timeout)
    try:
        if not sonos_url(response.geturl()):
            raise ValueError('response left Sonos source domains')
        return response
    except Exception:
        response.close()
        raise
