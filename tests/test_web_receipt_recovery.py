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
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('web_reconcile', ROOT/'scripts/reconcile_web_release_assets.py')
reconcile = importlib.util.module_from_spec(spec)
spec.loader.exec_module(reconcile)

REPO = 'owner/archive'
TAG = 'apps-web-pro'
RUN = 'https://github.com/owner/archive/actions/runs/1'


def bundle_body(document):
    raw = json.dumps(document, indent=2).encode()
    stream = io.BytesIO()
    with tarfile.open(fileobj=stream, mode='w:gz') as archive:
        info = tarfile.TarInfo(f"sonos-{document['app']}-{document['capture_id']}/capture.json")
        info.size = len(raw)
        archive.addfile(info, io.BytesIO(raw))
    return stream.getvalue()


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


def _prepublication(capture_id='20260101T000000Z'):
    """A capture document as the bundle embeds it: before its own receipt."""
    snapshot = document(capture_id, publish=False)
    snapshot.pop('bundle')
    return snapshot


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
    def setUp(self):
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

    def test_an_already_recorded_capture_is_not_duplicated(self):
        state, _ = self.recover()
        before = json.loads(json.dumps(state))
        state, report = self.recover(state)
        self.assertEqual(report['recovered'], [])
        # The bundle is already recorded, so it is skipped before its contents
        # are re-judged; the deployment list never grows a duplicate.
        self.assertEqual([row['reason'] for row in report['skipped']], ['bundle already recorded'])
        self.assertEqual([row['capture_id'] for row in report['skipped']], ['20260101T000000Z'])
        self.assertEqual(state['deployments'], before['deployments'])

    def test_a_superseded_capture_is_retained_once_and_never_duplicated(self):
        state = {'schema_version': 1, 'deployments': []}
        with self.error, self.reader:
            first = reconcile.reconcile(state, repository=REPO, run_url=RUN, app_names=['pro'])
        self.assertEqual([row['capture_id'] for row in first['recovered']], ['20260101T000000Z'])
        # A second capture of the same deployment is retained as evidence: the
        # recorded deployment already holds that identity.
        later = document('20260102T000000Z', fingerprint='fp-1')
        self.assets = release_assets(later, bundle=later['bundle'])
        self.reader = patch.object(reconcile, 'read_capture_document', side_effect=lambda url: json.loads(json.dumps(later)))
        with self.error, self.reader:
            second = reconcile.reconcile(state, repository=REPO, run_url=RUN, app_names=['pro'])
        self.assertEqual([row['capture_id'] for row in second['superseded']], ['20260102T000000Z'])
        self.assertEqual([row['capture_id'] for row in state['superseded_captures']], ['20260102T000000Z'])
        # A third run must not append the same evidence again.
        with self.error, self.reader:
            third = reconcile.reconcile(state, repository=REPO, run_url=RUN, app_names=['pro'])
        self.assertEqual(third['superseded'], [])
        self.assertEqual([row['capture_id'] for row in state['superseded_captures']], ['20260102T000000Z'])
        self.assertEqual(len(state['deployments']), 1)

    def test_a_missing_resource_leaves_the_capture_unrecorded(self):
        self.assets = release_assets(self.document, bundle=self.document["bundle"], omit={f'20260101T000000Z--asset-1.js'})
        state, report = self.recover()
        self.assertEqual(report['recovered'], [])
        self.assertEqual(len(report['errors']), 1)
        self.assertIn('absent from the release', report['errors'][0]['error'])
        self.assertEqual(state['deployments'], [])

    def test_a_resource_with_different_content_leaves_the_capture_unrecorded(self):
        self.assets = release_assets(self.document, bundle=self.document["bundle"], corrupt={f'20260101T000000Z--asset-0.js'})
        state, report = self.recover()
        self.assertEqual(report['recovered'], [])
        self.assertIn('disagrees with release metadata', report['errors'][0]['error'])
        self.assertEqual(state['deployments'], [])

    def test_a_capture_for_another_release_tag_is_refused(self):
        self.document['release_tag'] = 'apps-web-controller'
        state, report = self.recover()
        self.assertEqual(report['recovered'], [])
        self.assertIn('does not match', report['errors'][0]['error'])
        self.assertEqual(state['deployments'], [])

    def test_a_capture_without_a_deployment_identity_is_refused(self):
        for fingerprint in (None, ''):
            with self.subTest(fingerprint=fingerprint):
                self.document['deployment_fingerprint'] = fingerprint
                state, report = self.recover()
                self.assertEqual(report['recovered'], [])
                self.assertIn('deployment fingerprint', report['errors'][0]['error'])

    def test_a_bundle_name_that_disagrees_with_its_capture_json_is_refused(self):
        self.reader = patch.object(reconcile, 'read_capture_document',
                                   side_effect=lambda url: {**self.document, 'capture_id': '20260101T999999Z'})
        state, report = self.recover()
        self.assertEqual(report['recovered'], [])
        self.assertIn('capture.json says', report['errors'][0]['error'])

    def test_bundle_names_parse_only_for_the_expected_shape(self):
        for name, expected in (
            ('sonos-pro-20260101T000000Z--complete.tar.gz', '20260101T000000Z'),
            ('sonos-pro-20260101T000000Z--abcdef123456--complete.tar.gz', '20260101T000000Z'),
            ('sonos-controller-20260101T000000Z--complete.tar.gz', None),
            ('20260101T000000Z--asset-0.js', None),
            ('sonos-pro--complete.tar.gz', None),
            ('sonos-pro-20260101T000000Z--zzzzzzzzzzzz--complete.tar.gz', None),
        ):
            with self.subTest(name=name):
                self.assertEqual(reconcile.bundle_capture_id(name, 'pro'), expected)

    def test_a_dry_run_reports_without_mutating_the_receipt(self):
        state = {'schema_version': 1, 'deployments': []}
        with self.error, self.reader:
            report = reconcile.reconcile(state, repository=REPO, run_url=RUN, app_names=['pro'], dry_run=True)
        self.assertEqual(len(report['recovered']), 1)
        self.assertEqual(state['deployments'], [])

    def test_an_unreachable_release_is_reported_without_writing_the_receipt(self):
        state = {'schema_version': 1, 'deployments': []}
        with patch.object(reconcile, 'release_assets',
                          side_effect=__import__('subprocess').CalledProcessError(1, ['gh'])), self.reader:
            report = reconcile.reconcile(state, repository=REPO, run_url=RUN, app_names=['pro'])
        self.assertEqual(report['recovered'], [])
        self.assertEqual(len(report['errors']), 1)
        self.assertEqual(state['deployments'], [])


class BundleReadingTests(unittest.TestCase):
    def test_capture_document_is_read_from_the_bundle_itself(self):
        body = bundle_body(document())
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): return False
        with patch.object(reconcile.urllib.request, 'urlopen', return_value=Response(body)):
            recovered = reconcile.read_capture_document('https://github.com/fixture/bundle.tar.gz')
        self.assertEqual(recovered['capture_id'], '20260101T000000Z')

    def test_a_prepublication_snapshot_is_recovered_from_release_metadata(self):
        """Bundles embed capture.json as it stood before the bundle receipt existed."""
        snapshot = _prepublication()
        body = bundle_body(snapshot)
        class Response(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *args): return False
        assets = release_assets(snapshot, bundle={'release_asset': f"sonos-pro-{snapshot['capture_id']}--complete.tar.gz",
                                                  'bytes': 123, 'sha256': 'b'*64})
        bundle_name = f"sonos-pro-{snapshot['capture_id']}--complete.tar.gz"
        with patch.object(reconcile.urllib.request, 'urlopen', return_value=Response(body)):
            recovered = reconcile.read_capture_document('https://github.com/fixture/bundle.tar.gz')
        self.assertNotIn('bundle', recovered)
        result = reconcile.recover_capture(recovered, assets, app='pro', tag=TAG, repository=REPO,
                                           run_url=RUN, bundle_name=bundle_name)
        self.assertEqual(result['resources'], 2)
        self.assertEqual(result['document']['bundle']['status'], 'verified')
        self.assertEqual(result['document']['bundle']['release_asset'], bundle_name)
        self.assertEqual(result['document']['bundle']['sha256'], 'b'*64)

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
