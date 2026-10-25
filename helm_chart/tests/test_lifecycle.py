import base64
import json
import subprocess
import unittest
from unittest.mock import Mock, patch

from helm_chart.scripts.cluster import Cluster
from helm_chart.scripts.credentials import ensure_secret, prepare_sonarqube


def encoded(value):
    return base64.b64encode(value.encode()).decode()


class CredentialTests(unittest.TestCase):
    def test_repeated_up_reuses_the_existing_secret(self):
        cluster = Mock()
        cluster.object.return_value = {'type': 'Opaque', 'data': {'key': encoded('original')}}
        self.assertEqual(ensure_secret(cluster, 'langfuse', 'app', {'key': 'replacement'}), {'key': 'original'})
        cluster.manifest.assert_not_called()

    def test_missing_credentials_with_existing_data_are_not_regenerated(self):
        cluster = Mock()
        cluster.object.return_value = None
        cluster.objects.return_value = [{'metadata': {'name': 'existing-volume'}}]
        with self.assertRaisesRegex(RuntimeError, 'Credentials missing'):
            ensure_secret(cluster, 'langfuse', 'app', {'key': 'replacement'})
        cluster.manifest.assert_not_called()

    def test_first_install_creates_a_secret_in_kubernetes(self):
        cluster = Mock()
        cluster.object.return_value = None
        cluster.objects.return_value = []
        ensure_secret(cluster, 'postgres', 'sonarqube-role', {'username': 'sonarqube', 'password': 'password'},
                      'kubernetes.io/basic-auth', {'cnpg.io/reload': 'true'})
        doc = cluster.manifest.call_args.args[0]
        self.assertEqual(doc['type'], 'kubernetes.io/basic-auth')
        self.assertEqual(doc['data']['password'], encoded('password'))
        self.assertNotIn('stringData', doc)

    def test_incomplete_secret_is_rejected_without_exposing_its_contents(self):
        cluster = Mock()
        cluster.object.return_value = {'data': {'other': encoded('private-value')}}
        with self.assertRaises(ValueError) as error:
            ensure_secret(cluster, 'namespace', 'app', {'password': 'replacement'})
        self.assertNotIn('private-value', str(error.exception))
        cluster.manifest.assert_not_called()

    def test_sonar_tls_copy_preserves_the_existing_database_password(self):
        cluster = Mock()
        cluster.kube = ['kubectl']
        cluster.object.side_effect = [
            {'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}},
            {'data': {'password': encoded('existing-password')}},
            {'data': {'ca.crt': encoded('current-ca')}},
        ]
        run = Mock(return_value='clusters.postgresql.cnpg.io\n')
        wait = lambda check, description, timeout: self.assertTrue(check())
        prepare_sonarqube(cluster, run, wait)
        doc = cluster.manifest.call_args.args[0]
        self.assertEqual(doc['data'], {'jdbc-password': encoded('existing-password'), 'ca.crt': encoded('current-ca')})
        self.assertEqual(doc['metadata']['namespace'], 'sonarqube')

    def test_sonar_waits_for_transient_cnpg_api_denial(self):
        cluster = Mock()
        cluster.kube = ['kubectl']
        cluster.object.side_effect = [
            subprocess.CalledProcessError(1, ['kubectl', 'get', 'clusters.postgresql.cnpg.io']),
            {'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}},
            {'data': {'password': encoded('existing-password')}},
            {'data': {'ca.crt': encoded('current-ca')}},
        ]
        run = Mock(return_value='clusters.postgresql.cnpg.io\n')

        def wait(check, description, timeout):
            self.assertFalse(check())
            self.assertTrue(check())

        prepare_sonarqube(cluster, run, wait)
        self.assertEqual(cluster.manifest.call_args.args[0]['metadata']['name'], 'sonarqube-jdbc')


class PlatformInputTests(unittest.TestCase):
    def test_package_configuration_is_not_taken_from_terraform(self):
        cluster = Cluster.__new__(Cluster)
        cluster.settings = {'cluster': {'name': 'management', 'role': 'management'},
                            'git': {'repository': 'https://example.test/gitops', 'revision': 'main'},
                            'atlantis': {'enabled': True, 'github_app_id': 'legacy-output'}}
        values = cluster.root({}, False)['spec']['source']['helm']['valuesObject']
        self.assertNotIn('atlantis', values)


class TeardownTests(unittest.TestCase):
    def test_application_rollouts_block_controller_teardown(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        cluster.objects = Mock(side_effect=[
            [{'metadata': {'name': 'argo-rollouts'}}],
            [{'metadata': {'name': 'application', 'namespace': 'team'}}],
        ])
        cluster.delete = Mock()
        with patch('helm_chart.scripts.cluster.run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Remove application Rollouts'):
                cluster.cleanup()
        cluster.delete.assert_not_called()
        run.assert_not_called()
        self.assertEqual(cluster.objects.call_args.args, ('rollouts.argoproj.io',))

    def test_tracked_package_azure_resources_delete_before_operator(self):
        for package, extra in [(package, extra) for package in ["atlantis", "testkube"] for extra in [False, True]]:
            with self.subTest(package=package, untracked_resource=extra):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                def app(name, wave):
                    return {'metadata': {'name': name, 'labels': {'platform.ai-platform-portfolio/managed': 'true'},
                                         'annotations': {'argocd.argoproj.io/sync-wave': str(wave)}}}
                apps = [app('azure-service-operator', 10), app(package + '-azure', 20), app(package, 40)]
                resource = {'apiVersion': 'managedidentity.azure.com/v1api20230131', 'kind': 'UserAssignedIdentity',
                            'metadata': {'name': package, 'namespace': package}}
                apps[1]['status'] = {'resources': [{'group': 'managedidentity.azure.com', 'kind': 'UserAssignedIdentity',
                                                  'namespace': package, 'name': package}]}
                inventory = [resource]
                if extra:
                    inventory.append({**resource, 'metadata': {'name': 'untracked', 'namespace': 'atlantis'}})
                cluster.objects = Mock(side_effect=lambda kind, namespace=None: apps if kind == 'applications.argoproj.io' else inventory)
                cluster.delete = Mock()
                with patch('helm_chart.scripts.cluster.run', return_value='userassignedidentities.managedidentity.azure.com\n'):
                    if extra:
                        with self.assertRaisesRegex(RuntimeError, 'Remove ASO Azure resources'):
                            cluster.cleanup()
                        cluster.delete.assert_not_called()
                    else:
                        cluster.cleanup()
                        self.assertEqual([c.args[1][0] for c in cluster.delete.call_args_list],
                                         [package, package + '-azure', 'azure-service-operator', 'platform'])

    def test_azure_resources_block_teardown_before_operator_or_identity_removal(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        cluster.objects = Mock(side_effect=[
            [{'metadata': {'name': 'azure-service-operator'}}],
            [{'metadata': {'name': 'consumer-resource'}}],
        ])
        cluster.delete = Mock()
        with patch('helm_chart.scripts.cluster.run', return_value='resourcegroups.resources.azure.com\n') as run:
            with self.assertRaisesRegex(RuntimeError, 'Remove ASO Azure resources'):
                cluster.cleanup()
        cluster.delete.assert_not_called()
        self.assertEqual(run.call_count, 1)
        self.assertIn('customresourcedefinitions.apiextensions.k8s.io', run.call_args.args)
        self.assertIn('app.kubernetes.io/name=azure-service-operator', run.call_args.args)

    def test_native_aks_network_objects_do_not_block_teardown(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        app = {'metadata': {'name': 'azure-service-operator',
               'labels': {'platform.ai-platform-portfolio/managed': 'true'}}}
        def objects(kind, namespace=None):
            if kind == 'applications.argoproj.io':
                return [app]
            if kind == 'nodenetworkconfigs.acn.azure.com':
                return [{'metadata': {'name': 'aks-node-network'}}]
            return []
        def discovery(*args, **kwargs):
            if 'app.kubernetes.io/name=azure-service-operator' in args:
                return 'resourcegroups.resources.azure.com\n'
            return 'resourcegroups.resources.azure.com\nnodenetworkconfigs.acn.azure.com\n'
        cluster.objects = Mock(side_effect=objects)
        cluster.delete = Mock()
        with patch('helm_chart.scripts.cluster.run', side_effect=discovery):
            cluster.cleanup()
        self.assertFalse(any(c.args[0] == 'nodenetworkconfigs.acn.azure.com' for c in cluster.objects.call_args_list))
        self.assertEqual(cluster.delete.call_args_list[0].args[1], ['azure-service-operator'])

    def test_empty_azure_inventory_allows_teardown_and_read_errors_fail_closed(self):
        for inventory_error in [False, True]:
            with self.subTest(inventory_error=inventory_error):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                app = {'metadata': {'name': 'azure-service-operator',
                       'labels': {'platform.ai-platform-portfolio/managed': 'true'}}}
                def objects(kind, namespace=None):
                    if kind == 'applications.argoproj.io':
                        return [app]
                    if inventory_error:
                        raise subprocess.CalledProcessError(1, ['kubectl', 'get'])
                    return []
                cluster.objects = Mock(side_effect=objects)
                cluster.delete = Mock()
                with patch('helm_chart.scripts.cluster.run', return_value='resourcegroups.resources.azure.com\n'):
                    if inventory_error:
                        with self.assertRaises(subprocess.CalledProcessError):
                            cluster.cleanup()
                        cluster.delete.assert_not_called()
                    else:
                        cluster.cleanup()
                        self.assertEqual(cluster.delete.call_args_list[0].args[1], ['azure-service-operator'])

    def test_stuck_root_and_children_are_stopped_before_deletion(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        root = {'metadata': {'name': 'platform', 'resourceVersion': '1'},
                'operation': {'sync': {}},
                'status': {'operationState': {'phase': 'Running'}}}
        child = {'metadata': {'name': 'postgres', 'resourceVersion': '2',
                 'labels': {'platform.ai-platform-portfolio/managed': 'true'}},
                 'operation': {'sync': {}}}
        apps = [root]
        cluster.objects = Mock(side_effect=lambda *args: list(apps))
        transitions = []

        def patch_application(*args):
            self.assertEqual(args[4], 'applications.argoproj.io')
            app = next(a for a in apps if a['metadata']['name'] == args[5])
            change = json.loads(args[-1])
            if 'spec' in change:
                return
            self.assertEqual(change['metadata']['resourceVersion'], app['metadata']['resourceVersion'])
            if 'status' in change:
                self.assertEqual(change['status']['operationState']['phase'], 'Terminating')
                apps.append(child)
                app['status']['operationState']['phase'] = 'Failed'
            else:
                self.assertIsNone(change['operation'])
            app.pop('operation')
            transitions.append(app['metadata']['name'])

        def delete(*args):
            self.assertEqual(transitions, ['platform', 'postgres'])
            self.assertTrue(all(not a.get('operation') for a in apps))

        cluster.delete = Mock(side_effect=delete)
        with patch('helm_chart.scripts.cluster.run', side_effect=patch_application), patch('helm_chart.scripts.cluster.time.sleep'):
            cluster.cleanup()
        self.assertEqual([c.args[1] for c in cluster.delete.call_args_list], [['postgres'], ['platform']])

    def test_children_created_by_an_active_root_are_included_in_cleanup(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        root = {'metadata': {'name': 'platform'}}
        child = lambda name, wave: {'metadata': {'name': name,
            'labels': {'platform.ai-platform-portfolio/managed': 'true'},
            'annotations': {'argocd.argoproj.io/sync-wave': str(wave)}}}
        apps = [root]
        cluster.objects = Mock(side_effect=lambda *args: list(apps))
        cluster.delete = Mock()
        def patch_root(*args, **kwargs):
            if 'patch' in args and 'platform' in args and len(apps) == 1:
                apps.extend([child('sonarqube', 60), child('postgres', 30), child('cloudnative-pg', 10)])
        with patch('helm_chart.scripts.cluster.run', side_effect=patch_root):
            cluster.cleanup()
        deleted = [call.args[1][0] for call in cluster.delete.call_args_list]
        self.assertEqual(deleted, ['sonarqube', 'postgres', 'cloudnative-pg', 'platform'])

    def test_hub_lookup_failure_does_not_allow_teardown(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = ['kubectl', '--kubeconfig', 'hub']
        cluster.settings = {'cluster': {'role': 'workload', 'name': 'dev'}}
        cluster.delete = Mock()
        with patch('helm_chart.scripts.cluster.run', side_effect=[None, subprocess.CalledProcessError(1, ['kubectl'])]):
            with self.assertRaises(subprocess.CalledProcessError):
                cluster.cleanup()
        cluster.delete.assert_not_called()

    def test_only_confirmed_not_found_is_treated_as_absence(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        with patch('helm_chart.scripts.cluster.run', return_value='') as run:
            self.assertIsNone(cluster.object('application', 'app', 'argocd'))
        self.assertIn('--ignore-not-found', run.call_args.args)


class AzureAccountTests(unittest.TestCase):
    def test_oidc_issuer_lookup_captures_private_diagnostics(self):
        cluster = Cluster.__new__(Cluster)
        cluster.connection = {'subscription_id': 'subscription', 'resource_group': 'group', 'name': 'cluster'}
        for code, output in [(0, 'https://issuer.example.test/'), (1, 'private-error'), (0, '')]:
            with patch('helm_chart.scripts.cluster.subprocess.run', return_value=Mock(returncode=code, stdout=output)) as run:
                if code == 0 and output:
                    self.assertEqual(cluster.oidc_issuer(), output)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'Cannot read the AKS OIDC issuer'):
                        cluster.oidc_issuer()
                self.assertTrue(run.call_args.kwargs['capture_output'])


    def test_reads_authenticated_cli_context(self):
        from helm_chart.scripts.azure import current_account
        with patch('helm_chart.scripts.azure.subprocess.run', return_value=Mock(
                returncode=0, stdout='{"id":"subscription","tenantId":"tenant"}')):
            self.assertEqual(current_account(), {'subscription_id': 'subscription', 'tenant_id': 'tenant'})

    def test_missing_login_fails_without_printing_cli_diagnostics(self):
        from helm_chart.scripts.azure import current_account
        with patch('helm_chart.scripts.azure.subprocess.run', return_value=Mock(returncode=1, stderr='private details')):
            with self.assertRaisesRegex(RuntimeError, 'authenticate') as error:
                current_account()
            self.assertNotIn('private details', str(error.exception))

    def test_incomplete_account_is_rejected(self):
        from helm_chart.scripts.azure import current_account
        with patch('helm_chart.scripts.azure.subprocess.run', return_value=Mock(returncode=0, stdout='{"id":"subscription"}')):
            with self.assertRaises(ValueError):
                current_account()


class ArgoIngressTests(unittest.TestCase):
    def test_dns_preflight_rejects_unowned_records_and_accepts_only_matching_owner(self):
        cluster = Cluster.__new__(Cluster)
        cluster.account = {'subscription_id': 'subscription'}
        cluster.settings = {'cluster': {'name': 'management'}}
        runtime = {'ARGOCD_HOSTNAME': 'argocd-management.example.test', 'AZURE_DNS_ZONE': 'example.test',
                   'AZURE_DNS_RESOURCE_GROUP': 'dns'}
        address = {'name': 'argocd-management', 'type': 'Microsoft.Network/dnszones/A'}
        for records, allowed in [([], True), ([{'name': '*', 'type': address['type']}], True),
                                 ([address], False)]:
            with patch('helm_chart.scripts.cluster.subprocess.run', return_value=Mock(returncode=0, stdout=json.dumps(records))):
                if allowed:
                    cluster.check_dns(runtime)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'DNS clash'):
                        cluster.check_dns(runtime)
        for owner, allowed in [('management', True), ('another-cluster', False)]:
            txt = {'name': 'external-dns-a-argocd-management', 'type': 'Microsoft.Network/dnszones/TXT',
                   'TXTRecords': [{'value': [f'heritage=external-dns,external-dns/owner={owner},',
                                            'external-dns/resource=httproute/argocd/argocd']}]}
            with patch('helm_chart.scripts.cluster.subprocess.run', return_value=Mock(returncode=0, stdout=json.dumps([address, txt]))):
                if allowed:
                    cluster.check_dns(runtime)
                else:
                    with self.assertRaisesRegex(RuntimeError, 'DNS clash'):
                        cluster.check_dns(runtime)
        with patch('helm_chart.scripts.cluster.subprocess.run', return_value=Mock(returncode=1)):
            with self.assertRaisesRegex(RuntimeError, 'Cannot check'):
                cluster.check_dns(runtime)


    def test_https_wait_checks_current_certificate_gateway_dns_and_verified_request(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        certificate = {'metadata': {'generation': 2}, 'status': {'conditions': [
            {'type': 'Ready', 'status': 'True', 'observedGeneration': 2}]}}
        gateway = {'status': {'addresses': [{'type': 'IPAddress', 'value': '192.0.2.10'}]}}
        cluster.object = Mock(side_effect=lambda kind, *args: certificate if kind.startswith('certificates') else gateway)
        response = Mock(status=200)
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        def wait(check, description):
            self.assertTrue(check())
            certificate['status']['conditions'][0]['observedGeneration'] = 1
            self.assertFalse(check())
            certificate['status']['conditions'][0]['observedGeneration'] = 2
            gateway['status']['addresses'][0]['value'] = '192.0.2.20'
            self.assertFalse(check())
        with patch('helm_chart.scripts.cluster.run', return_value='certificates.cert-manager.io\ngateways.gateway.networking.k8s.io\n'), \
             patch('helm_chart.scripts.cluster.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('192.0.2.10', 443))]), \
             patch('helm_chart.scripts.cluster.urllib.request.urlopen', return_value=response) as request, \
             patch('helm_chart.scripts.cluster.wait_for', side_effect=wait):
            cluster.ingress_ready({'ARGOCD_HOSTNAME': 'argocd.example.test'})
        request.assert_called_once_with('https://argocd.example.test/healthz', timeout=10)


if __name__ == '__main__':
    unittest.main()
