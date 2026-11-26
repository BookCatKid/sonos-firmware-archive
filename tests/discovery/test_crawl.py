import tempfile
import unittest
import urllib.error
from pathlib import Path
from sonos_firmware.crawl import crawl_manifests, official

BASE='https://update.sonos.com/firmware/Gold/opaque/'

class CrawlTests(unittest.TestCase):


    def test_manifest_serving_host_supplies_an_independent_payload_lead(self):
        root = 'https://update-stage.sonos.com/firmware/Prod/fixture/update.upm'
        production = 'https://update-firmware.sonos.com/firmware/Prod/fixture/1.0-1-1-3.exe'
        body = ('<update_manifest version="1.1"><image model="3" version="1.0-1">'
                + production + '</image></update_manifest>').encode()
        def missing(url):
            raise urllib.error.HTTPError(url, 404, 'missing', {}, None)
        with tempfile.TemporaryDirectory() as temporary:
            graph = crawl_manifests({}, [{'url': root, 'body': body}], [], Path(temporary),
                                    fetch=missing, delay=0)
        candidates = {row.url: row for row in graph['candidates']}
        alternate = production.replace('update-firmware.', 'update-stage.')
        self.assertIn(production, candidates)
        self.assertIn(alternate, candidates)
        self.assertEqual(candidates[alternate].advertised_by, root)


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
            self.assertFalse(r['deadline_reached'])
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
        from unittest.mock import patch
        requested = []
        def one_fetch(url):
            requested.append(url)
            return fetch(url)
        with tempfile.TemporaryDirectory() as d, \
             patch('sonos_firmware.crawl.winding_down', side_effect=lambda: len(requested) >= 1):
            r=crawl_manifests({},[],[BASE+'update.upm',BASE+'another.upm'],Path(d),fetch=one_fetch,delay=0)
            self.assertTrue(r['deadline_reached'])


    def test_no_outside_hosts_credentials_or_ports(self):
        for u in ['https://evil.test/firmware/a.upm','https://u:p@update.sonos.com/firmware/a','https://update.sonos.com:444/firmware/a','file:///firmware/a']:
            self.assertFalse(official(u))


class ProbeSafetyTests(unittest.TestCase):


    def test_challenge_does_not_block_unrelated_hosts(self):
        from unittest.mock import patch
        from sonos_firmware.discovery import Candidate, probe_all
        candidates = [
            Candidate('1.0', 1, 'https://update.sonos.com/one.upd', 'test', 'one.upd'),
            Candidate('1.0', 2, 'https://update.sonos.com/two.upd', 'test', 'two.upd'),
            Candidate('1.0', 3, 'https://update-firmware.sonos.com/three.upd', 'test', 'three.upd'),
        ]
        outcomes = [{'http_status': 429, 'available': False},
                    {'http_status': 200, 'available': True}]
        with patch('sonos_firmware.discovery.probe', side_effect=outcomes) as probe:
            rows = probe_all(candidates)
        self.assertEqual(probe.call_count, 2)
        self.assertIn('cooling down', rows[1]['error'])
        self.assertTrue(rows[2]['available'])


class PublicCapturedReplayTests(unittest.TestCase):
    def test_all_150_recovered_urls_are_reached_offline(self):
        import json, hashlib
        root=Path(__file__).resolve().parents[2]
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
        seeds=json.loads((fixture/'observed-seeds.json').read_text())
        with tempfile.TemporaryDirectory() as d:
            graph=crawl_manifests({},[],seeds,Path(d),fetch=fetch,delay=0)
        reached={c.url for c in graph['candidates']}
        for row in graph['directories']:
            reached.update(row['directory']+'/'+row['version']+'-1-'+str(n)+'.upd' for n in range(81))
        targets={u for u in (fixture/'recovered-firmware-urls.txt').read_text().splitlines() if u.startswith('https://')}
        self.assertEqual(len(targets),150)
        self.assertEqual(targets-reached,set())
        self.assertFalse(graph['deadline_reached'])

class OutOfScopeClassificationTests(unittest.TestCase):

    def test_private_manifest_host_stays_fail_closed(self):
        body = (b'<update_manifest version="1.1">'
                b'<link>https://127.0.0.1/Prod/99.0-1/update.upm</link>'
                b'</update_manifest>')
        with tempfile.TemporaryDirectory() as d:
            r = crawl_manifests({}, [{'url': BASE + 'update.upm', 'body': body}], [], Path(d), delay=0)
        kinds = {f['kind'] for f in r['failures']}
        self.assertEqual(kinds, {'unsupported'})
