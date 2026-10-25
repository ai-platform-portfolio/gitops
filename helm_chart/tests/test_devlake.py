"""Contract tests for production attribution and scoped credential reconciliation."""
from copy import deepcopy
import types
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[1] / 'platform/packages/devlake'


def load(name):
    # Load package scripts without writing bytecode into Helm's chart files.
    module = types.ModuleType(name)
    path = PACKAGE / (name + '.py')
    exec(compile(path.read_text(), str(path), 'exec'), module.__dict__)
    return module


configure = load('configure')
sys.modules['configure'] = configure
collector = load('collect')


class DeliveryContractTests(unittest.TestCase):
    def setUp(self):
        self.source = {'repoURL': 'https://github.com/example/gitops.git', 'helm': {'valuesObject': {
            'delivery': {'id': 'release-1', 'commit': 'a' * 40,
                         'repository': 'https://github.com/example/service.git'}}}}
        self.app = {'metadata': {'uid': 'cluster-unique-application'}, 'spec': {'source': self.source},
                    'status': {'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'},
                               'operationState': {'phase': 'Succeeded', 'startedAt': '2026-10-25T12:00:00Z',
                                                  'syncResult': {'source': deepcopy(self.source)}}}}
        self.settings = {'environment': 'PRODUCTION'}

    def event(self, app=None, settings=None):
        return collector.deployment('inference', settings or self.settings, app or self.app,
                                    'production-a', '2026-10-25T12:01:00Z')

    def test_actual_source_commit_and_explicit_environment(self):
        event = self.event(settings={'environment': 'DEVELOPMENT'})
        self.assertEqual(event['environment'], 'DEVELOPMENT')
        self.assertEqual(event['deploymentCommits'][0]['commitSha'], 'a' * 40)
        self.assertEqual(event['deploymentCommits'][0]['repoUrl'], 'https://github.com/example/service.git')

    def test_noop_sync_reuses_id_but_new_release_and_cluster_do_not(self):
        original = self.event()['id']
        self.app['status']['operationState']['startedAt'] = '2026-10-25T13:00:00Z'
        self.assertEqual(original, self.event()['id'])
        other = collector.deployment('inference', self.settings, self.app, 'production-b', '2026-10-25T13:00:00Z')
        self.assertNotEqual(original, other['id'])
        for source in [self.app['spec']['source'], self.app['status']['operationState']['syncResult']['source']]:
            source['helm']['valuesObject']['delivery']['id'] = 'rollback-release-2'
        self.assertNotEqual(original, self.event()['id'])

    def test_stale_healthy_status_and_incomplete_rollouts_are_excluded(self):
        self.app['spec']['source']['helm']['valuesObject']['delivery']['id'] = 'next-release'
        self.assertIsNone(self.event())
        self.app['spec']['source'] = deepcopy(self.app['status']['operationState']['syncResult']['source'])
        self.app['status']['health']['status'] = 'Progressing'
        self.assertIsNone(self.event())
        self.app['status']['health']['status'] = 'Healthy'
        self.app['status']['operationState']['phase'] = 'Failed'
        self.assertIsNone(self.event())

    def test_missing_or_chart_version_provenance_is_rejected(self):
        for source in [self.app['spec']['source'], self.app['status']['operationState']['syncResult']['source']]:
            source['helm']['valuesObject']['delivery']['commit'] = '2.1.4'
        with self.assertRaisesRegex(ValueError, 'actual application source'):
            self.event()

    def test_outbox_survives_failed_send_and_replays_original_payload(self):
        import json
        state = {'metadata': {'resourceVersion': '1'}}
        sent = []
        fail = [True]
        class FakePath:
            def __init__(self, *args): pass
            def __truediv__(self, name): return self
            def read_text(self): return 'test-token'
        def request(url, method='GET', body=None, headers=None, context=None):
            if url.endswith('/configmaps/devlake-configure'):
                return {'data': {'projects.json': json.dumps({'inference': {'application': 'inference-api', 'environment': 'PRODUCTION'}})}}
            if url.endswith('/applications/inference-api'):
                return deepcopy(self.app)
            if url.endswith('/configmaps/devlake-delivery-checkpoints'):
                if method == 'PUT':
                    state.clear()
                    state.update(deepcopy(body))
                    state['metadata']['resourceVersion'] = str(int(state['metadata']['resourceVersion']) + 1)
                return deepcopy(state)
            if '/rest/plugins/webhook/' in url:
                # The payload must already be durable before the first HTTP send.
                self.assertIn(body['id'], json.loads(state['data']['ledger'])['pending'])
                sent.append(deepcopy(body))
                if fail[0]:
                    raise RuntimeError('backend unavailable')
                return {}
            raise AssertionError(url)
        with patch.object(collector, 'Path', FakePath), patch.object(collector, 'request', request), \
             patch.object(collector.ssl, 'create_default_context', return_value=None), \
             patch.dict(collector.os.environ, {'CLUSTER_NAME': 'production-a'}):
            with self.assertRaisesRegex(RuntimeError, 'durable pending'):
                collector.collect()
            self.assertEqual(len(json.loads(state['data']['ledger'])['pending']), 1)
            fail[0] = False
            collector.collect()
            self.assertEqual(sent[0], sent[1])
            self.assertFalse(json.loads(state['data']['ledger'])['pending'])
            collector.collect()
            self.assertEqual(len(sent), 2)

    def test_ingestion_key_is_reused_and_scope_is_anchored(self):
        paths = []
        key = {'id': 1, 'name': 'gitops-service-events', 'allowedPath':
               '^/plugins/webhook/connections/by-name/(inference)/(deployments|issues)(/.*)?$'}
        def api(path, method='GET', body=None):
            paths.append((path, method))
            return {'apikeys': [key]}
        self.assertFalse(configure.reconcile_key(api, {'inference': {}}, {'data': {'token': 'existing'}}))
        self.assertEqual(paths, [('/api-keys?pageSize=1000', 'GET')])


if __name__ == '__main__':
    unittest.main()
