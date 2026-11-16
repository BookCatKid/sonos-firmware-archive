"""Discover literal public links in HTML, scripts, stylesheets and manifests."""
import re
import urllib.parse
from html import unescape
from html.parser import HTMLParser

URL_RE = re.compile(r'''(?:(?:src|href)=["']([^"']+)|["'(]((?:https?://[^"')\\ ]+|/(?:_next/static|assets|fonts)/[^"')\\ ]+)))(?=["')\s]|$)''')
PAYLOAD_SUFFIXES = ('.upd', '.exe', '.dmg', '.apk', '.bin', '.img', '.pkg', '.zip',
                    '.tar', '.gz', '.bz2', '.xz', '.tgz', '.txz')


class _Links(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.base = None
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag == 'base':
            if self.base is None:
                self.base = attrs.get('href')
            return
        self.links.extend(attrs[key] for key in ('href', 'src') if attrs.get(key))
        if tag == 'meta' and (attrs.get('http-equiv') or '').lower() == 'refresh':
            match = re.search(r'(?i)\burl\s*=\s*(.+)', attrs.get('content') or '')
            if match:
                self.links.append(match[1].strip().strip('\"\''))


def _join(base, raw):
    try:
        return urllib.parse.urljoin(base, raw)
    except ValueError:
        # Keep malformed absolute leads visible to the caller's URL policy.
        return raw


def discover_urls(base_url: str, body: bytes) -> set[str]:
    text = body.decode("utf-8", errors="replace")
    parsed = _Links()
    try:
        parsed.feed(text)
    except AssertionError:
        # Older pages can contain unknown marked declarations. Keep partial
        # attributes and continue the literal scan over the complete document.
        pass
    base_url = _join(base_url, parsed.base) if parsed.base else base_url
    # A base declaration changes resolution; it is not itself a navigation.
    text = re.sub(r'<base\b[^>]*>', '', text, flags=re.I)
    # All literal scanners must see the same decoded query separators. Otherwise
    # a scanner that stops at a backslash can add an extra truncated URL.
    text = text.replace('\\u0026', '&').replace('\\/', '/')
    found = set()
    for raw in parsed.links:
        if raw and not raw.startswith(('data:', 'javascript:', 'mailto:', '#')):
            found.add(_join(base_url, raw))
    for match in URL_RE.finditer(text):
        raw = (match.group(1) or match.group(2) or "").replace("\\u0026", "&")
        if match.group(1):
            raw = unescape(raw)
        if not raw or raw.startswith(("data:", "javascript:", "mailto:", "#")):
            continue
        found.add(_join(base_url, raw))
    # Next.js manifests often contain escaped route chunks outside attributes.
    for raw in re.findall(r"/_next/static/[A-Za-z0-9_./%?=&+~@\[\]-]+", text):
        found.add(_join(base_url, raw))
    # Next.js build manifests omit the /_next/ prefix for route chunks.
    for raw in re.findall(r'''["'](static/(?:chunks|css|media)/[^"']+)["']''', text):
        if "${" not in raw:
            found.add(_join(base_url, f"/_next/{raw}"))
    # Web manifests use JSON src fields; stylesheets use url(...).
    for raw in re.findall(
        r'''(?:["'](?:src|href)["']\s*:\s*["']|url\(\s*["']?)([^"'()\s]+)''',
        text,
    ):
        if "${" not in raw and not raw.startswith("data:"):
            found.add(_join(base_url, raw))
    # Browser modules resolve relative imports against the importing script.
    # Bare package specifiers need an import map and are not fetchable paths.
    for pattern in (
        r'''\b(?:import|export)\s*(?:[^;"'`()]*?\bfrom\s*)?["'`]([^"'`]+)["'`]''',
        r'''\bimport\s*\(\s*["'`]([^"'`]+)["'`]''',
    ):
        for raw in re.findall(pattern, text):
            if raw.startswith(('./', '../', '/', 'https://', 'http://')):
                found.add(_join(base_url, raw))
    # Download pages also advertise literal locations inside inline scripts.
    suffixes = '|'.join(re.escape(ext.removeprefix('.')) for ext in ('.upm', *PAYLOAD_SUFFIXES))
    for raw in re.findall(rf'''["']((?:https?://|//|\./|\.\./|/)[^"'<>\s]+|[^"'<>\s]+\.(?:{suffixes})(?:\?[^"'<>\s]*)?)["']''', text, re.I):
        if '${' not in raw:
            found.add(_join(base_url, unescape(raw.replace('\\/', '/').replace('\\u0026', '&'))))
    found = {url.split('#', 1)[0] for url in found
             if "${" not in url and url.lower().startswith(('http://', 'https://'))}
    return found
