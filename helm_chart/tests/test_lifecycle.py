import base64
import json
import subprocess
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from helm_chart.scripts.cluster import Cluster, wait_for, owned_dns, cleanup_stale_dns, already_down, main, teardown_connection
from helm_chart.scripts.credentials import ensure_secret, prepare_sonarqube
from bootstrap.scripts import wait_for_aks as aks_readiness


def encoded(value):
    return base64.b64encode(value.encode()).decode()


class AKSReadinessTests(unittest.TestCase):
    def setUp(self):
        self.environment = {'AKS_NAME': 'test-cluster', 'AKS_RESOURCE_GROUP': 'test-group'}

    def commands(self, answers, healthy=True):
        answers = iter(answers)

        def command(*args):
            if 'can-i' in args:
                return Mock(returncode=0, stdout=next(answers))
            if 'get' in args:
                return Mock(returncode=0 if healthy else 1, stdout='ok' if healthy else 'unavailable')
            return Mock(returncode=0, stdout='')
        return command

    def test_healthy_api_alone_does_not_authorize_helm(self):
        with patch.dict('os.environ', self.environment), \
             patch.object(aks_readiness, 'TIMEOUT_SECONDS', 1), \
             patch.object(aks_readiness.time, 'monotonic', side_effect=[0, 0, 0, 0, 2]), \
             patch.object(aks_readiness.time, 'sleep'), \
             patch.object(aks_readiness, 'run', side_effect=self.commands(['no'] * 3)) as run:
            with self.assertRaisesRegex(SystemExit, 'bootstrap permissions denied'):
                aks_readiness.main()
        self.assertEqual(sum('can-i' in c.args for c in run.call_args_list), 3)

    def test_three_consecutive_authorized_checks_are_required(self):
        with patch.dict('os.environ', self.environment), \
             patch.object(aks_readiness.time, 'monotonic', return_value=0), \
             patch.object(aks_readiness.time, 'sleep') as sleep, \
             patch.object(aks_readiness, 'run', side_effect=self.commands(['yes'] * 3)) as run:
            aks_readiness.main()
        checks = [c.args for c in run.call_args_list if 'can-i' in c.args]
        self.assertEqual(len(checks), 3)
        self.assertTrue(all(c[-5:] == ('auth', 'can-i', '*', '*', '--all-namespaces') for c in checks))
        self.assertEqual(sleep.call_count, 2)

    def test_permission_denial_resets_consecutive_successes(self):
        with patch.dict('os.environ', self.environment), \
             patch.object(aks_readiness.time, 'monotonic', return_value=0), \
             patch.object(aks_readiness.time, 'sleep') as sleep, \
             patch.object(aks_readiness, 'run', side_effect=self.commands(['yes', 'no', 'yes', 'yes', 'yes'])):
            aks_readiness.main()
        self.assertEqual(sleep.call_count, 4)

    def test_unhealthy_api_does_not_attempt_permission_check(self):
        with patch.dict('os.environ', self.environment), \
             patch.object(aks_readiness, 'TIMEOUT_SECONDS', 1), \
             patch.object(aks_readiness.time, 'monotonic', side_effect=[0, 0, 2]), \
             patch.object(aks_readiness.time, 'sleep'), \
             patch.object(aks_readiness, 'run', side_effect=self.commands([], healthy=False)) as run:
            with self.assertRaisesRegex(SystemExit, 'API not ready'):
                aks_readiness.main()
        self.assertFalse(any('can-i' in c.args for c in run.call_args_list))

    def test_command_timeouts_are_bounded_and_credentials_not_reported(self):
        error = subprocess.TimeoutExpired(['credential-command'], 30, output='private-token')
        with patch.dict('os.environ', self.environment), \
             patch.object(aks_readiness, 'TIMEOUT_SECONDS', 1), \
             patch.object(aks_readiness.time, 'monotonic', side_effect=[0, 0, 2]), \
             patch.object(aks_readiness.time, 'sleep'), \
             patch.object(aks_readiness, 'run', side_effect=error):
            with self.assertRaises(SystemExit) as failure:
                aks_readiness.main()
        self.assertNotIn('private-token', str(failure.exception))


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

    def test_sonar_wait_resumes_root_when_new_revision_is_compared_after_seed(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']

        def root(compared, phase='Running'):
            return {'status': {'sync': {'revision': compared}, 'operationState': {
                'phase': phase, 'syncResult': {'revision': 'old'}}}}

        cluster.object = Mock(side_effect=[
            root('old'), None,  # First check: new revision not yet compared; no database.
            root('new'), root('new', 'Failed'),  # Later comparison permits termination.
            {'status': {'conditions': [{'type': 'Ready', 'status': 'True'}]}},
            {'data': {'password': encoded('existing-password')}},
            {'data': {'ca.crt': encoded('current-ca')}},
        ])
        cluster.manifest = Mock()
        discovery = Mock(return_value='clusters.postgresql.cnpg.io\n')

        def wait(check, description, timeout):
            self.assertFalse(check())
            cluster.manifest.assert_not_called()
            self.assertTrue(check())

        with patch('helm_chart.scripts.cluster.run') as run:
            prepare_sonarqube(cluster, discovery, wait)
        run.assert_called_once()
        self.assertEqual(json.loads(run.call_args.args[-1]),
                         {'status': {'operationState': {'phase': 'Terminating'}}})
        self.assertEqual(cluster.manifest.call_args.args[0]['data'], {
            'jdbc-password': encoded('existing-password'), 'ca.crt': encoded('current-ca')})

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


class HealthWaitTests(unittest.TestCase):
    def test_temporary_kubernetes_command_errors_do_not_abort_health_wait(self):
        error = subprocess.CalledProcessError(1, ['kubectl', 'get', 'applications.argoproj.io'])
        check = Mock(side_effect=[error, error, False, True])
        with patch('helm_chart.scripts.cluster.time.sleep') as sleep:
            wait_for(check, 'Waiting for Argo', timeout=30, retry_command_errors=True)
        self.assertEqual(check.call_count, 4)
        self.assertEqual(sleep.call_count, 3)

    def test_repeated_command_errors_are_reported(self):
        error = subprocess.CalledProcessError(1, ['kubectl', 'get', 'applications.argoproj.io'])
        check = Mock(side_effect=error)
        with patch('helm_chart.scripts.cluster.time.sleep') as sleep:
            with self.assertRaises(subprocess.CalledProcessError):
                wait_for(check, 'Waiting for Argo', retry_command_errors=True)
        self.assertEqual(check.call_count, 6)
        self.assertEqual(sleep.call_count, 5)


class PlatformInputTests(unittest.TestCase):
    def test_package_configuration_is_not_taken_from_terraform(self):
        cluster = Cluster.__new__(Cluster)
        cluster.settings = {'cluster': {'name': 'management', 'role': 'management'},
                            'git': {'repository': 'https://example.test/gitops', 'revision': 'main'},
                            'atlantis': {'enabled': True, 'github_app_id': 'legacy-output'}}
        values = cluster.root({}, False)['spec']['source']['helm']['valuesObject']
        self.assertNotIn('atlantis', values)

    def test_stale_root_operation_is_terminated_after_new_revision_is_seen(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.object = Mock(side_effect=[
            {'status': {'sync': {'revision': 'new'}, 'operationState': {
                'phase': 'Running', 'syncResult': {'revision': 'old'}}}},
            {'status': {'sync': {'revision': 'new'}, 'operationState': {'phase': 'Terminated',
                'syncResult': {'revision': 'old'}}}},
        ])
        with patch('helm_chart.scripts.cluster.run') as run:
            cluster.resume_stale_root_sync()
        command = run.call_args.args
        self.assertEqual(command[:6], ('kubectl', '-n', 'argocd', 'patch', 'applications.argoproj.io', 'platform'))
        self.assertEqual(json.loads(command[-1]), {'status': {'operationState': {'phase': 'Terminating'}}})

    def test_current_root_operation_is_left_running(self):
        cluster = Cluster.__new__(Cluster)
        cluster.object = Mock(return_value={'status': {'sync': {'revision': 'same'},
            'operationState': {'phase': 'Running', 'syncResult': {'revision': 'same'}}}})
        with patch('helm_chart.scripts.cluster.run') as run:
            cluster.resume_stale_root_sync()
        run.assert_not_called()

    def test_health_wait_ends_old_operation_after_later_revision_comparison(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.settings = {'cluster': {'role': 'management'}}
        resources = [{'kind': 'Application', 'name': name}
                     for name in ('sonarqube', 'sonarqube-configure', 'argocd-route')]
        def root(compared, active, healthy=False):
            return {'metadata': {'name': 'platform'}, 'spec': {}, 'status': {
                'resources': resources, 'sync': {'revision': compared,
                    'status': 'Synced' if healthy else 'OutOfSync'},
                'health': {'status': 'Healthy' if healthy else 'Progressing'},
                'operationState': {'phase': 'Succeeded' if healthy else 'Running',
                    'syncResult': {'revision': active}}}}
        children = [{'metadata': {'name': name}, 'spec': {}, 'status': {
            'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'}}}
            for name in ('sonarqube', 'sonarqube-configure', 'argocd-route')]
        cluster.objects = Mock(side_effect=[
            [root('old', 'old'), *children],
            [root('new', 'old'), *children],
            [root('new', 'new', healthy=True), *children],
        ])
        cluster.object = Mock(return_value={'status': {'operationState': {'phase': 'Terminated',
            'syncResult': {'revision': 'old'}}}})
        with patch('helm_chart.scripts.cluster.run') as run, patch('helm_chart.scripts.cluster.time.sleep'):
            cluster.ready()
        self.assertEqual(cluster.objects.call_count, 3)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(json.loads(run.call_args.args[-1]),
                         {'status': {'operationState': {'phase': 'Terminating'}}})

    def test_startup_waits_for_discovery_and_registration_but_not_failed_prs(self):
        cluster = Cluster.__new__(Cluster)
        cluster.settings = {'cluster': {'role': 'management'}}
        cluster.resume_stale_root_sync = Mock()
        names = ['platform', 'sonarqube', 'sonarqube-configure', 'argocd-route']
        apps = [{'metadata': {'name': name}, 'spec': {}, 'status': {
            'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'}}} for name in names]
        apps[0]['status']['resources'] = [{'kind': 'Application', 'name': n} for n in names[1:]] + [
            {'kind': 'ApplicationSet', 'name': 'application-previews'}]
        registration = {'metadata': {'name': 'example-previews-registration', 'labels': {
            'platform.ai-platform-portfolio/registration': 'true'}}, 'spec': {}, 'status': {
                'sync': {'status': 'Synced'}, 'health': {'status': 'Healthy'}}}
        failed_pr = {'metadata': {'name': 'example-pr-20'}, 'spec': {}, 'status': {
            'sync': {'status': 'OutOfSync'}, 'health': {'status': 'Degraded'}}}
        ready_set = {'metadata': {'name': 'application-previews'}, 'status': {'conditions': [
            {'type': kind, 'status': state} for kind, state in [
                ('ParametersGenerated', 'True'), ('ResourcesUpToDate', 'True'), ('ErrorOccurred', 'False')]]}}
        pending = {**registration, 'status': {'sync': {'status': 'OutOfSync'}}}
        cluster.objects = Mock(side_effect=[
            [*apps, failed_pr], [],
            [*apps, pending, failed_pr], [ready_set],
            [*apps, registration, failed_pr], [ready_set]])
        with patch('helm_chart.scripts.cluster.time.sleep'):
            cluster.ready()
        self.assertEqual(cluster.objects.call_count, 6)


class TeardownTests(unittest.TestCase):
    @staticmethod
    def onboarding(name='repository-discovery'):
        root = {'metadata': {'name': 'platform'}, 'status': {'resources': [
            {'group': 'argoproj.io', 'kind': 'ApplicationSet', 'namespace': 'argocd', 'name': name}]}}
        appset = {'metadata': {'name': name, 'uid': 'discovery-uid',
                  'labels': {'platform.ai-platform-portfolio/onboarding': 'true'},
                  'annotations': {'argocd.argoproj.io/tracking-id':
                                  f'platform:argoproj.io/ApplicationSet:argocd/{name}'}},
                  'spec': {'syncPolicy': {'preserveResourcesOnDeletion': False},
                           'template': {'metadata': {'finalizers': ['resources-finalizer.argocd.argoproj.io']}}}}
        return root, appset

    @staticmethod
    def tracked_platform(apps):
        root = {'metadata': {'name': 'platform'}, 'status': {'resources': []}}
        for app in apps:
            name = app['metadata']['name']
            app['metadata'].setdefault('annotations', {})['argocd.argoproj.io/tracking-id'] = (
                f'platform:argoproj.io/Application:argocd/{name}')
            root['status']['resources'].append(
                {'group': 'argoproj.io', 'kind': 'Application', 'namespace': 'argocd', 'name': name})
        return [root, *apps]

    def test_root_sensors_drain_before_event_bus_and_controller(self):
        for state in ['active', 'deleting', 'absent', 'api-absent']:
            with self.subTest(state=state):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                apps = self.tracked_platform([
                    {'metadata': {'name': name, 'labels': {'platform.ai-platform-portfolio/managed': 'true'},
                                  'annotations': {'argocd.argoproj.io/sync-wave': wave}}}
                    for name, wave in [('platform-events', '20'), ('argo-events', '0')]])
                refs = [('events-a', 'onboard-alpha'), ('events-b', 'onboard-beta')]
                apps[0]['status']['resources'] += [
                    {'group': 'argoproj.io', 'kind': 'Sensor', 'namespace': ns, 'name': name} for ns, name in refs]
                events = []

                def sensor(kind, name, namespace):
                    self.assertEqual(kind, 'sensors.argoproj.io')
                    self.assertIn((namespace, name), refs)
                    if state == 'absent':
                        return None
                    metadata = {'annotations': {'argocd.argoproj.io/tracking-id':
                                                f'platform:argoproj.io/Sensor:{namespace}/{name}'}}
                    if state == 'deleting':
                        metadata['deletionTimestamp'] = 'now'
                    return {'metadata': metadata}

                def command(*args, **kwargs):
                    if 'patch' in args and 'platform' in args:
                        events.append('stop-root')
                    return '' if state == 'api-absent' else 'sensors.argoproj.io'

                cluster.object = Mock(side_effect=sensor)
                cluster.objects = Mock(return_value=apps)
                cluster.delete = Mock(side_effect=lambda kind, names, namespace=None: events.append((kind, names, namespace)))
                with patch('helm_chart.scripts.cluster.run', side_effect=command):
                    cluster.cleanup()
                self.assertEqual(events[0], 'stop-root')
                deletions = events[1:]
                if state in ['active', 'deleting']:
                    self.assertEqual(deletions[:2], [('sensors.argoproj.io', [name], ns) for ns, name in refs])
                    deletions = deletions[2:]
                else:
                    self.assertFalse(any(c.args[0] == 'sensors.argoproj.io' for c in cluster.delete.call_args_list))
                self.assertEqual([d[1] for d in deletions], [['platform-events'], ['argo-events'], ['platform']])
                if state == 'api-absent':
                    cluster.object.assert_not_called()

    def test_root_sensor_failures_preserve_platform_dependencies(self):
        for fault in ['tracking', 'read-error', 'namespace', 'timeout']:
            with self.subTest(fault=fault):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                root = {'metadata': {'name': 'platform'}, 'status': {'resources': [
                    {'group': 'argoproj.io', 'kind': 'Sensor', 'namespace': 'events', 'name': 'onboard-alpha'}]}}
                cluster.objects = Mock(return_value=[root])
                cluster.object = Mock(return_value={'metadata': {'annotations': {
                    'argocd.argoproj.io/tracking-id': 'platform:argoproj.io/Sensor:events/onboard-alpha'}}})
                cluster.delete = Mock()
                if fault == 'tracking':
                    cluster.object.return_value['metadata']['annotations'].clear()
                elif fault == 'read-error':
                    cluster.object.side_effect = subprocess.CalledProcessError(1, ['kubectl', 'get'])
                elif fault == 'namespace':
                    root['status']['resources'][0].pop('namespace')
                else:
                    cluster.delete.side_effect = TimeoutError('Waiting for Sensor finalizer')
                error = {'read-error': subprocess.CalledProcessError, 'timeout': TimeoutError}.get(fault, RuntimeError)
                with patch('helm_chart.scripts.cluster.run', return_value='sensors.argoproj.io'), self.assertRaises(error):
                    cluster.cleanup()
                if fault == 'timeout':
                    self.assertEqual(cluster.delete.call_count, 1)
                    self.assertEqual(cluster.delete.call_args.args, ('sensors.argoproj.io', ['onboard-alpha'], 'events'))
                else:
                    cluster.delete.assert_not_called()

    def test_dns_records_drain_after_routes_and_before_controller_deletion(self):
        for timeout in [False, True]:
            with self.subTest(timeout=timeout):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management', 'name': 'example'},
                                    'runtime': {'AZURE_DNS_ZONE': 'example.test', 'AZURE_DNS_RESOURCE_GROUP': 'dns'}}
                cluster.account = {'subscription_id': 'subscription'}
                apps = self.tracked_platform([
                    {'metadata': {'name': name, 'labels': {'platform.ai-platform-portfolio/managed': 'true'},
                                  'annotations': {'argocd.argoproj.io/sync-wave': wave}}}
                    for name, wave in [('external-dns', '15'), ('platform-events', '20')]])
                deleted = []
                cluster.objects = Mock(return_value=apps)
                cluster.delete = Mock(side_effect=lambda kind, names, namespace=None: deleted.extend(names))
                observations = iter([[('txt', 'a')], []])

                def registry(*args):
                    self.assertEqual(deleted, ['platform-events'])
                    return next(observations)

                def wait(check, description, **kwargs):
                    if 'ExternalDNS' in description:
                        self.assertFalse(check())
                        if timeout:
                            raise TimeoutError(description)
                    self.assertTrue(check())

                with patch('helm_chart.scripts.cluster.run', return_value=''), \
                     patch('helm_chart.scripts.cluster.owned_dns', side_effect=registry), \
                     patch('helm_chart.scripts.cluster.wait_for', side_effect=wait):
                    if timeout:
                        with self.assertRaises(TimeoutError):
                            cluster.cleanup()
                        self.assertEqual(deleted, ['platform-events'])
                    else:
                        cluster.cleanup()
                        self.assertEqual(deleted, ['platform-events', 'external-dns', 'platform'])

    def test_foreground_delete_waits_until_finalizers_finish(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        _, appset = self.onboarding()
        cluster.objects = Mock(side_effect=[[appset], [appset], []])
        with patch('helm_chart.scripts.cluster.run', return_value='applicationsets.argoproj.io') as run, \
             patch('helm_chart.scripts.cluster.time.sleep') as sleep:
            cluster.delete('applicationsets.argoproj.io', ['repository-discovery'], 'argocd', foreground=True)
        delete = next(c.args for c in run.call_args_list if 'delete' in c.args)
        self.assertIn('--cascade=foreground', delete)
        self.assertNotIn('patch', delete)
        sleep.assert_called_once()

    def test_registration_drain_precedes_guards_and_leaves_other_sets_untouched(self):
        for already_drained in [False, True]:
            with self.subTest(retry=already_drained):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                root, appset = self.onboarding()
                postgres = {'metadata': {'name': 'postgres', 'labels': {'platform.ai-platform-portfolio/managed': 'true'}}}
                foreign = {'metadata': {'name': 'unrelated-discovery', 'labels': appset['metadata']['labels']}}
                drained = already_drained
                events = []

                def objects(kind, namespace=None):
                    if kind == 'applications.argoproj.io':
                        return [root, postgres]
                    if kind == 'applicationsets.argoproj.io':
                        return [foreign] if drained else [appset, foreign]
                    if kind == 'atlasschemas.db.atlasgo.io':
                        self.assertTrue(drained, 'Atlas guard ran before registration cleanup')
                        events.append('guard')
                    return []

                def delete(kind, names, namespace=None, foreground=False):
                    nonlocal drained
                    if kind == 'applicationsets.argoproj.io':
                        self.assertEqual(names, ['repository-discovery'])
                        self.assertTrue(foreground)
                        events.append('drain')
                        drained = True
                    else:
                        self.assertTrue(drained)
                        events.append(names[0])

                def command(*args, **kwargs):
                    if 'patch' in args and 'platform' in args:
                        events.append('suspend-root')
                    return 'atlasschemas.db.atlasgo.io'

                cluster.objects = Mock(side_effect=objects)
                cluster.delete = Mock(side_effect=delete)
                with patch('helm_chart.scripts.cluster.run', side_effect=command):
                    cluster.cleanup()
                self.assertEqual(events[0], 'suspend-root')
                self.assertLess(events.index('guard'), events.index('postgres'))
                if already_drained:
                    self.assertNotIn('drain', events)
                else:
                    self.assertLess(events.index('drain'), events.index('guard'))
                self.assertEqual(events[-1], 'platform')

    def test_nested_applications_finish_before_residual_guards(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        root, discovery = self.onboarding()
        registration = {'metadata': {'name': 'registration', 'uid': 'registration-uid',
                        'ownerReferences': [{'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'ApplicationSet',
                                             'name': 'repository-discovery', 'uid': 'discovery-uid'}]}}
        nested = {'metadata': {'name': 'consumer-set', 'uid': 'consumer-set-uid', 'annotations': {
                  'argocd.argoproj.io/tracking-id': 'registration:argoproj.io/ApplicationSet:argocd/consumer-set'}}}
        leaf = {'metadata': {'name': 'consumer-pr-42', 'uid': 'leaf-uid',
                'ownerReferences': [{'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'ApplicationSet',
                                     'name': 'consumer-set', 'uid': 'consumer-set-uid'}]}}
        delivery = {'metadata': {'name': 'consumer-delivery', 'uid': 'delivery-uid', 'annotations': {
                    'argocd.argoproj.io/tracking-id': 'registration:argoproj.io/Application:argocd/consumer-delivery'}}}
        unrelated = {'metadata': {'name': 'unrelated', 'uid': 'unrelated-uid',
                     'ownerReferences': [{'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'ApplicationSet',
                                          'name': 'consumer-set', 'uid': 'different-parent-uid'}]}}
        postgres = {'metadata': {'name': 'postgres', 'labels': {'platform.ai-platform-portfolio/managed': 'true'}}}
        apps = [root, postgres, leaf, delivery, registration, unrelated]
        sets = [nested, discovery]  # Deliberately reverse parent/child order.
        deleted = []

        def objects(kind, namespace=None):
            if kind == 'applications.argoproj.io':
                return list(apps)
            if kind == 'applicationsets.argoproj.io':
                return list(sets)
            self.assertNotIn(leaf, apps, 'Residual guard ran before PostDelete completed')
            return []

        def delete(kind, names, namespace=None, foreground=False):
            deleted.extend(names)
            if kind == 'applicationsets.argoproj.io':
                sets.clear()
                apps.remove(registration)
                leaf['metadata']['deletionTimestamp'] = 'now'

        def wait(check, description):
            if 'registered applications' in description:
                self.assertFalse(check(), 'Parent disappearance must not end descendant cleanup')
                self.assertNotIn('postgres', deleted)
                apps.remove(leaf)
                self.assertFalse(check(), 'Delivery Application cleanup must finish too')
                apps.remove(delivery)
            self.assertTrue(check())

        cluster.objects = Mock(side_effect=objects)
        cluster.delete = Mock(side_effect=delete)
        with patch('helm_chart.scripts.cluster.run', return_value='atlasschemas.db.atlasgo.io'), \
             patch('helm_chart.scripts.cluster.wait_for', side_effect=wait):
            cluster.cleanup()
        self.assertEqual(deleted, ['repository-discovery', 'postgres', 'platform'])
        self.assertIn(unrelated, apps)

    def test_deleting_atlas_hooks_finish_before_platform_deletion(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        postgres = {'metadata': {'name': 'postgres', 'labels': {'platform.ai-platform-portfolio/managed': 'true'}}}
        observations = iter([[{'metadata': {'name': 'post-delete', 'deletionTimestamp': 'now'}}], []])
        cluster.delete = Mock()

        def objects(kind, namespace=None):
            if kind == 'applications.argoproj.io':
                return [postgres]
            if kind == 'atlasschemas.db.atlasgo.io':
                cluster.delete.assert_not_called()
                return next(observations)
            return []

        cluster.objects = Mock(side_effect=objects)
        with patch('helm_chart.scripts.cluster.run', return_value='atlasschemas.db.atlasgo.io'), \
             patch('helm_chart.scripts.cluster.time.sleep') as sleep:
            cluster.cleanup()
        sleep.assert_called_once()
        self.assertEqual([c.args[1] for c in cluster.delete.call_args_list], [['postgres'], ['platform']])

    def test_unsafe_registration_cleanup_stops_before_deletion(self):
        for fault in ['tracking', 'retention', 'finalizers', 'read-error']:
            with self.subTest(fault=fault):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                root, appset = self.onboarding()
                if fault == 'tracking':
                    appset['metadata']['annotations']['argocd.argoproj.io/tracking-id'] = 'another-owner'
                elif fault == 'retention':
                    appset['spec']['syncPolicy']['preserveResourcesOnDeletion'] = True
                elif fault == 'finalizers':
                    appset['spec']['template']['metadata']['finalizers'] = []

                def objects(kind, namespace=None):
                    if kind == 'applications.argoproj.io':
                        return [root]
                    if fault == 'read-error':
                        raise subprocess.CalledProcessError(1, ['kubectl', 'get'])
                    return [appset]

                cluster.objects = Mock(side_effect=objects)
                cluster.delete = Mock()
                error = subprocess.CalledProcessError if fault == 'read-error' else RuntimeError
                with patch('helm_chart.scripts.cluster.run'), self.assertRaises(error):
                    cluster.cleanup()
                cluster.delete.assert_not_called()

    def test_registration_cleanup_timeout_preserves_platform_services(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        root, appset = self.onboarding()
        postgres = {'metadata': {'name': 'postgres'}}
        cluster.objects = Mock(side_effect=lambda kind, namespace=None:
                               [root, postgres] if kind == 'applications.argoproj.io' else [appset])
        cluster.delete = Mock(side_effect=TimeoutError('Waiting for application cleanup'))
        with patch('helm_chart.scripts.cluster.run'), self.assertRaises(TimeoutError):
            cluster.cleanup()
        self.assertEqual(cluster.delete.call_count, 1)
        self.assertEqual(cluster.delete.call_args.args, ('applicationsets.argoproj.io', ['repository-discovery'], 'argocd'))
        self.assertTrue(cluster.delete.call_args.kwargs['foreground'])

    def test_database_consumers_are_cleaned_before_platform_teardown(self):
        kinds = ['atlasschemas.db.atlasgo.io', 'atlasmigrations.db.atlasgo.io',
                 'databaseroles.postgresql.cnpg.io', 'databases.postgresql.cnpg.io']
        for kind, tracked, read_error in [(kind, False, False) for kind in kinds] + [
                ('databases.postgresql.cnpg.io', True, False),
                ('databases.postgresql.cnpg.io', True, True)]:
            with self.subTest(kind=kind, tracked=tracked, read_error=read_error):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                app = {'metadata': {'name': 'postgres',
                    'labels': {'platform.ai-platform-portfolio/managed': 'true'}},
                    'status': {'resources': [{'group': 'postgresql.cnpg.io', 'kind': 'Database',
                                             'namespace': 'postgres', 'name': 'service'}]}}
                def objects(resource, namespace=None):
                    if resource == 'applications.argoproj.io':
                        return [app]
                    if read_error:
                        raise subprocess.CalledProcessError(1, ['kubectl', 'get'])
                    return [{'metadata': {'namespace': 'postgres',
                             'name': 'service' if tracked else 'preview'}}] if resource == kind else []
                cluster.objects = Mock(side_effect=objects)
                cluster.delete = Mock()
                with patch('helm_chart.scripts.cluster.run', return_value='\n'.join(kinds)):
                    if read_error:
                        with self.assertRaises(subprocess.CalledProcessError):
                            cluster.cleanup()
                        cluster.delete.assert_not_called()
                    elif tracked:
                        cluster.cleanup()
                        cluster.delete.assert_called()
                    else:
                        with self.assertRaisesRegex(RuntimeError, 'preview'):
                            cluster.cleanup()
                        cluster.delete.assert_not_called()

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
        packages = [('atlantis', 'atlantis-azure'), ('holmes', 'holmes-azure'), ('testkube', 'testkube-azure'),
                    ('kargo', 'kargo-acr'), ('another-package', 'custom-identity')]
        for package, azure_package, extra in [(p, a, e) for p, a in packages for e in [False, True]]:
            with self.subTest(package=package, untracked_resource=extra):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                def app(name, wave):
                    return {'metadata': {'name': name, 'labels': {'platform.ai-platform-portfolio/managed': 'true'},
                                         'annotations': {'argocd.argoproj.io/sync-wave': str(wave)}}}
                apps = [app('configuration', 10), app('azure-service-operator', 10),
                        app(azure_package, 20), app(package, 40)]
                resource = {'apiVersion': 'managedidentity.azure.com/v1api20230131', 'kind': 'UserAssignedIdentity',
                            'metadata': {'name': package, 'namespace': package, 'annotations': {'argocd.argoproj.io/tracking-id':
                                         f'{azure_package}:managedidentity.azure.com/UserAssignedIdentity:{package}/{package}'}}}
                apps[2]['status'] = {'resources': [{'group': 'managedidentity.azure.com', 'kind': 'UserAssignedIdentity',
                                                  'namespace': package, 'name': package}]}
                apps = self.tracked_platform(apps)
                inventory = [resource]
                if extra:
                    inventory.append({**resource, 'metadata': {'name': 'untracked', 'namespace': 'atlantis'}})
                deleted = []
                def objects(kind, namespace=None):
                    if kind == 'applications.argoproj.io':
                        return apps
                    return inventory if azure_package not in deleted else []
                cluster.objects = Mock(side_effect=objects)
                cluster.delete = Mock(side_effect=lambda kind, names, namespace=None: deleted.extend(names))
                with patch('helm_chart.scripts.cluster.run', return_value='userassignedidentities.managedidentity.azure.com\n'):
                    if extra:
                        with self.assertRaisesRegex(RuntimeError, 'Remove ASO Azure resources'):
                            cluster.cleanup()
                        cluster.delete.assert_not_called()
                    else:
                        cluster.cleanup()
                        self.assertEqual(deleted,
                                         [package, azure_package, 'configuration',
                                          'azure-service-operator', 'platform'])

    def test_incomplete_platform_ownership_cannot_allow_azure_deletion(self):
        for fault in ['root-inventory', 'managed-label', 'package-tracking', 'resource-tracking']:
            with self.subTest(fault=fault):
                cluster = Cluster.__new__(Cluster)
                cluster.kube = ['kubectl']
                cluster.hub = None
                cluster.settings = {'cluster': {'role': 'management'}}
                package = {'metadata': {'name': 'custom-platform-package',
                           'labels': {'platform.ai-platform-portfolio/managed': 'true'}},
                           'status': {'resources': [{'group': 'managedidentity.azure.com', 'kind': 'UserAssignedIdentity',
                                                    'namespace': 'azure-resources', 'name': 'identity'}]}}
                apps = self.tracked_platform([{'metadata': {'name': 'azure-service-operator'}}, package])
                resource = {'apiVersion': 'managedidentity.azure.com/v1api20230131', 'kind': 'UserAssignedIdentity',
                            'metadata': {'name': 'identity', 'namespace': 'azure-resources', 'annotations': {
                                'argocd.argoproj.io/tracking-id': 'custom-platform-package:managedidentity.azure.com/UserAssignedIdentity:azure-resources/identity'}}}
                if fault == 'root-inventory':
                    apps[0]['status']['resources'] = []
                elif fault == 'managed-label':
                    package['metadata']['labels'].clear()
                elif fault == 'package-tracking':
                    package['metadata']['annotations']['argocd.argoproj.io/tracking-id'] = 'unrelated-owner'
                else:
                    resource['metadata']['annotations']['argocd.argoproj.io/tracking-id'] = 'unrelated-owner'
                cluster.objects = Mock(side_effect=lambda kind, namespace=None:
                                       apps if kind == 'applications.argoproj.io' else [resource])
                cluster.delete = Mock()
                with patch('helm_chart.scripts.cluster.run', return_value='userassignedidentities.managedidentity.azure.com'), \
                     self.assertRaisesRegex(RuntimeError, 'Remove ASO Azure resources'):
                    cluster.cleanup()
                cluster.delete.assert_not_called()

    def test_recovery_resources_finish_deletion_before_aso(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        def app(name, wave):
            return {'metadata': {'name': name, 'labels': {'platform.ai-platform-portfolio/managed': 'true'},
                                 'annotations': {'argocd.argoproj.io/sync-wave': str(wave)}}}
        resource = {'apiVersion': 'dbforpostgresql.azure.com/v20250801', 'kind': 'FlexibleServer',
                    'metadata': {'name': 'recovery', 'namespace': 'azure-resources', 'annotations': {
                        'argocd.argoproj.io/tracking-id': 'postgres-recovery:dbforpostgresql.azure.com/FlexibleServer:azure-resources/recovery'}}}
        apps = [app('configuration', 10), app('azure-service-operator', 10), app('postgres-recovery', 45)]
        apps[-1]['status'] = {'resources': [{'group': 'dbforpostgresql.azure.com', 'kind': 'FlexibleServer',
                                           'namespace': 'azure-resources', 'name': 'recovery'}]}
        apps = self.tracked_platform(apps)
        deleted = []
        cluster.objects = Mock(side_effect=lambda kind, namespace=None: apps if kind == 'applications.argoproj.io'
                               else ([] if 'postgres-recovery' in deleted else [resource]))
        cluster.delete = Mock(side_effect=lambda kind, names, namespace=None: deleted.extend(names))
        with patch('helm_chart.scripts.cluster.run', return_value='flexibleservers.dbforpostgresql.azure.com\n'):
            cluster.cleanup()
        self.assertEqual(deleted, ['postgres-recovery', 'configuration', 'azure-service-operator', 'platform'])

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

    def test_stuck_azure_finalizer_preserves_credential_and_operator(self):
        cluster = Cluster.__new__(Cluster)
        cluster.kube = ['kubectl']
        cluster.hub = None
        cluster.settings = {'cluster': {'role': 'management'}}
        def app(name, wave):
            return {'metadata': {'name': name,
                    'labels': {'platform.ai-platform-portfolio/managed': 'true'},
                    'annotations': {'argocd.argoproj.io/sync-wave': str(wave)}}}
        apps = [app('configuration', 10), app('azure-service-operator', 10), app('testkube-azure', 20)]
        apps[-1]['status'] = {'resources': [{'group': 'managedidentity.azure.com',
            'kind': 'UserAssignedIdentity', 'namespace': 'testkube', 'name': 'testkube'}]}
        resource = {'apiVersion': 'managedidentity.azure.com/v1api20230131',
                    'kind': 'UserAssignedIdentity',
                    'metadata': {'name': 'testkube', 'namespace': 'testkube', 'annotations': {
                        'argocd.argoproj.io/tracking-id': 'testkube-azure:managedidentity.azure.com/UserAssignedIdentity:testkube/testkube'}}}
        apps = self.tracked_platform(apps)
        cluster.objects = Mock(side_effect=lambda kind, namespace=None: apps
                               if kind == 'applications.argoproj.io' else [resource])
        cluster.delete = Mock()
        def wait(check, description):
            if 'ASO Azure resources' in description:
                self.assertFalse(check())
                raise TimeoutError(description)
        with patch('helm_chart.scripts.cluster.run', return_value='userassignedidentities.managedidentity.azure.com\n'), \
             patch('helm_chart.scripts.cluster.wait_for', side_effect=wait):
            with self.assertRaisesRegex(TimeoutError, 'ASO Azure resources'):
                cluster.cleanup()
        self.assertEqual([call.args[1][0] for call in cluster.delete.call_args_list], ['testkube-azure'])

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
        cluster.objects = Mock(side_effect=lambda kind, namespace=None: list(apps) if kind == 'applications.argoproj.io' else [])
        transitions = []

        def patch_application(*args, **kwargs):
            if 'api-resources' in args:
                return ''
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
            if 'api-resources' in args:
                return ''
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


class PartialTeardownTests(unittest.TestCase):
    def setUp(self):
        self.account = {'subscription_id': 'test-subscription', 'tenant_id': 'test-tenant'}
        self.values = {'id': '/subscriptions/test-subscription/resourceGroups/example/providers/Microsoft.ContainerService/managedClusters/test',
                       'name': 'test', 'resource_group_name': 'example', 'private_key': 'never-log-this'}
        self.aks = {'type': 'azurerm_kubernetes_cluster', 'mode': 'managed', 'values': self.values}

    def state(self, resources):
        return json.dumps({'values': {'root_module': {'child_modules': [{'resources': resources}]}}})

    def test_recovers_only_connection_metadata_from_nested_state(self):
        with patch('helm_chart.scripts.cluster.run', return_value=self.state([self.aks])) as run:
            connection = teardown_connection(['terraform'])
        self.assertEqual(connection, {'id': self.values['id'], 'name': 'test', 'resource_group': 'example'})
        self.assertEqual(run.call_args.args, ('terraform', 'show', '-json'))
        self.assertEqual(run.call_args.kwargs, {'capture': True, 'timeout': 60})
        self.assertNotIn('never-log-this', str(connection))

    def test_ambiguous_or_incomplete_state_and_tracked_kubernetes_are_rejected(self):
        incomplete = {**self.aks, 'values': {'name': 'test'}}
        for resources in [[self.aks, self.aks], [incomplete],
                          [self.aks, {'mode': 'managed', 'type': 'helm_release'}],
                          [self.aks, {'mode': 'managed', 'type': 'kubernetes_manifest'}]]:
            with self.subTest(resources=resources), \
                 patch('helm_chart.scripts.cluster.run', return_value=self.state(resources)):
                with self.assertRaises(RuntimeError):
                    teardown_connection(['terraform'])

    def test_partial_constructor_validates_account_and_hub_without_recreating_outputs(self):
        for fault in ['none', 'account', 'hub']:
            account = {**self.account, 'subscription_id': 'another' if fault == 'account' else 'test-subscription'}
            with self.subTest(fault=fault), \
                 patch.dict('os.environ', {'HUB_KUBECONFIG': '/hub' if fault == 'hub' else ''}), \
                 patch('helm_chart.scripts.cluster.current_account', return_value=account), \
                 patch('helm_chart.scripts.cluster.run', side_effect=['{}', self.state([self.aks]), '', '']) as run:
                if fault != 'none':
                    with self.assertRaisesRegex(RuntimeError, 'authenticated account|hub detachment'):
                        Cluster(Path('/bootstrap'), Path('/temporary/kubeconfig'), teardown=True)
                    self.assertEqual(run.call_count, 2)
                else:
                    cluster = Cluster(Path('/bootstrap'), Path('/temporary/kubeconfig'), teardown=True)
                    self.assertIsNone(cluster.settings)
                    self.assertEqual(cluster.connection['name'], 'test')
                    self.assertEqual(run.call_count, 4)
            self.assertFalse(any('apply' in c.args or 'refresh' in c.args for c in run.call_args_list))

    def test_complete_outputs_retain_normal_cleanup_settings(self):
        connection = {'name': 'test', 'resource_group': 'example'}
        settings = {'cluster': {'name': 'test', 'role': 'management'}}
        outputs = {'aks_connection': {'value': connection}, 'platform_settings': {'value': settings}}
        with patch('helm_chart.scripts.cluster.current_account', return_value=self.account), \
             patch('helm_chart.scripts.cluster.run', side_effect=[json.dumps(outputs), '', '']), \
             patch('helm_chart.scripts.cluster.teardown_connection') as recovery:
            cluster = Cluster(Path('/bootstrap'), Path('/temporary/kubeconfig'), teardown=True)
        self.assertEqual(cluster.settings, settings)
        recovery.assert_not_called()

    def test_no_aks_remaining_needs_no_kubernetes_or_upgrade_commands(self):
        with patch.dict('os.environ', {'HUB_KUBECONFIG': ''}), \
             patch('helm_chart.scripts.cluster.current_account', return_value=self.account), \
             patch('helm_chart.scripts.cluster.run', side_effect=['{}', self.state([])]) as run:
            cluster = Cluster(Path('/bootstrap'), Path('/temporary/kubeconfig'), teardown=True)
            cluster.cleanup()
            cluster.cancel_node_upgrades()
        self.assertEqual(run.call_count, 2)
        self.assertIsNone(cluster.connection)

    def test_consumer_checks_accept_only_drained_cluster_and_system_workloads(self):
        cluster = Cluster.__new__(Cluster)
        cluster.connection, cluster.settings, cluster.kube = {}, None, ['kubectl']
        kinds = ['applications.argoproj.io', 'atlasmigrations.db.atlasgo.io', 'databaseroles.postgresql.cnpg.io']
        aso = 'widgets.resources.azure.com'
        crd = {'metadata': {'name': aso, 'labels': {'app.kubernetes.io/name': 'azure-service-operator'}}}
        for leftover in [None, 'deployments.apps', *kinds, aso]:
            def objects(kind):
                if kind == 'customresourcedefinitions.apiextensions.k8s.io':
                    return [crd]
                if kind == leftover:
                    return [{'metadata': {'namespace': 'default'}}]
                if kind == 'pods':
                    return [{'metadata': {'namespace': 'kube-system'}}]
                return []
            cluster.objects = Mock(side_effect=objects)
            with self.subTest(leftover=leftover), \
                 patch('helm_chart.scripts.cluster.run', return_value='\n'.join([*kinds, 'appprojects.argoproj.io'])) as run:
                if leftover:
                    with self.assertRaisesRegex(RuntimeError, 'remain'):
                        cluster.cleanup()
                else:
                    cluster.cleanup()
            self.assertEqual(run.call_count, 1)  # Discovery only; no cleanup mutation.
            self.assertNotIn('appprojects.argoproj.io', [c.args[0] for c in cluster.objects.call_args_list])

    def test_unreadable_state_discovery_and_consumer_apis_fail_closed(self):
        error = subprocess.CalledProcessError(1, ['read'])
        with patch('helm_chart.scripts.cluster.run', side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                teardown_connection(['terraform'])
        cluster = Cluster.__new__(Cluster)
        cluster.connection, cluster.settings, cluster.kube = {}, None, ['kubectl']
        cluster.objects = Mock(side_effect=error)
        with patch('helm_chart.scripts.cluster.run', side_effect=error):
            with self.assertRaises(subprocess.CalledProcessError):
                cluster.cleanup()
        with patch('helm_chart.scripts.cluster.run', return_value='applications.argoproj.io'):
            with self.assertRaises(subprocess.CalledProcessError):
                cluster.cleanup()

    def test_startup_does_not_use_state_recovery(self):
        with patch('helm_chart.scripts.cluster.run', side_effect=subprocess.CalledProcessError(1, ['output'])), \
             patch('helm_chart.scripts.cluster.teardown_connection') as recovery:
            with self.assertRaises(subprocess.CalledProcessError):
                Cluster(Path('/bootstrap'), Path('/temporary/kubeconfig'))
        recovery.assert_not_called()


class NodeUpgradeTeardownTests(unittest.TestCase):
    def setUp(self):
        self.cluster = Cluster.__new__(Cluster)
        self.cluster.connection = {'name': 'other-cluster', 'resource_group': 'other-group',
                                   'subscription_id': 'test-subscription'}
        self.aks = {'id': '/clusters/other-cluster', 'nodeResourceGroup': 'azure-owned-nodes',
                    'agentPoolProfiles': [{'name': 'workers'}, {'name': 'system'}]}
        self.scales = [{'name': 'azure-generated-vmss', 'tags': {'aks-managed-poolName': 'workers'}}]

    def reads(self, owner='/clusters/OTHER-cluster'):
        return [json.dumps(self.aks), json.dumps(owner), json.dumps(self.scales)]

    def status(self, code):
        return Mock(returncode=0, stdout=json.dumps({'runningStatus': {'code': code}}))

    def test_active_upgrade_is_cancelled_only_after_ownership_checks_and_rechecked(self):
        with patch('helm_chart.scripts.cluster.run', side_effect=self.reads() + [None]) as run, \
             patch('helm_chart.scripts.cluster.subprocess.run', side_effect=[
                 self.status('RollingForward'), self.status('Cancelled')]) as status:
            self.cluster.cancel_node_upgrades()
        cancel = run.call_args
        self.assertEqual(cancel.args[:4], ('az', 'vmss', 'rolling-upgrade', 'cancel'))
        self.assertIn('azure-owned-nodes', cancel.args)
        self.assertIn('azure-generated-vmss', cancel.args)
        self.assertEqual(cancel.kwargs['timeout'], 180)
        self.assertEqual(status.call_count, 2)
        self.assertTrue(all(c.kwargs['timeout'] == 30 for c in status.call_args_list))

    def test_inactive_or_missing_upgrade_is_not_cancelled(self):
        absent = Mock(returncode=1, stderr='ERROR: (NotFound) No rolling upgrade history')
        for result in [self.status(c) for c in ['Completed', 'Cancelled', 'Faulted']] + [absent]:
            with self.subTest(result=result), \
                 patch('helm_chart.scripts.cluster.run', side_effect=self.reads()) as run, \
                 patch('helm_chart.scripts.cluster.subprocess.run', return_value=result):
                self.cluster.cancel_node_upgrades()
            self.assertEqual(run.call_count, 3)

    def test_unverified_group_or_pool_stops_before_any_upgrade_action(self):
        for fault in ['group', 'missing-owner', 'pool', 'untagged']:
            with self.subTest(fault=fault):
                self.scales = [{'name': 'azure-generated-vmss', 'tags': {'aks-managed-poolName': 'workers'}}]
                if fault == 'pool':
                    self.scales.append({'name': 'unrelated', 'tags': {'aks-managed-poolName': 'another-pool'}})
                if fault == 'untagged':
                    self.scales[0]['tags'] = {}
                owner = None if fault == 'missing-owner' else '/unrelated' if fault == 'group' else self.aks['id']
                with patch('helm_chart.scripts.cluster.run', side_effect=self.reads(owner)), \
                     patch('helm_chart.scripts.cluster.subprocess.run') as status:
                    with self.assertRaisesRegex(RuntimeError, 'unverified|Unverified'):
                        self.cluster.cancel_node_upgrades()
                status.assert_not_called()

    def test_unreadable_or_unknown_status_is_not_treated_as_absent(self):
        for result in [Mock(returncode=1, stderr='ERROR: (AuthorizationFailed) NotFound in message'),
                       self.status('Unexpected')]:
            with self.subTest(result=result), \
                 patch('helm_chart.scripts.cluster.run', side_effect=self.reads()) as run, \
                 patch('helm_chart.scripts.cluster.subprocess.run', return_value=result):
                with self.assertRaises(RuntimeError):
                    self.cluster.cancel_node_upgrades()
            self.assertEqual(run.call_count, 3)

    def test_cancel_error_timeout_or_still_active_stops_teardown(self):
        for error in [subprocess.CalledProcessError(1, ['az']), subprocess.TimeoutExpired(['az'], 180), None]:
            with self.subTest(error=error), \
                 patch('helm_chart.scripts.cluster.run', side_effect=self.reads() + [error]), \
                 patch('helm_chart.scripts.cluster.subprocess.run', return_value=self.status('RollingForward')):
                with self.assertRaises(type(error) if error else RuntimeError):
                    self.cluster.cancel_node_upgrades()

    def test_down_checks_upgrades_after_package_cleanup(self):
        cluster = Mock()
        with patch('sys.argv', ['cluster.py', 'down', '--infra-dir', '/bootstrap']), \
             patch('helm_chart.scripts.cluster.already_down', return_value=False), \
             patch('helm_chart.scripts.cluster.Cluster', return_value=cluster):
            main()
        self.assertEqual([c[0] for c in cluster.method_calls], ['cleanup', 'cancel_node_upgrades'])


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


class DNSLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.account = {'subscription_id': 'subscription', 'tenant_id': 'tenant'}
        self.config = {'owner': 'example', 'group': 'management',
                       'zone': {'name': 'example.test', 'resource_group_name': 'dns'}}
        self.marker = {'name': 'external-dns-a-events', 'type': 'Microsoft.Network/dnszones/TXT', 'etag': 'txt-version',
                       'TXTRecords': [{'value': ['"heritage=external-dns,external-dns/owner=example,',
                                                'external-dns/resource=httproute/events/webhook"']}]}
        self.address = {'name': 'events', 'type': 'Microsoft.Network/dnszones/A', 'etag': 'a-version'}

    def test_registry_ownership_is_exact_and_mixed_or_unsupported_records_fail(self):
        for fault in ['foreign', 'mixed', 'mixed-field', 'unsupported', 'no-etag']:
            with self.subTest(fault=fault):
                marker = json.loads(json.dumps(self.marker))
                if fault == 'foreign':
                    marker['TXTRecords'][0]['value'] = ['heritage=external-dns,external-dns/owner=example-other']
                elif fault == 'mixed':
                    marker['TXTRecords'].append({'value': ['external-dns/owner=another']})
                elif fault == 'mixed-field':
                    marker['TXTRecords'][0]['value'][0] = marker['TXTRecords'][0]['value'][0].replace(
                        'external-dns/owner=example', 'external-dns/owner=example,external-dns/owner=another')
                elif fault == 'unsupported':
                    marker['name'] = 'external-dns-cname-events'
                else:
                    marker.pop('etag')
                with patch('helm_chart.scripts.cluster.run', return_value=json.dumps([marker, self.address])) as run:
                    if fault == 'foreign':
                        self.assertEqual(owned_dns(self.account, self.config), [])
                    else:
                        with self.assertRaises(RuntimeError):
                            owned_dns(self.account, self.config)
                self.assertFalse(any('delete' in c.args for c in run.call_args_list))

    def test_stale_cleanup_checks_marker_and_deletes_only_owned_pair_with_etags(self):
        for orphan_marker in [False, True]:
            with self.subTest(orphan_marker=orphan_marker):
                target = None if orphan_marker else self.address
                with patch('helm_chart.scripts.cluster.owned_dns', side_effect=[[(self.marker, target)], []]), \
                     patch('helm_chart.scripts.cluster.run', return_value=json.dumps(self.marker)) as run:
                    cleanup_stale_dns(self.account, self.config)
                deletes = [c.args for c in run.call_args_list if 'delete' in c.args]
                self.assertEqual([c[4] for c in deletes], ['txt'] if orphan_marker else ['a', 'txt'])
                self.assertEqual([c[c.index('--if-match') + 1] for c in deletes],
                                 ['txt-version'] if orphan_marker else ['a-version', 'txt-version'])
                self.assertTrue(all(c[c.index('--subscription') + 1] == 'subscription' for c in deletes))

    def test_record_or_ownership_conflict_stops_before_deleting_marker(self):
        for ownership_changed in [False, True]:
            with self.subTest(ownership_changed=ownership_changed):
                current = {**self.marker, 'etag': 'changed'} if ownership_changed else self.marker
                def command(*args, **kwargs):
                    if 'show' in args:
                        return json.dumps(current)
                    raise subprocess.CalledProcessError(1, ['az', 'delete', '--if-match'])
                with patch('helm_chart.scripts.cluster.owned_dns', return_value=[(self.marker, self.address)]), \
                     patch('helm_chart.scripts.cluster.run', side_effect=command) as run:
                    with self.assertRaises(RuntimeError if ownership_changed else subprocess.CalledProcessError):
                        cleanup_stale_dns(self.account, self.config)
                self.assertFalse(any('txt' in c.args and 'delete' in c.args for c in run.call_args_list))
                if ownership_changed:
                    self.assertFalse(any('delete' in c.args for c in run.call_args_list))

    def test_empty_state_retry_reads_effective_tfvars_and_requires_absent_group(self):
        for fault in ['none', 'state', 'group', 'console']:
            with self.subTest(fault=fault):
                outputs = ['{}', 'module.remaining' if fault == 'state' else '', json.dumps(fault == 'group')]
                console = Mock(returncode=1 if fault == 'console' else 0, stdout=json.dumps(json.dumps(self.config)))
                with patch('helm_chart.scripts.cluster.run', side_effect=outputs) as run, \
                     patch('helm_chart.scripts.cluster.current_account', return_value=self.account), \
                     patch('helm_chart.scripts.cluster.subprocess.run', return_value=console) as subprocess_run, \
                     patch('helm_chart.scripts.cluster.cleanup_stale_dns') as cleanup:
                    if fault == 'none':
                        self.assertTrue(already_down(Path('/bootstrap'), Path('/inputs/custom.tfvars')))
                        cleanup.assert_called_once_with(self.account, self.config)
                        self.assertIn('-var-file=/inputs/custom.tfvars', subprocess_run.call_args.args[0])
                        self.assertIn('ARM_TENANT_ID', subprocess_run.call_args.kwargs['env'])
                    elif fault == 'state':
                        self.assertFalse(already_down(Path('/bootstrap'), Path('/inputs/custom.tfvars')))
                        cleanup.assert_not_called()
                        subprocess_run.assert_not_called()
                    else:
                        with self.assertRaises(RuntimeError):
                            already_down(Path('/bootstrap'), Path('/inputs/custom.tfvars'))
                        cleanup.assert_not_called()
                self.assertFalse(any('apply' in c.args or 'destroy' in c.args for c in run.call_args_list))

    def test_existing_outputs_use_cluster_path_and_already_down_never_constructs_cluster(self):
        with patch('helm_chart.scripts.cluster.run', return_value='{"aks_connection":{}}'), \
             patch('helm_chart.scripts.cluster.current_account') as account:
            self.assertFalse(already_down(Path('/bootstrap'), Path('/inputs.tfvars')))
            account.assert_not_called()
        with patch('sys.argv', ['cluster.py', 'down', '--infra-dir', '/bootstrap', '--tfvars', 'custom.tfvars']), \
             patch('helm_chart.scripts.cluster.already_down', return_value=True) as down, \
             patch('helm_chart.scripts.cluster.Cluster') as cluster:
            main()
        down.assert_called_once_with(Path('/bootstrap'), Path('/bootstrap/custom.tfvars'))
        cluster.assert_not_called()


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
        def wait(check, description, retry_command_errors=False):
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
