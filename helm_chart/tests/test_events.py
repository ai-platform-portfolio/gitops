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
    def render(self, events, **settings):
        runtime = {key: 'example' for p in CHART.rglob('*') if p.is_file()
                   for key in re.findall(r'\.Values.runtime\.([A-Z_]+)', p.read_text())}
        runtime.update(DNS_ZONE='example.test', AZURE_KEY_VAULT_URI='https://example.vault.azure.net/')
        with tempfile.TemporaryDirectory() as tmp:
            values = Path(tmp) / 'values.json'
            values.write_text(json.dumps({'runtime': runtime, 'clusterOnboarding': {'enabled': False}, 'events': events, **settings}))
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

    def test_repository_discovery_restores_intent_with_one_pinned_owner(self):
        result = self.render({'github': {'organization': 'second-org'}})
        self.assertEqual(result.returncode, 0, result.stderr)
        sets = [d for d in yaml.safe_load_all(result.stdout) if d and d['kind'] == 'ApplicationSet']
        self.assertEqual(len(sets), 2)
        for appset, profile in zip(sorted(sets, key=lambda d: d['metadata']['name']), ['environments', 'previews']):
            generator = appset['spec']['generators'][0]['scmProvider']
            self.assertEqual(generator['requeueAfterSeconds'], 0)
            self.assertEqual(generator['github']['organization'], 'second-org')
            self.assertFalse(generator['github']['allBranches'])
            self.assertEqual(generator['filters'], [{'pathsExist': [f'deploy/{profile}.yaml']}])
            template = appset['spec']['template']
            self.assertEqual(template['spec']['sources'][0]['helm']['valueFiles'], [f'$intent/deploy/{profile}.yaml'])
            pin = template['spec']['sources'][0]['targetRevision']
            self.assertRegex(pin, r'^[0-9a-f]{40}$')
            self.assertEqual(template['spec']['sources'][1]['targetRevision'], '{{.sha}}')
            self.assertEqual(template['spec']['destination']['namespace'], 'argocd')
            # Evaluate the actual nested Go/Sprig template, not a Python imitation.
            with tempfile.TemporaryDirectory() as tmp:
                chart = Path(tmp)
                (chart / 'templates').mkdir()
                (chart / 'Chart.yaml').write_text('apiVersion: v2\nname: generated\nversion: 0.1.0\n')
                (chart / 'templates/application.yaml').write_text('{{ tpl (toYaml .Values.template) .Values.parameters }}')
                names = []
                for organization, sha in [('first-org', 'a' * 40), ('second-org', 'a' * 40), ('first-org', 'b' * 40)]:
                    values = chart / 'values.json'
                    values.write_text(json.dumps({'template': template, 'parameters': {
                        'organization': organization, 'repository': 'Same_App',
                        'url': f'https://github.com/{organization}/Same_App.git', 'sha': sha}}))
                    generated = subprocess.run(['helm', 'template', 'generated', str(chart), '-f', str(values)],
                                               text=True, capture_output=True)
                    self.assertEqual(generated.returncode, 0, generated.stderr)
                    application = yaml.safe_load(generated.stdout)
                    names.append(application['metadata']['name'])
                    source = application['spec']['sources'][0]
                    self.assertEqual(source['helm']['valuesObject']['platform']['argocd']['chartRevision'], pin)
                    self.assertEqual(application['spec']['sources'][1]['targetRevision'], sha)
                self.assertNotEqual(names[0], names[1])
                self.assertEqual(names[0], names[2])

    def test_workload_cluster_cannot_register_organization_applications(self):
        result = self.render({}, cluster={'role': 'workload'})
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn('kind: ApplicationSet', result.stdout)

    def test_discovery_cannot_silently_fall_back_to_polling_without_events(self):
        result = self.render({'github': {'enabled': False}})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requires GitHub events', result.stderr)


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
payload = json.loads(args[args.index('--data-binary') + 1])
pathlib.Path(os.environ['SIGNAL_TEST_LOG']).write_text(json.dumps({
    'headers': sys.stdin.read(), 'args': args, 'payload': payload}))
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
                result = subprocess.run(['bash', str(PUBLISHER)], env=env, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                sent = json.loads(log.read_text())
                self.assertIn('Bearer ' + credential, sent['headers'])
                self.assertNotIn(credential, ' '.join(sent['args']) + result.stdout + result.stderr)
                self.assertIn('/configmaps/onboard-aks-second', sent['args'][-1])
                self.assertEqual(set(sent['payload']['data']), {'revision', 'request'})
                requests.append(sent['payload']['data']['request'])
            self.assertNotEqual(*requests)
            for override in [{'REPO_REL_DIR': 'clusters/../other'}, {'HEAD_COMMIT': '$(false)'},
                             {'REPO_REL_DIR': 'clusters/aks-second\nother'}, {'HEAD_COMMIT': 'a' * 40 + '\nother'},
                             {'WORKSPACE': 'other'}, {'BASE_REPO_OWNER': 'untrusted'}]:
                log.unlink()
                result = subprocess.run(['bash', str(PUBLISHER)], env={**env, **override}, capture_output=True)
                self.assertNotEqual(result.returncode, 0)
                self.assertFalse(log.exists())
                log.write_text('{}')
            result = subprocess.run(['bash', str(PUBLISHER)], env={**env, 'SIGNAL_TEST_STATUS': '22'}, capture_output=True)
            self.assertNotEqual(result.returncode, 0, 'API failure must fail the Atlantis step')


if __name__ == '__main__':
    unittest.main()
