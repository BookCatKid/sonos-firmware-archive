"""Lost web publication receipts are recoverable from their published bundles.

A worker can publish every asset of a capture and then lose the metadata commit
(interrupted job, rejected rebase, crash after upload). The Release still holds
the bundle, and that bundle carries the capture's own `capture.json`. Recovery
must rebuild only the receipts that the Release can independently prove.
"""
import hashlib
import importlib.util
import io
import json
import tarfile
import unittest
import urllib.error
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('web_reconcile', ROOT/'scripts/apps/reconcile_web_release_assets.py')
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)

REPO = 'owner/archive'
TAG = 'apps-web-pro'
RUN = 'https://github.com/owner/archive/actions/runs/1'


def document(capture_id='20260101T000000Z', *, app='pro', resources=2, publish=True, fingerprint='fp-1'):
    rows = [{
        'requested_url': f'https://pro.sonos.com/asset-{index}.js',
        'final_url': f'https://pro.sonos.com/asset-{index}.js',
        'status': 200, 'outcome': 'captured', 'bytes': 10 + index,
        'sha256': hashlib.sha256(f'body-{index}'.encode()).hexdigest(),
        'file': f'bodies/{capture_id}--asset-{index}.js',
        'release_asset': f'{capture_id}--asset-{index}.js',
    } for index in range(resources)]
    result = {
        'schema_version': 1, 'app': app, 'entry_url': 'https://pro.sonos.com/',
        'captured_at': '2026-01-01T00:00:00+00:00', 'capture_id': capture_id,
        'probe_fingerprint': fingerprint, 'deployment_fingerprint': fingerprint,
        'identity_complete': True, 'resource_total': resources, 'captured_total': resources,
        'gap_total': 0, 'release_tag': TAG, 'resources': rows, 'pending_urls': [], 'complete': True,
        'bundle': {'file': f'{capture_id}.tar.gz', 'bytes': 123, 'sha256': 'b'*64,
                   'release_asset': f'sonos-{app}-{capture_id}--complete.tar.gz'},
    }
    if publish:
        for item in [*rows, result['bundle']]:
            item.update(status='verified', repository=REPO, release_tag=TAG,
                        release_url=f'https://github.com/{REPO}/releases/download/{TAG}/{item["release_asset"]}')
    return result


def release_assets(doc, *, omit=(), corrupt=(), bundle=None):
    names = [row['release_asset'] for row in doc['resources']]
    if bundle:
        names.append(bundle['release_asset'])
    assets = {}
    for name in names:
        if name in omit:
            continue
        if bundle and name == bundle['release_asset']:
            size, digest = bundle['bytes'], bundle['sha256']
        else:
            row = next(item for item in doc['resources'] if item['release_asset'] == name)
            size, digest = row['bytes'], row['sha256']
        if name in corrupt:
            digest = 'c'*64
        assets[name] = {'name': name, 'size': size, 'digest': 'sha256:'+digest,
                        'browser_download_url': f'https://github.com/{REPO}/releases/download/{TAG}/{name}'}
    return assets


class WebReceiptRecoveryTests(unittest.TestCase):


    def test_unavailable_partition_is_reported_without_claiming_a_complete_inventory(self):
        def inventory(repo, tag):
            if tag != TAG:
                raise urllib.error.URLError('inventory unavailable')
            return list(self.assets.values())
        with patch.object(reconcile, 'matching_tags', return_value=[TAG, TAG+'-part-2']), \
             patch.object(reconcile, 'release_assets', side_effect=inventory), self.reader:
            state = {'schema_version': 1, 'deployments': []}
            report = reconcile.reconcile(state, repository=REPO, run_url=RUN, app_names=['pro'])
        self.assertEqual(len(report['errors']), 1)
        self.assertEqual(report['recovered'], [])
        self.assertEqual(state['deployments'], [])

    def test_capture_split_across_release_partitions_recovers_actual_locations(self):
        overflow = TAG+'-part-2'
        names = list(self.assets)
        base_assets = [self.assets[names[0]]]
        overflow_assets = [dict(self.assets[name], browser_download_url=
            self.assets[name]['browser_download_url'].replace('/'+TAG+'/', '/'+overflow+'/')) for name in names[1:]]
        with patch.object(reconcile, 'matching_tags', return_value=[TAG, overflow], create=True), \
             patch.object(reconcile, 'release_assets', side_effect=lambda repo, tag:
                 base_assets if tag == TAG else overflow_assets), self.reader:
            state = {'schema_version': 1, 'deployments': []}
            report = reconcile.reconcile(state, repository=REPO, run_url=RUN, app_names=['pro'])
        self.assertEqual(report['errors'], [])
        self.assertEqual(len(report['recovered']), 1)
        recovered = state['deployments'][0]
        self.assertEqual([r['release_tag'] for r in recovered['resources']], [TAG, overflow])
        self.assertEqual(recovered['bundle']['release_tag'], overflow)

    def setUp(self):
        tags = patch.object(reconcile, 'matching_tags', return_value=[TAG])
        tags.start()
        self.addCleanup(tags.stop)
        self.document = document()
        self.assets = release_assets(self.document, bundle=self.document['bundle'])
        self.error = patch.object(reconcile, 'release_assets', side_effect=lambda repo, tag: list(self.assets.values()))
        self.reader = patch.object(reconcile, 'read_capture_document', side_effect=lambda url: json.loads(json.dumps(self.document)))

    def recover(self, state=None):
        state = state if state is not None else {'schema_version': 1, 'deployments': []}
        with self.error, self.reader:
            report = reconcile.reconcile(state, repository=REPO, run_url=RUN, app_names=['pro'])
        return state, report

    def test_a_capture_whose_bundle_proves_publication_is_restored(self):
        state, report = self.recover()
        self.assertEqual(report['errors'], [])
        self.assertEqual([row['capture_id'] for row in report['recovered']], ['20260101T000000Z'])
        self.assertEqual(len(state['deployments']), 1)
        restored = state['deployments'][0]
        self.assertEqual(restored['deployment_fingerprint'], 'fp-1')
        self.assertEqual(restored['bundle']['status'], 'verified')
        self.assertEqual(restored['bundle']['receipt_recovered_from'], RUN)
        self.assertEqual([row['status'] for row in restored['resources']], ['verified', 'verified'])
        self.assertEqual(restored['receipt_recovered_from'], RUN)
        # A local worker path is never part of a durable receipt.
        self.assertNotIn('file', restored['bundle'])


    def test_a_missing_resource_leaves_the_capture_unrecorded(self):
        self.assets = release_assets(self.document, bundle=self.document["bundle"], omit={'20260101T000000Z--asset-1.js'})
        state, report = self.recover()
        self.assertEqual(report['recovered'], [])
        self.assertEqual(len(report['errors']), 1)
        self.assertIn('absent from the release', report['errors'][0]['error'])
        self.assertEqual(state['deployments'], [])

    def test_a_resource_with_different_content_leaves_the_capture_unrecorded(self):
        self.assets = release_assets(self.document, bundle=self.document["bundle"], corrupt={'20260101T000000Z--asset-0.js'})
        state, report = self.recover()
        self.assertEqual(report['recovered'], [])
        self.assertIn('disagrees with release metadata', report['errors'][0]['error'])
        self.assertEqual(state['deployments'], [])


class BundleReadingTests(unittest.TestCase):


    def test_a_bundle_without_a_capture_document_is_rejected(self):
        stream = io.BytesIO()
        with tarfile.open(fileobj=stream, mode='w:gz') as archive:
            info = tarfile.TarInfo('sonos-pro-x/bodies/only.js')
            info.size = 2
            archive.addfile(info, io.BytesIO(b'{}'))
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch.object(reconcile.urllib.request, 'urlopen', return_value=Response(stream.getvalue())):
            with self.assertRaises(ValueError) as raised:
                reconcile.read_capture_document('https://github.com/fixture/bundle.tar.gz')
        self.assertIn('exactly one capture.json', str(raised.exception))


if __name__ == '__main__':
    unittest.main()
