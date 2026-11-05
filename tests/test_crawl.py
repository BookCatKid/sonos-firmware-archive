import tempfile
import unittest
import urllib.error
from pathlib import Path
from sonos_firmware.discovery import parse_manifest
from sonos_firmware.crawl import crawl_manifests, official

BASE='https://update.sonos.com/firmware/Gold/opaque/'

class CrawlTests(unittest.TestCase):
    def parse(self, xml, url=BASE+'update.upm'):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'x.upm';p.write_text(xml)
            return parse_manifest(p, source_url=url)

    def test_relative_caret_and_old_empty_image(self):
        m,cs=self.parse('<update_manifest version="1" default_version="55.1-74250" base_url="^55.1-74250"><image model="16" version="55.1-74250"/><image model="4" version="55.1-74250">55.1-74250-1-4.dmg</image></update_manifest>')
        self.assertIn(BASE+'55.1-74250-1-16.upd',{c.url for c in cs})
        self.assertIn(BASE+'55.1-74250-1-4.dmg',{c.url for c in cs})
        self.assertTrue(m['warnings'])

    def test_unrecognized_version_visible(self):
        m,cs=self.parse('<update_manifest version="99"></update_manifest>')
        self.assertTrue(any('unrecognized' in w for w in m['warnings']))

    def test_recursive_sibling_cycle_and_evidence(self):
        first=b'<update_manifest version="1.1"><image model="16" version="28.1-83040">28.1-83040-1-16.upd</image><link>../two/update.upm</link></update_manifest>'
        second=b'<update_manifest version="1.1"><link>../opaque/update.upm</link><image model="20" version="57.1-12345">57.1-12345-1-20.upd</image></update_manifest>'
        calls=[]
        def fetch(url):
            calls.append(url)
            return second
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({},[{'url':BASE+'update.upm','body':first}],[],Path(d),fetch=fetch,delay=0)
            self.assertEqual(len(r['manifests']),2)
            self.assertEqual(len(calls),1)
            self.assertEqual(len(r['directories']),2)
            self.assertFalse(r['limits_reached'])
            self.assertEqual(len(list(Path(d).glob('*.upm'))),2)
            self.assertIn(('28.1-83040',BASE.rstrip('/')),{(x['version'],x['directory']) for x in r['directories']})

    def test_missing_unsupported_malformed_and_limits_visible(self):
        def fetch(url):
            raise urllib.error.HTTPError(url,404,'missing',{},None)
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({},[],[BASE+'update.upm','https://evil.test/firmware/update.upm'],Path(d),fetch=fetch,delay=0)
        self.assertEqual({f['kind'] for f in r['failures']},{'not-found','unsupported'})
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({},[{'url':BASE+'update.upm','body':b'bad'}],[],Path(d),delay=0)
            self.assertTrue(r['failures'])
            self.assertEqual(len(list(Path(d).glob('*.upm'))),1)
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({},[],[BASE+'update.upm',BASE+'another.upm'],Path(d),max_manifests=1,fetch=fetch,delay=0)
            self.assertTrue(r['limits_reached'])

    def test_challenge_stops(self):
        calls=[]
        def fetch(url):
            calls.append(url);raise urllib.error.HTTPError(url,429,'slow down',{},None)
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({},[],[BASE+'update.upm',BASE+'another.upm'],Path(d),fetch=fetch,delay=0)
        self.assertTrue(r['blocked']);self.assertEqual(len(calls),1)

    def test_no_outside_hosts_credentials_or_ports(self):
        for u in ['https://evil.test/firmware/a.upm','https://u:p@update.sonos.com/firmware/a','https://update.sonos.com:444/firmware/a','file:///firmware/a']:
            self.assertFalse(official(u))


class CapturedManifestRegressionTests(unittest.TestCase):
    def test_gold_build_filename_not_campaign_build_and_seeded_directories(self):
        seed = BASE + '28.1-83040-1-16.upd'
        def missing(url):
            raise urllib.error.HTTPError(url,404,'missing',{},None)
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({},[],[seed],Path(d),fetch=missing,delay=0)
        self.assertEqual(r['directories'],[{'version':'28.1-83040','directory':BASE.rstrip('/')}])
        self.assertEqual(r['edges'][0]['url'],BASE+'update.upm')

    def test_relative_path_without_source_not_silently_correct(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'m';p.write_text('<update_manifest version="1"><image model="3" version="1.0">app.exe</image></update_manifest>')
            m,cs=parse_manifest(p)
        self.assertTrue(any('needs manifest source' in w for w in m['warnings']))

    def test_legacy_mirror_explicitly_seeded_only(self):
        url='https://github.com/systemcrash/sonos-firmware/blob/abc/update.upm'
        body=b'<update_manifest version="1.1"></update_manifest>'
        calls=[]
        def fetch(u):
            calls.append(u);return body
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({'source_manifests':[{'source_url':url}]},[],[],Path(d),fetch=fetch,delay=0)
        self.assertEqual(calls,['https://raw.githubusercontent.com/systemcrash/sonos-firmware/abc/update.upm'])
        self.assertEqual(len(r['manifests']),1)


class InitialFetchTests(unittest.TestCase):
    def test_feed_manifest_without_body_uses_guarded_retrieve(self):
        url='https://update.sonos.com/firmware/Prod/a/update.upm'
        body=b'<update_manifest version="1.1"></update_manifest>'
        calls=[]
        def fetch(u):
            calls.append(u);return body
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({},[{'url':url}],[],Path(d),fetch=fetch,delay=0)
        self.assertEqual(calls,[url])
        self.assertEqual(len(r['manifests']),1)


class ProbeSafetyTests(unittest.TestCase):
    def test_challenge_does_not_probe_remainder(self):
        from unittest.mock import patch
        from sonos_firmware.discovery import Candidate, probe_all
        cs=[Candidate('1.0',n,BASE+str(n)+'.upd','test',str(n)+'.upd') for n in range(3)]
        with patch('sonos_firmware.discovery.probe', return_value={'http_status':429, 'available':False}) as probe:
            rows=probe_all(cs)
        self.assertEqual(probe.call_count,1)
        self.assertEqual(len(rows),3)
        self.assertTrue(all(x.get('error') for x in rows))

    def test_probe_budget_marks_remainder_unchecked(self):
        from unittest.mock import patch
        from sonos_firmware.discovery import Candidate, probe_all
        cs=[Candidate('1.0',n,BASE+str(n)+'.upd','test',str(n)+'.upd') for n in range(3)]
        with patch('sonos_firmware.discovery.probe') as probe:
            rows=probe_all(cs, deadline=0)
        self.assertEqual(probe.call_count,0)
        self.assertTrue(all('budget' in x.get('error','') for x in rows))

    def test_seeded_chip_file_is_checked_without_list(self):
        seed={'url':BASE+'chip.bin','filename':'chip.bin','version':'3.1.4','package_model':44}
        def missing(url):
            raise urllib.error.HTTPError(url,404,'missing',{},None)
        with tempfile.TemporaryDirectory() as d:
            r=crawl_manifests({},[],[seed],Path(d),fetch=missing,delay=0)
        self.assertEqual([x.url for x in r['candidates']],[seed['url']])


class PublicCapturedReplayTests(unittest.TestCase):
    def test_all_150_recovered_urls_are_reached_offline(self):
        import json, hashlib
        root=Path(__file__).resolve().parents[1]
        fixture=root/'tests/fixtures/public-discovery'
        cache={}
        for receipt in json.loads((fixture/'receipts.json').read_text()):
            body=(fixture/receipt['file']).read_bytes()
            self.assertEqual(hashlib.sha256(body).hexdigest(),receipt['sha256'])
            cache[receipt['url']]=body
        def fetch(url):
            if url in cache:
                return cache[url]
            raise urllib.error.HTTPError(url,404,'offline fixture only, not a live404',{},None)
        seeds=json.loads((root/'config/discovery-seeds.json').read_text())
        with tempfile.TemporaryDirectory() as d:
            graph=crawl_manifests({},[],seeds,Path(d),fetch=fetch,delay=0)
        reached={c.url for c in graph['candidates']}
        for row in graph['directories']:
            reached.update(row['directory']+'/'+row['version']+'-1-'+str(n)+'.upd' for n in range(81))
        targets={u for u in (root/'docs/recovered-firmware-urls-2026-11-04.txt').read_text().splitlines() if u.startswith('https://')}
        self.assertEqual(len(targets),150)
        self.assertEqual(targets-reached,set())
        self.assertFalse(graph['limits_reached'])
