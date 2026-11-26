"""Shared URL boundaries for Sonos roots and their advertised distribution sources."""
import re
import copy
import http.client
import ipaddress
import socket
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


def public_https_url(url):
    """Validate a public HTTPS location without making a metadata-time request."""
    if not isinstance(url, str) or any(ord(c) <= 32 or ord(c) == 127 for c in url):
        return False
    try:
        parsed = urlsplit(url)
        host = parsed.hostname or ''
        if (parsed.scheme != 'https' or parsed.port not in {None, 443} or
                parsed.username is not None or parsed.password is not None):
            return False
        try:
            address = ipaddress.ip_address(host)
            return address.is_global and not address.is_multicast
        except ValueError:
            return ('.' in host and not host.endswith(('.localhost', '.local', '.internal')) and
                    all(re.fullmatch(r'[a-z0-9](?:[a-z0-9-]*[a-z0-9])?', p) for p in host.split('.')))
    except ValueError:
        return False


def trusted_advertiser(witness):
    if isinstance(witness, str):
        return sonos_url(witness)
    if not isinstance(witness, dict):
        return False
    revision = witness.get('git_blob', witness.get('git_commit'))
    if not isinstance(revision, str) or not re.fullmatch(r'[0-9a-f]{40}', revision):
        return False
    repository, path = witness.get('repository'), witness.get('path', '')
    if not isinstance(path, str) or '..' in path.split('/'):
        return False
    return ((repository == 'microsoft/winget-pkgs' and
             (path.startswith(('manifests/s/Sonos/', 'manifests/Sonos/', 'manifests/Sonos.')))) or
            (repository == 'Homebrew/homebrew-cask' and
             re.fullmatch(r'Casks/(?:s/)?sonos[^/]*\.rb', path) is not None))


def advertised_url(url, advertised_by=None):
    return sonos_url(url) or (trusted_advertiser(advertised_by) and public_https_url(url))


def _public_destination(url):
    if not public_https_url(url):
        raise ValueError('advertised source must use public HTTPS')
    if not sonos_url(url):
        _public_addresses(urlsplit(url).hostname, 443)


def _public_addresses(host, port):
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(row[4][0]).is_global or
                            ipaddress.ip_address(row[4][0]).is_multicast for row in addresses):
        raise ValueError('advertised source resolves to a non-public address')
    return addresses


def _connect_public(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None):
    """Connect to a validated sockaddr, without a second hostname resolution."""
    rows = _public_addresses(*address)
    error = None
    for family, kind, protocol, _, destination in rows:
        connection = None
        try:
            connection = socket.socket(family, kind, protocol)
            if timeout is not socket._GLOBAL_DEFAULT_TIMEOUT:
                connection.settimeout(timeout)
            if source_address:
                connection.bind(source_address)
            connection.connect(destination)
            return connection
        except OSError as caught:
            error = caught
            if connection is not None:
                connection.close()
    raise error


class _PublicHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = _connect_public


class _PublicHTTPSHandler(urllib.request.HTTPSHandler):
    def https_open(self, request):
        return self.do_open(_PublicHTTPSConnection, request, context=self._context)


class SonosRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        advertised = getattr(req, '_sonos_advertised_source', False)
        if advertised:
            _public_destination(newurl)
        elif not sonos_url(newurl):
            raise urllib.error.URLError(f'redirect outside Sonos source domains: {newurl}')
        redirected = super().redirect_request(req, fp, code, msg, headers, newurl)
        if redirected is not None:
            redirected._sonos_advertised_source = advertised
            if urlsplit(req.full_url).netloc != urlsplit(newurl).netloc:
                for name, _ in redirected.header_items():
                    if name.lower() in {'authorization', 'cookie', 'proxy-authorization'}:
                        redirected.remove_header(name)
        return redirected


_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}), SonosRedirect(), _PublicHTTPSHandler())


def open_sonos(request, *, timeout=60):
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    if not sonos_url(url):
        raise ValueError('source URL must use an HTTPS Sonos domain')
    if isinstance(request, urllib.request.Request):
        request = copy.copy(request)
        request._sonos_advertised_source = False
    response = _OPENER.open(request, timeout=timeout)
    try:
        if not sonos_url(response.geturl()):
            raise ValueError('response left Sonos source domains')
        return response
    except Exception:
        response.close()
        raise


def open_advertised(request, *, advertised_by=None, timeout=60):
    """Follow an advertised source, retaining the first-party authority boundary."""
    if not isinstance(request, urllib.request.Request):
        request = urllib.request.Request(request)
    else:
        request = copy.copy(request)
    validate_advertised_source(request.full_url, advertised_by)
    request._sonos_advertised_source = True
    response = _OPENER.open(request, timeout=timeout)
    try:
        _public_destination(response.geturl())
        return response
    except Exception:
        response.close()
        raise


def validate_advertised_source(url, advertised_by=None):
    if not advertised_url(url, advertised_by):
        raise ValueError('external source has no trusted advertisement')
    _public_destination(url)
    return url


def curl_source_options(url, advertised_by=None):
    """Bind browser-compatible metadata requests to validated CDN addresses."""
    from curl_cffi import CurlOpt
    if not advertised_url(url, advertised_by):
        raise ValueError('external source has no trusted advertisement')
    options = {CurlOpt.PROXY: ''}  # A proxy must not resolve an unvalidated destination.
    if not sonos_url(url):
        parsed = urlsplit(url)
        addresses = _public_addresses(parsed.hostname, 443)
        hosts = sorted({('['+row[4][0]+']') if ':' in row[4][0] else row[4][0] for row in addresses})
        options[CurlOpt.RESOLVE] = [parsed.hostname+':443:'+','.join(hosts)]
    return options
