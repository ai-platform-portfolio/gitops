import unittest
from unittest.mock import Mock, patch

import onboard


class HealthGateTests(unittest.TestCase):
    def test_stale_argo_comparison_and_azure_generation_do_not_pass(self):
        app = {'spec': {'source': {'targetRevision': 'main', 'path': 'platform'}},
               'status': {'sync': {'status': 'Synced', 'comparedTo': {
                   'source': {'targetRevision': 'old', 'path': 'platform'}}},
                   'health': {'status': 'Healthy'}}}
        self.assertFalse(onboard.app_ready(app))
        app['status']['sync']['comparedTo']['source']['targetRevision'] = 'main'
        self.assertTrue(onboard.app_ready(app))

        resource = {'metadata': {'generation': 2}, 'status': {'conditions': [
            {'type': 'Ready', 'status': 'True', 'observedGeneration': 1}]}}
        self.assertFalse(onboard.ready(resource))
        resource['status']['conditions'][0]['observedGeneration'] = 2
        self.assertTrue(onboard.ready(resource))

    def test_registration_uses_verified_ca_and_does_not_enable_federation_early(self):
        hub = Mock()
        cluster = {'server': 'https://aks.example.test', 'certificate-authority-data': 'Y2E='}
        identity = {'identity': '{"client_id":"client","principal_id":"principal"}', 'tenant_id': 'tenant'}
        onboard.register(hub, 'aks-one', cluster, identity)
        bootstrap = hub.manifest.call_args.args[0]
        self.assertEqual(bootstrap['metadata']['labels']['platform.ai-platform-portfolio/stage'], 'bootstrap')
        self.assertNotIn('platform.ai-platform-portfolio/root', bootstrap['metadata']['annotations'])
        import json
        config = json.loads(bootstrap['stringData']['config'])
        self.assertFalse(config['tlsClientConfig']['insecure'])
        self.assertEqual(config['tlsClientConfig']['caData'], 'Y2E=')
        onboard.register(hub, 'aks-one', cluster, identity, 'ready', {'kind': 'Application'})
        self.assertEqual(hub.manifest.call_args.args[0]['metadata']['labels']['platform.ai-platform-portfolio/stage'], 'ready')

    @patch('onboard.socket.getaddrinfo')
    def test_workload_egress_uses_child_api_and_private_foundry(self, lookup):
        lookup.side_effect = lambda host, *_args, **_kwargs: [
            (2, 1, 6, '', ('20.1.2.3', 443))] if host == 'aks.example.test' else [
            (2, 1, 6, '', ('10.2.3.4', 443)), (2, 1, 6, '', ('52.1.2.3', 443))]
        original = {'holmes': {'enabled': True, 'modelEndpoint': 'https://foundry.example.test/',
                               'apiCidrs': ['1.1.1.1/32'], 'modelCidrs': [], 'entraCidrs': []},
                    'entraPrefixes': ['40.1.0.0/16']}
        result = onboard.workload_settings(original, {'server': 'https://aks.example.test'})
        self.assertEqual(result['holmes']['apiCidrs'], ['20.1.2.3/32'])
        self.assertEqual(result['holmes']['modelCidrs'], ['10.2.3.4/32'])
        self.assertEqual(result['holmes']['entraCidrs'], ['40.1.0.0/16'])
        self.assertEqual(original['holmes']['apiCidrs'], ['1.1.1.1/32'])


if __name__ == '__main__':
    unittest.main()
