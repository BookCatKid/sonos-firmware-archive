"""Independent installer discovery through exact replay and verified publication."""
import io
import importlib.util
import json
import tempfile
import unittest
import urllib.error
import zipfile
from datetime import timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from unittest.mock import patch

from sonos_firmware import archive_index, archive_replay, installers, releases, retry
from sonos_firmware.app_archive import preserve
from sonos_firmware.storage import atomic_json
from test_app_archive import FakeGitHub, NOW

HOST = 'update-software.sonos.com'
URL = 'https://'+HOST+'/software/Sonos.exe'


class Response(io.BytesIO):
    status = 200

    def __init__(self, url, body, headers=None):
        super().__init__(body)
        self.url, self.headers = url, headers or {}

    def geturl(self):
        return self.url


def apk(body):
    output = io.BytesIO()
    with zipfile.ZipFile(output, 'w') as archive:
        archive.writestr('AndroidManifest.xml', body)
    return output.getvalue()


class InstallerHistoryTests(unittest.TestCase):
    def test_history_receipt_references_durable_index_instead_of_worker_discovery_file(self):
        self.add('20200101000000', b'MZ historical fixture')
        self.run_history()
        receipt = json.loads((self.root/'data/apps/desktop-archive.json').read_text())
        self.assertEqual(receipt['discovery'], 'data/apps/desktop-history-index.json')
        self.assertTrue((self.root/receipt['discovery']).is_file())
        receipt['discovery'] = '/vanished-worker/discovery.json'
        atomic_json(self.root/'data/apps/desktop-archive.json', receipt)
        self.responses.clear()
        self.run_history(now=NOW+timedelta(seconds=1))
        migrated = json.loads((self.root/'data/apps/desktop-archive.json').read_text())
        self.assertEqual(migrated['discovery'], 'data/apps/desktop-history-index.json')
        self.assertEqual(len(self.replays), 1)

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.github = FakeGitHub()
        self.rows = []
        self.responses = {}
        self.replays, self.live_requests, self.index_requests = [], [], []
        self.index_error = None
        self.addCleanup(patch.stopall)
        patch.object(releases, 'remote_assets', side_effect=self.github.assets).start()
        patch.object(releases.subprocess, 'run', side_effect=self.github.command).start()
        patch.object(archive_replay.urllib.request, 'build_opener', return_value=self).start()
        patch.object(installers, 'open_advertised', side_effect=self.live).start()
        patch.object(installers, 'discover_sources', side_effect=lambda previous, **kwargs:
            archive_index.discover_sources(previous, fetch=self.index, **kwargs)).start()

    def index(self, url):
        self.index_requests.append(url)
        if self.index_error:
            raise self.index_error
        root = parse_qs(urlsplit(url).query)['url'][0]
        return [['timestamp', 'original', 'digest'], *(self.rows if root in (HOST+'/', 'sonos.com/') else [])]

    def live(self, request, **kwargs):
        self.live_requests.append(request.full_url)
        return Response(request.full_url, b'MZ currently served bytes')

    def open(self, request, **kwargs):
        self.replays.append(request.full_url)
        value = self.responses[request.full_url]
        if isinstance(value, Exception):
            raise value
        if isinstance(value, tuple):
            return Response(request.full_url, *value)
        return Response(request.full_url, value)

    def add(self, timestamp, body, url=URL, headers=None):
        original = url.replace('https:', 'http:')
        capture = {'timestamp': timestamp, 'original_url': original}
        self.rows.append([timestamp, original, timestamp])
        replay = archive_replay.replay_url(url, capture, allowed=installers.official_source)
        self.responses[replay] = (body, headers) if headers else body
        return replay

    def run_history(self, kind='desktop', now=NOW, **kwargs):
        with patch.object(retry, '_now', return_value=now):
            return installers.archive_history(self.root, self.root/'worker', 'owner/archive', kind,
                                              pages=3, **kwargs)

    def receipt(self, kind='desktop'):
        return json.loads((self.root/f'data/apps/{kind}-archive.json').read_text())

    def test_empty_archive_replays_every_variant_even_when_live_url_still_works(self):
        self.add('20200101000000', b'MZ older installer')
        self.add('20210101000000', b'MZ newer installer')
        self.assertEqual(self.github.releases, {})
        result = self.run_history()
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['artifact_total'], 2)
        self.assertEqual(self.live_requests, [])
        self.assertEqual(len(self.github.uploads), 2)
        self.assertEqual({row[3] for row in self.github.uploads}, {b'MZ older installer', b'MZ newer installer'})
        self.assertTrue(all(row['sha256'] in row['release_asset'] for row in self.receipt()['artifacts']))
        self.assertTrue(all(row['acquisitions'][0]['archive_capture']['original_url'].startswith('http:')
                            for row in self.receipt()['artifacts']))

    def test_lost_upload_response_recovers_without_replay_or_worker_payload(self):
        self.add('20200101000000', b'MZ older installer')
        self.github.lose_upload_response = True
        self.assertEqual(self.run_history()['artifact_total'], 0)
        self.responses.clear()
        self.rows.clear()
        result = self.run_history(now=NOW+timedelta(hours=2))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(len(self.replays), 1)
        self.assertFalse(list(self.root.rglob('*.exe')))

    def test_preserved_historical_capture_refresh_only_checks_destination(self):
        self.add('20200101000000', b'MZ older installer')
        self.run_history()
        self.responses.clear()
        self.rows.clear()
        self.assertEqual(self.run_history(now=NOW+timedelta(days=8))['status'], 'ok')
        self.assertEqual(len(self.replays), 1)

    def test_mobile_capture_variants_share_the_same_publisher_and_validate_zip(self):
        url = URL.replace('.exe', '.apk')
        self.add('20200101000000', apk(b'old manifest'), url)
        self.add('20210101000000', apk(b'new manifest'), url)
        with patch('sonos_firmware.apk.inspect_official', return_value={'verified': True, 'package': 'fixture.app'}), \
             patch.object(installers.shutil, 'which', return_value='/fixture/java'):
            result = self.run_history('mobile')
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['artifact_total'], 2)
        saved = json.loads((self.root/'data/apps/mobile-archive.json').read_text())
        self.assertTrue(all(row['verification']['verified'] for row in saved['artifacts']))
        self.assertEqual({row[1] for row in self.github.uploads}, {'apps-fireos-s2'})
        self.assertEqual(self.live_requests, [])

    def test_challenge_pauses_other_replays_and_truncation_never_publishes(self):
        first = self.add('20200101000000', b'<html>challenge</html>', headers={'Content-Type': 'text/html'})
        self.add('20210101000000', b'MZ newer installer')
        self.assertEqual(self.run_history()['artifact_total'], 0)
        self.assertEqual(self.replays, [first])
        self.responses[first] = (b'MZ short', {'Content-Length': '100'})
        self.replays.clear()
        result = self.run_history(now=NOW+timedelta(hours=2))
        self.assertEqual(result['artifact_total'], 1)
        self.assertEqual(len(self.replays), 2)
        self.assertTrue(any('Content-Length' in str(row) for row in result['errors']))

    def test_index_challenge_retains_current_publication_and_queued_history(self):
        self.add('20200101000000', b'MZ older installer')
        # Save indexed evidence from an earlier run without acquiring its bytes.
        with patch.object(retry, '_now', return_value=NOW):
            index = archive_index.discover_sources({}, fetch=self.index, pages=3,
                extensions=('exe', 'dmg'), allowed=installers.official_source)
            atomic_json(self.root/'data/apps/desktop-history-index.json', index)
            current = self.root/'current.json'
            atomic_json(current, {'candidates': [{'url': URL, 'family': 's2', 'platform': 'windows',
                                                 'version': 'unknown', 'sources': [], 'current': True}]})
            preserve(installers.source(), current, self.root/'data/apps/desktop-archive.json',
                     'owner/archive', root=self.root)
        self.index_error = urllib.error.HTTPError('index', 429, 'challenge', {}, None)
        self.assertEqual(self.run_history()['artifact_total'], 1)
        self.assertEqual(self.replays, [])
        self.assertEqual(self.receipt()['pending_total'], 1)
        self.index_error = None
        self.assertEqual(self.run_history(now=NOW+timedelta(hours=2))['artifact_total'], 2)
        self.assertEqual(len(self.live_requests), 1)

    def test_targets_schedule_history_even_when_current_discovery_fails(self):
        from sonos_firmware.platform.registry import targets
        from sonos_firmware.platform.runner import plan
        for kind in ('desktop', 'mobile'):
            with self.subTest(kind=kind):
                target = targets(Path(__file__).resolve().parents[1])[kind]
                steps = plan(target, self.root, 'owner/archive')['steps']
                self.assertIn('archive-installer-history', steps[-1]['command'])
                self.assertTrue(steps[-1]['after_failure'])
                self.assertIn(f'data/apps/{kind}-history-index.json', target.metadata)

    def test_indexed_urls_feed_live_discovery_and_desktop_sibling_inference(self):
        import sys
        for kind, suffix in (('desktop', 'exe'), ('mobile', 'apk')):
            with self.subTest(kind=kind):
                url = URL.rsplit('.', 1)[0]+'.'+suffix
                index_path = self.root/f'data/apps/{kind}-history-index.json'
                atomic_json(index_path, {'artifacts': {url: {'captures': {}}}})
                script = Path(__file__).resolve().parents[1]/f'scripts/discover_{kind}_installers.py'
                spec = importlib.util.spec_from_file_location('indexed_'+kind, script)
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                output = index_path.with_name(kind+'-discovery.json')
                with patch.object(sys, 'argv', ['discover', '--output', str(output), '--archive-root', str(self.root)]), \
                     patch.object(module, 'probe', side_effect=lambda row: {**row, 'http_status': 200}):
                    if kind == 'desktop':
                        with patch.object(module, 'winget_candidates'), patch.object(module, 'winget_history', return_value={'status': 'ok', 'pending_total': 0, 'errors': []}), patch.object(module, 'homebrew_candidates'), \
                             patch.object(module, 'check_direct_redirects', return_value=[]):
                            module.main()
                    else:
                        with patch.object(module, 'resolve_latest_redirect', return_value={}):
                            module.main()
                urls = {row['url'] for row in json.loads(output.read_text())['candidates']}
                self.assertIn(url, urls)
                if kind == 'desktop':
                    self.assertIn(url.rsplit('.', 1)[0]+'.dmg', urls)
