"""Credential and reuse boundaries for management-cluster event wiring."""
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import unittest

import yaml

CHART = Path(__file__).resolve().parents[1] / 'platform'
PUBLISHER = CHART / 'packages/atlantis/publish-signal.sh'


class EventTemplateTests(unittest.TestCase):
    def render(self, events):
        runtime = {key: 'example' for p in CHART.rglob('*') if p.is_file()
                   for key in re.findall(r'\.Values.runtime\.([A-Z_]+)', p.read_text())}
        runtime.update(DNS_ZONE='example.test', AZURE_KEY_VAULT_URI='https://example.vault.azure.net/')
        with tempfile.TemporaryDirectory() as tmp:
            values = Path(tmp) / 'values.json'
            values.write_text(json.dumps({'runtime': runtime, 'clusterOnboarding': {'enabled': False}, 'events': events}))
            return subprocess.run(['helm', 'template', 'platform', str(CHART), '-f', str(values)],
                                  text=True, capture_output=True)

    def test_second_consumer_uses_native_specs_without_onboarding(self):
        source = {'service': {'ports': [{'port': 12000, 'targetPort': 12000}]}, 'webhook': {
            'changed': {'port': '12000', 'endpoint': '/changed', 'method': 'POST',
                        'authSecret': {'name': 'externally-rotated-token', 'key': 'token'}}}}
        sensor = {'dependencies': [{'name': 'change', 'eventSourceName': 'catalog', 'eventName': 'changed'}],
                  'triggers': [{'template': {'name': 'notify', 'http': {'url': 'https://example.test', 'method': 'POST'}}}]}
        result = self.render({'sources': {'catalog': source}, 'sensors': {'catalog': sensor}})
        self.assertEqual(result.returncode, 0, result.stderr)
        docs = [d for d in yaml.safe_load_all(result.stdout) if d]
        actual = next(d['spec'] for d in docs if d['kind'] == 'EventSource')
        self.assertEqual(actual['webhook'], source['webhook'])
        self.assertEqual(actual['eventBusName'], 'platform')
        self.assertEqual(next(d['spec']['triggers'] for d in docs if d['kind'] == 'Sensor'), sensor['triggers'])
        apps = {d['metadata']['name'] for d in docs if d['kind'] == 'Application'}
        self.assertTrue({'argo-events', 'platform-events'} <= apps)
        self.assertNotIn('onboarding-webhook', result.stdout)

    def test_webhook_without_authentication_fails_render(self):
        result = self.render({'sources': {'unsafe': {'webhook': {'change': {
            'port': '12000', 'endpoint': '/change', 'method': 'POST'}}}}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires an explicit authSecret', result.stderr)


class SignalPublisherTests(unittest.TestCase):
    def test_token_is_reread_and_invalid_inputs_cannot_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            token = directory / 'token'
            log = directory / 'request.json'
            curl = directory / 'curl'
            curl.write_text('''#!/usr/bin/env python3
import json, os, pathlib, sys
args = sys.argv[1:]
payload = args[args.index('--data-binary') + 1][1:]
pathlib.Path(os.environ['SIGNAL_TEST_LOG']).write_text(json.dumps({
    'headers': sys.stdin.read(), 'args': args, 'payload': json.loads(pathlib.Path(payload).read_text())}))
sys.exit(int(os.environ.get('SIGNAL_TEST_STATUS', '0')))
''')
            curl.chmod(0o755)
            env = {**os.environ, 'PATH': str(directory) + os.pathsep + os.environ['PATH'],
                   'BASE_REPO_OWNER': 'example', 'BASE_REPO_NAME': 'clusters',
                   'SIGNAL_REPOSITORY': 'example/clusters', 'WORKSPACE': 'default',
                   'REPO_REL_DIR': 'clusters/aks-second', 'HEAD_COMMIT': 'a' * 40,
                   'SIGNAL_NAMESPACE': 'cluster-onboarding', 'SIGNAL_TOKEN_DIR': tmp,
                   'KUBERNETES_SERVICE_HOST': 'api.example.test', 'KUBERNETES_SERVICE_PORT_HTTPS': '443',
                   'SIGNAL_TEST_LOG': str(log)}
            requests = []
            for credential in ['first-test-token', 'rotated-test-token']:
                token.write_text(credential)
                result = subprocess.run(['sh', str(PUBLISHER)], env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                sent = json.loads(log.read_text())
                self.assertIn('Bearer ' + credential, sent['headers'])
                self.assertNotIn(credential, ' '.join(sent['args']) + result.stdout + result.stderr)
                self.assertIn('/configmaps/onboard-aks-second', sent['args'][-1])
                requests.append(sent['payload']['data']['request'])
            self.assertNotEqual(*requests)
            for override in [{'REPO_REL_DIR': 'clusters/../other'}, {'HEAD_COMMIT': '$(false)'},
                             {'REPO_REL_DIR': 'clusters/aks-second\nother'}, {'HEAD_COMMIT': 'a' * 40 + '\nother'},
                             {'WORKSPACE': 'other'}, {'BASE_REPO_OWNER': 'untrusted'}]:
                log.unlink()
                result = subprocess.run(['sh', str(PUBLISHER)], env={**env, **override}, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(log.exists())
                log.write_text('{}')
            result = subprocess.run(['sh', str(PUBLISHER)], env={**env, 'SIGNAL_TEST_STATUS': '22'}, capture_output=True)
            self.assertNotEqual(result.returncode, 0, 'API failure must fail the Atlantis step')


if __name__ == '__main__':
    unittest.main()
