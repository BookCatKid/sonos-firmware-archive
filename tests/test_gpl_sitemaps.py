"""No-network public Sonos sitemap discovery regression tests."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from sonos_firmware.gpl_archive import HttpResult, load_json

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location('discover_gpl_sitemaps_test', ROOT / 'scripts/discover_gpl_sitemaps.py')
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class SitemapTests(unittest.TestCase):
    def test_sitemap_scopes_and_schema(self):
        self.assertTrue(MODULE.allowed_sitemap('https://www.sonos.com/en-us/sitemap.xml'))
        self.assertTrue(MODULE.allowed_sitemap('https://support.sonos.com/sitemap-index.xml'))
        for url in ('http://www.sonos.com/sitemap.xml', 'https://evil.example/sitemap.xml',
                    'https://www.sonos.com/sitemap.xml?redirect=1', 'https://www.sonos.com/shop'):
            self.assertFalse(MODULE.allowed_sitemap(url))
        body = b'<sitemapindex xmlns="http://www.sitemaps.org/schemas/sitemap/0.9"><sitemap><loc>https://www.sonos.com/en/sitemap.xml</loc></sitemap></sitemapindex>'
        kind, links = MODULE.parse_xml_sitemap(body)
        self.assertEqual(kind, 'sitemapindex')
        self.assertEqual(links, ['https://www.sonos.com/en/sitemap.xml'])
        with self.assertRaises(ValueError):
            MODULE.parse_xml_sitemap(b'<html><a>gpl</a></html>')

    def test_version_leads_from_official_sitemap_only(self):
        links = ['https://www.sonos.com/documents/gpl/19.12/gpl.html',
                 'https://www.sonos.com/documents/gpl/19.12/SonosOpenSourceAttributions.pdf',
                 'https://support.sonos.com/en/article/gpl-downloads',
                 'https://evil.example/documents/gpl/999.0/gpl.html']
        versions, related = MODULE.extract_leads(links)
        self.assertEqual(versions, {'19.12': sorted(links[:2])})
        self.assertIn(links[2], related)
        self.assertNotIn(links[3], related)

    def test_resumable_checkpoint_does_not_trust_unverified_indexes(self):
        from argparse import Namespace
        root = 'https://www.sonos.com/sitemap.xml'
        child = 'https://www.sonos.com/en/sitemap.xml'
        responses = {
            root: HttpResult(200, f'<sitemapindex><sitemap><loc>{child}</loc></sitemap></sitemapindex>'.encode(), 'application/xml', root),
            child: HttpResult(200, b'<urlset><url><loc>https://www.sonos.com/documents/gpl/22.13/gpl.html</loc></url></urlset>', 'application/xml', child),
        }
        with tempfile.TemporaryDirectory() as tmp:
            args = Namespace(output=Path(tmp) / 'site.json', limit=1, seed=[root], delay=2.5, recheck=False)
            with patch.object(MODULE, 'get_page', side_effect=lambda url, **_: responses[url]), patch.object(MODULE, 'pause'):
                self.assertEqual(MODULE.scan(args), 0)
                self.assertEqual(len(load_json(args.output, {})['sitemaps']), 1)
                self.assertEqual(MODULE.scan(args), 0)
            state = json.loads(args.output.read_text())
            self.assertEqual(state['version_evidence']['22.13'], ['https://www.sonos.com/documents/gpl/22.13/gpl.html'])
            self.assertEqual(state['sitemaps'][child]['state'], 'verified-xml')
            self.assertNotIn('verified_index', state)


class SitemapPrioritizationTests(unittest.TestCase):
    def test_discovery_prioritizes_live_site_evidence_without_claiming_verification(self):
        from argparse import Namespace
        spec = importlib.util.spec_from_file_location('archive_gpl_site_priority', ROOT / 'scripts/archive_gpl_direct.py')
        archive = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(archive)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'site-evidence.json').write_text(json.dumps({
                'schema_version': 1,
                'version_evidence': {'22.13': ['https://www.sonos.com/documents/gpl/22.13/gpl.html']},
                'sitemaps': {}, 'related_pages': [],
            }))
            args = Namespace(output=root, recheck=False, major_max=25, minor_max=20,
                             limit=1, delay=2.5, binary=[])
            requested = []
            def fake_get_page(url):
                requested.append(url)
                return HttpResult(403, b'<Error><Code>AccessDenied</Code></Error>', 'application/xml', url)
            with patch.object(archive, 'seed_versions', return_value=set()), \
                 patch.object(archive, 'offline_candidates', return_value={}), \
                 patch.object(archive, 'get_page', side_effect=fake_get_page), \
                 patch.object(archive, 'pause'):
                self.assertEqual(archive.discover(args), 0)
            self.assertEqual(requested, ['https://www.sonos.com/documents/gpl/22.13/gpl.html'])
            self.assertEqual(archive.live_catalog(root)['releases'], {})
            discovery = json.loads((root / 'discovery.json').read_text())['candidates']['22.13']
            self.assertEqual(discovery['priority'], 0)
            self.assertEqual(discovery['state'], 'access-denied')
            self.assertEqual(discovery['evidence'], ['https://www.sonos.com/documents/gpl/22.13/gpl.html'])
