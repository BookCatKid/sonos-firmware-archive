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
from tests.apps.test_app_archive import FakeGitHub, NOW

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
    def test_current_discovery_seeds_index_search_without_any_prior_publication(self):
        atomic_json(self.root/'data/apps/desktop-discovery.json', {'candidates': [{'url': URL}]})
        with patch.object(installers, 'discover_sources', return_value={
                'status': 'ok', 'errors': [], 'artifacts': {}}) as index:
            self.run_history()
        self.assertIn(URL, index.call_args.kwargs['seed_urls'])

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
        # These payloads model replay transport, not executable version resources.
        patch.object(installers, 'inspect_desktop', return_value={'desktop_metadata': {'app_version': '1.0'}}).start()
        patch.object(releases, 'remote_assets', side_effect=self.github.assets).start()
        patch.object(releases.subprocess, 'run', side_effect=self.github.command).start()
        patch.object(archive_replay.urllib.request, 'build_opener', return_value=self).start()
        patch.object(installers, 'open_advertised', side_effect=self.live).start()
        patch.object(installers, 'discover_sources', side_effect=lambda previous, **kwargs:
            archive_index.discover_sources(previous, fetch=self.index,
                **{key: value for key, value in kwargs.items() if key != 'seed_urls'})).start()

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

    def test_preserved_campaign_installer_yields_a_pivot_context(self):
        campaign = 'https://'+HOST+'/software/tok3n/Sonos_90.0-67171.dmg'
        self.add('20200101000000', b'dmg campaign fixture', campaign)
        with patch.object(installers, 'inspect_desktop',
                          return_value={'desktop_metadata': {'app_version': '17.2.0'}}) as inspect:
            self.run_history()
        inspect.assert_called()
        artifact = next(row for row in self.receipt()['artifacts'] if campaign in row['source_urls'])
        contexts = installers.installer_pivot_contexts(artifact)
        self.assertEqual(installers.pivotable(artifact), True)
        self.assertEqual({(context['build'], context['app_version'], context['token']) for context in contexts},
                         {('90.0-67171', '17.2', 'tok3n'), ('90.0-67171', '17.2.0', 'tok3n')})

    def test_classic_installer_is_preserved_without_inspection(self):
        classic = 'https://'+HOST+'/software/mac/mdcr/SonosDesktopController131.dmg'
        self.add('20200101000000', b'classic fixture', classic)
        with patch.object(installers, 'inspect_desktop') as inspect:
            self.run_history()
        inspect.assert_not_called()
        self.assertEqual(installers.installer_pivot_contexts(
            next(row for row in self.receipt()['artifacts'] if classic in row['source_urls'])), [])

    def run_history(self, kind='desktop', now=NOW, **kwargs):
        with patch.object(retry, '_now', return_value=now):
            return installers.archive_history(self.root, self.root/'worker', 'owner/archive', kind, **kwargs)

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
            index = archive_index.discover_sources({}, fetch=self.index,
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
                target = targets(Path(__file__).resolve().parents[2])[kind]
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
                script = Path(__file__).resolve().parents[2]/f'scripts/apps/discover_{kind}_installers.py'
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

class DesktopMetadataTests(unittest.TestCase):
    def bundle_archive(self, plists):
        import io, zipfile, plistlib
        output = io.BytesIO()
        with zipfile.ZipFile(output, 'w') as archive:
            for member, values in plists:
                archive.writestr(member, plistlib.dumps(values))
        return output.getvalue()

    def inspect(self, plists):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'installer.zip'
            path.write_bytes(self.bundle_archive(plists))
            return installers.inspect_desktop({}, path)

    def test_release_version_is_read_from_the_application_bundle(self):
        result = self.inspect([('Sonos/Sonos.app/Contents/Info.plist',
                                {'CFBundleIdentifier': 'com.sonos.macController2',
                                 'CFBundleShortVersionString': '17.2', 'CFBundleVersion': '90.0.67171'})])
        metadata = result['desktop_metadata']
        self.assertEqual(metadata['app_version'], '17.2')
        self.assertEqual(metadata['bundle_version'], '90.0.67171')
        self.assertEqual([context['app_version'] for context in installers.installer_pivot_contexts(
            {'verification': result, 'source_urls': ['https://update-software.sonos.com/software/t/Sonos_90.0-67171.dmg'],
             'sha256': '0'})], ['17.2'])

    def test_a_classic_build_only_bundle_keeps_metadata_and_defers_its_version(self):
        result = self.inspect([
            ('Sonos/Sonos S1 Controller.app/Contents/Info.plist',
             {'CFBundleIdentifier': 'com.sonos.macController', 'CFBundleVersion': '??.??'}),
            ('Sonos/Sonos S1 Controller.app/Contents/Resources/Sonos Installer.app/Contents/Info.plist',
             {'CFBundleIdentifier': 'com.sonos.installer', 'CFBundleVersion': '57.22.59130'})])
        metadata = result['desktop_metadata']
        self.assertNotIn('app_version', metadata)
        self.assertEqual(metadata['bundle_version'], '57.22.59130')
        contexts = installers.installer_pivot_contexts(
            {'verification': result, 'source_urls': ['https://update-software.sonos.com/software/t/Sonos_57.22-59130.dmg'],
             'sha256': '0'})
        self.assertEqual([(c['build'], c['token'], c['app_version']) for c in contexts],
                         [('57.22-59130', 't', None)])

    def test_a_campaign_url_alone_supplies_a_pivot_context(self):
        contexts = installers.installer_pivot_contexts({
            'sha256': '0', 'source_urls': ['https://update-software.sonos.com/software/tok/Sonos_57.22-59130.dmg']})
        self.assertEqual([(c['build'], c['token'], c['app_version']) for c in contexts],
                         [('57.22-59130', 'tok', None)])
        self.assertEqual(installers.installer_pivot_contexts({
            'sha256': '0', 'source_urls': ['https://update-software.sonos.com/software/mac/mdcr/'
                                           'SonosDesktopController131.dmg']}), [])

    def test_a_helper_bundle_version_is_not_a_release_version(self):
        result = self.inspect([
            ('Sonos/Sonos.app/Contents/Info.plist',
             {'CFBundleIdentifier': 'com.sonos.macController2', 'CFBundleVersion': '90.0.67171'}),
            ('Sonos/Sonos.app/Contents/Resources/Helper.app/Contents/Info.plist',
             {'CFBundleIdentifier': 'com.sonos.installer', 'CFBundleShortVersionString': '5.0.0'})])
        metadata = result['desktop_metadata']
        self.assertNotIn('app_version', metadata)
        self.assertEqual(metadata['bundle_version'], '90.0.67171')

    def test_a_malformed_plist_does_not_hide_a_later_bundle(self):
        with tempfile.TemporaryDirectory() as temporary:
            import plistlib, zipfile
            path = Path(temporary)/'installer.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('Broken.app/Contents/Info.plist', b'<plist><dict>')
                archive.writestr('Sonos.app/Contents/Info.plist', plistlib.dumps(
                    {'CFBundleIdentifier': 'com.sonos.macController2', 'CFBundleShortVersionString': '17.2',
                     'CFBundleVersion': '90.0.67171'}))
            result = installers.inspect_desktop({}, path)
        self.assertEqual(result['desktop_metadata']['app_version'], '17.2')

    def test_a_non_sonos_bundle_is_not_metadata(self):
        result = self.inspect([('Other.app/Contents/Info.plist',
                                {'CFBundleIdentifier': 'com.example.other',
                                 'CFBundleShortVersionString': '1.2.3'})])
        self.assertIn('discovery_errors', result)



class DecoderOutputTests(unittest.TestCase):
    def run_decoder(self, output, returncode, allow_partial):
        def decode(command, stdout=None, stderr=None, check=None):
            if output and stdout is not None:
                stdout.write(output)
            return installers.subprocess.CompletedProcess(command, returncode)
        with tempfile.TemporaryDirectory() as temporary:
            destination = Path(temporary)/'output'
            with patch.object(installers.subprocess, 'run', side_effect=decode):
                try:
                    installers._archive_output(['decoder'], destination, allow_partial=allow_partial)
                except ValueError as error:
                    return str(error)
                return destination.read_bytes()

    def plist(self, version='1.2.3'):
        import plistlib
        return plistlib.dumps({'CFBundleIdentifier': 'com.sonos.controller',
                               'CFBundleShortVersionString': version})

    def test_a_partial_archive_is_usable_when_its_output_parses(self):
        import plistlib
        body = self.plist()
        result = self.run_decoder(body, 2, True)
        self.assertEqual(result, body)
        self.assertEqual(plistlib.loads(result)['CFBundleShortVersionString'], '1.2.3')

    def test_a_partial_archive_without_output_is_a_failure(self):
        self.assertIn('decoder failed', self.run_decoder(b'', 2, True))

    def test_a_failure_remains_a_failure_when_partial_output_is_not_allowed(self):
        self.assertIn('decoder failed', self.run_decoder(self.plist(), 2, False))

    def test_a_short_member_is_not_usable(self):
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary)/'member'
            output.write_bytes(b'prefix')
            self.assertIsNone(installers._complete_member(output, '40'))
            self.assertIsNone(installers._complete_member(output, ''))
            self.assertIsNone(installers._complete_member(output, None))
            self.assertEqual(installers._complete_member(output, '6'), b'prefix')
