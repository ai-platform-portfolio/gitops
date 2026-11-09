#!/usr/bin/env python3
"""Render the complete platform and pinned upstream charts without Azure access."""
from concurrent.futures import ThreadPoolExecutor
import base64
import hashlib
import json
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM_CHART = ROOT / 'helm_chart'


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True)
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, args, result.stdout, result.stderr)
    return result.stdout


def main():
    bootstrap = ROOT / 'bootstrap'
    bootstrap_values = yaml.safe_load((bootstrap / 'argocd-values.yaml').read_text())
    configmap = yaml.safe_load((bootstrap / 'argocd-configmap.yaml').read_text())
    assert bootstrap_values['configs']['cm'] == {'create': False}, 'Argo configuration must live in the ConfigMap file'
    assert configmap['kind'] == 'ConfigMap' and configmap['metadata']['name'] == 'argocd-cm'
    outputs = (bootstrap / 'outputs.tf').read_text()
    assert 'data.azurerm_client_config' not in outputs and 'var.account' not in outputs and 'oidc_issuer_url' not in outputs, 'Account identifiers must not be Terraform outputs'
    runtime = {name: 'example' for path in (HELM_CHART / 'platform').rglob('*') if path.is_file()
               for name in re.findall(r'\.Values.runtime\.([A-Z_]+)', path.read_text())}
    runtime.update(DNS_ZONE='example.test', ARGOCD_HOSTNAME='argocd-management.example.test', ACME_EMAIL='ops@example.test', CLUSTER_NAME='test',
                   AZURE_KEY_VAULT_URI='https://example.vault.azure.net/',
                   SONAR_SAML_CERTIFICATE='YWJj', SONAR_SAML_APPLICATION_ID='api://example',
                   AKS_OIDC_ISSUER_URL='https://issuer.example.test/')
    for key in ['AZURE_TENANT_ID', 'AZURE_SUBSCRIPTION_ID', 'AZURE_CLIENT_ID',
                'POSTGRES_AZURE_CLIENT_ID', 'CROSSPLANE_CLIENT_ID', 'CROSSPLANE_PRINCIPAL_ID',
                'ONBOARDING_CLIENT_ID', 'ONBOARDING_PRINCIPAL_ID']:
        runtime[key] = '00000000-0000-0000-0000-000000000001'
    with tempfile.TemporaryDirectory(prefix='platform-render-') as directory:
        tmp = Path(directory)
        values = tmp / 'values.json'
        recovery = {
            'enabled': True, 'name': 'example-recovery', 'pointInTimeUTC': '2026-01-01T00:00:00Z',
            'network': {'resourceGroup': 'network-rg', 'virtualNetwork': 'network', 'subnet': 'private',
                        'dnsResourceGroup': 'dns-rg', 'privateDnsZone': 'privatelink.postgres.database.azure.com'},
            'administrators': {'aks-admins': {'objectId': '00000000-0000-0000-0000-000000000002',
                                              'principalName': 'example-admins', 'principalType': 'Group'}},
        }
        recovery_inputs = {'runtime': runtime, 'postgres': {'recovery': recovery}}
        values.write_text(json.dumps(recovery_inputs))
        rendered_recovery = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'),
                                                          '-f', str(values), '--show-only', 'templates/postgres-recovery.yaml')))
        app = next(d for d in rendered_recovery if d)
        resources = list(yaml.safe_load_all(app['spec']['source']['helm']['valuesObject']['manifests']))
        server = next(d for d in resources if d['kind'] == 'FlexibleServer')
        assert server['spec']['createMode'] == 'PointInTimeRestore'
        assert server['spec']['network'] == {'publicNetworkAccess': 'Disabled'}
        assert server['spec']['authConfig']['passwordAuth'] == 'Disabled'
        assert server['spec']['sourceServerResourceReference']['armId'].endswith('/pg-ai-platform-portfolio-management')
        assert server['spec']['azureName'] == recovery['name']
        endpoint = next(d for d in resources if d['kind'] == 'PrivateEndpoint')
        assert endpoint['spec']['privateLinkServiceConnections'][0]['privateLinkServiceReference']['name'] == recovery['name']
        dns = next(d for d in resources if d['kind'] == 'PrivateEndpointsPrivateDnsZoneGroup')
        assert dns['spec']['owner'] == {'name': recovery['name']}
        assert int(server['metadata']['annotations']['argocd.argoproj.io/sync-wave']) < int(endpoint['metadata']['annotations']['argocd.argoproj.io/sync-wave']) < int(dns['metadata']['annotations']['argocd.argoproj.io/sync-wave'])
        admins = [d for d in resources if d['kind'] == 'FlexibleServersAdministrator']
        assert len(admins) == 2 and all(d['spec']['owner'] == {'name': recovery['name']} for d in admins)
        # Source-name equality must fail before any Azure resource can be rendered.
        recovery['name'] = 'pg-ai-platform-portfolio-management'
        values.write_text(json.dumps(recovery_inputs))
        rejected = subprocess.run(['helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values)],
                                  text=True, capture_output=True)
        assert rejected.returncode and 'recovery name must differ' in rejected.stderr
        recovery.update(name='example-recovery', pointInTimeUTC='')
        values.write_text(json.dumps(recovery_inputs))
        rejected = subprocess.run(['helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values)],
                                  text=True, capture_output=True)
        assert rejected.returncode and 'pointInTimeUTC' in rejected.stderr
        hba_rule = 'hostssl example example_login all scram-sha-256'
        preview_only = {'argocd': {'onboarding': {'profiles': ['previews']}},
                        'atlas': {'azurePostgres': {'enabled': False}}}
        values.write_text(json.dumps({**preview_only, 'runtime': runtime, 'postgres': {'additionalHba': [hba_rule]}}))
        hba_render = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))))
        postgres = next(d for d in hba_render if d and d['kind'] == 'Application' and d['metadata']['name'] == 'postgres')
        cluster = next(d for d in yaml.safe_load_all(postgres['spec']['source']['helm']['valuesObject']['manifests']) if d and d['kind'] == 'Cluster')
        assert cluster['spec']['postgresql']['pg_hba'][-2:] == [hba_rule, 'host all all all reject']
        assert {d['metadata']['name'] for d in hba_render if d and d['kind'] == 'ApplicationSet'} == {'application-previews'}
        atlas_azure = next(d for d in hba_render if d and d['metadata']['name'] == 'atlas-azure')
        azure_docs = [d for d in yaml.safe_load_all(atlas_azure['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert {d['kind'] for d in azure_docs} == {'UserAssignedIdentity', 'FederatedIdentityCredential'}, 'Deferred server must not block Atlas identity setup'
        for profiles, message in [
            (['previews', 'environments'], 'environments onboarding requires atlas.azurePostgres.enabled'),
            (['unknown'], 'argocd.onboarding.profiles supports only previews and environments'),
        ]:
            values.write_text(json.dumps({'runtime': runtime, 'argocd': {'onboarding': {'profiles': profiles}},
                                          'atlas': {'azurePostgres': {'enabled': False}}}))
            rejected = subprocess.run(['helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values)],
                                      text=True, capture_output=True)
            assert rejected.returncode and message in rejected.stderr

        # Preview TLS is shared infrastructure, with no per-application allowlist.
        for role in ['management', 'workload']:
            for preview, hostname in [
                ({'enabled': False}, None),
                ({}, '*.previews.alternate.test'),
                ({'hostname': '*.sandbox.other.test'}, '*.sandbox.other.test'),
            ]:
                values.write_text(json.dumps({**preview_only, 'runtime': {**runtime, 'DNS_ZONE': 'alternate.test'},
                                              'cluster': {'name': 'other', 'role': role},
                                              'gateway': {'preview': preview}}))
                apps = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))))
                app = next(d for d in apps if d and d.get('kind') == 'Application' and d['metadata']['name'] == 'gateway')
                docs = list(yaml.safe_load_all(app['spec']['source']['helm']['valuesObject']['manifests']))
                gateway = next(d for d in docs if d['kind'] == 'Gateway')
                listeners = gateway['spec']['listeners']
                assert listeners[0]['name'] == 'http' and listeners[0]['protocol'] == 'HTTP'
                assert not any(d['kind'] in ['Certificate', 'Secret'] for d in docs), 'cert-manager owns the central TLS resources'
                if hostname is None:
                    assert len(listeners) == 1 and 'annotations' not in gateway['metadata']
                else:
                    assert len(listeners) == 2 and listeners[1]['hostname'] == hostname
                    labels = listeners[1]['allowedRoutes']['namespaces']['selector']['matchLabels']
                    assert labels == {'gitops.platform/gateway': 'public', 'gitops.platform/preview': 'true'}
                    assert gateway['metadata']['annotations']['cert-manager.io/cluster-issuer'] == 'letsencrypt-azuredns'
                    if role == 'management':
                        registrations = [d for d in apps if d and d.get('kind') == 'ApplicationSet']
                        assert {r['metadata']['name'] for r in registrations} == {'application-previews'}
                        for registration in registrations:
                            source = registration['spec']['template']['spec']['sources'][0]
                            previews = source['helm']['valuesObject']['platform']['argocd']['previews']
                            assert previews['domain'] == hostname.removeprefix('*.'), 'Preview routes must match the shared certificate domain'
        values.write_text(json.dumps({'runtime': runtime, 'gateway': {'preview': {'hostname': 'not-a-wildcard.test'}}}))
        try:
            command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))
        except subprocess.CalledProcessError as error:
            assert 'gateway.preview.hostname must be a wildcard DNS hostname' in error.stderr
        else:
            raise AssertionError('Invalid preview wildcard hostname was accepted')
        atlantis = {
            'enabled': True, 'github_app_id': '12345', 'github_app_slug': 'platform-atlantis',
            'github_team': 'platform',
            'repositories': ['github.com/example/*', '!github.com/example/gitops', '!github.com/example/terraform'],
            'azure': {
                'identity_resource_group_name': 'rg-{{ .Values.cluster.name }}-atlantis',
                'role_assignments': {
                    'state': {'resource_group_name': 'rg-state', 'storage_account_name': 'examplestate',
                              'role_definition_name': 'Storage Blob Data Contributor'},
                    'deployment': {'resource_group_name': 'rg-developer', 'role_definition_name': 'Contributor'},
                },
            },
        }
        values.write_text(json.dumps({'runtime': runtime, 'sonarqubeReady': True,
                                      'atlantis': {'enabled': False},
                                      'clusterOnboarding': {'enabled': False},
                                      'backstage': {'clusterRequests': {'enabled': False}}}))
        disabled = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))))
        assert 'atlantis' not in {app['metadata']['name'] for app in disabled}, 'Atlantis must be opt-in'
        assert bootstrap_values['configs']['params']['server.insecure'] is True
        assert bootstrap_values['configs']['params']['controller.diff.server.side'] is True, 'API-defaulted CRDs require server-side diff'
        assert bootstrap_values['server']['service']['type'] == 'ClusterIP'
        controller_env = {entry['name']: entry['value'] for entry in bootstrap_values['controller']['env']}
        assert controller_env['ARGOCD_CLUSTER_CACHE_WATCH_RETRY_USE_BACKOFF'] == 'true'
        assert controller_env['ARGOCD_CLUSTER_CACHE_LIST_SEMAPHORE'] == '5'

        for label, overrides in {
            'app-id': {'github_app_id': ''},
            'team': {'github_team': ''},
            'global-repo': {'repositories': ['*']},
            'owner-wildcard': {'repositories': ['github.com/*/repo']},
            'exclusions-only': {'repositories': ['!github.com/example/repo']},
            'missing-roles': {'azure': {'role_assignments': None}},
            'identity-group': {'azure': {'identity_resource_group_name': ''}},
            'location': {'azure': {'location': ''}},
            'role-key': {'azure': {'role_assignments': {'Bad_Role': {'resource_group_name': 'rg', 'role_definition_name': 'Reader'}}}},
            'role-group': {'azure': {'role_assignments': {'state': {'resource_group_name': '', 'role_definition_name': 'Reader'}}}},
            'role-name': {'azure': {'role_assignments': {'state': {'resource_group_name': 'rg', 'role_definition_name': ''}}}},
            'storage-name': {'azure': {'role_assignments': {'state': {'resource_group_name': 'rg', 'storage_account_name': 'INVALID', 'role_definition_name': 'Reader'}}}},
        }.items():
            values.write_text(json.dumps({'runtime': runtime, 'atlantis': {**atlantis, **overrides}}))
            try:
                command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))
            except subprocess.CalledProcessError:
                continue
            raise AssertionError('Invalid Atlantis GitOps input was accepted: ' + label)
        # Also validate the explicitly restored staging/production configuration.
        values.write_text(json.dumps({'runtime': runtime, 'sonarqubeReady': True, 'atlantis': atlantis,
                                      'argocd': {'onboarding': {'profiles': ['previews', 'environments']}},
                                      'atlas': {'azurePostgres': {'enabled': True}}}))
        argo_values = tmp / 'argocd.json'
        argo_values.write_text(json.dumps({**bootstrap_values, 'extraObjects': [configmap]}))
        version = re.search(r'chart_version\s*=\s*"([^"]+)"', (bootstrap / 'terraform.tfvars').read_text())[1]
        argo = list(yaml.safe_load_all(command('helm', 'template', 'argocd', 'argo-cd', '--repo',
            'https://argoproj.github.io/argo-helm', '--version', version, '--namespace', 'argocd', '-f', str(argo_values))))
        controller = next(d for d in argo if d and d['kind'] == 'StatefulSet'
                          and d['metadata']['name'] == 'argocd-application-controller')
        rendered_env = {entry['name']: entry.get('value') for entry in controller['spec']['template']['spec']['containers'][0]['env']}
        assert all(rendered_env.get(name) == value for name, value in controller_env.items()), 'Argo must receive the request-recovery settings'
        maps = [d for d in argo if d and d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'argocd-cm']
        assert len(maps) == 1 and maps[0]['data'] == configmap['data'], 'Helm must install exactly the file-defined Argo ConfigMap'
        rendered = [d for d in yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))) if d]
        sensors = [d for d in rendered if d['kind'] == 'Sensor' and d['metadata']['name'].startswith('onboard-')]
        assert sensors and not any(d['kind'] == 'CronJob' for d in rendered), 'Onboarding must be event-driven'
        for sensor in sensors:
            assert sensor['metadata']['annotations']['argocd.argoproj.io/sync-options'] == 'SkipDryRunOnMissingResource=true'
            assert sensor['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '30'
            name = sensor['metadata']['name'].removeprefix('onboard-')
            filters = sensor['spec']['dependencies'][0]['filters']['data']
            def accepts(payload):
                for rule in filters:
                    value = payload
                    for key in rule['path'].split('.'):
                        value = value.get(key) if isinstance(value, dict) else None
                    if not isinstance(value, str) or not any(re.fullmatch(pattern, value) for pattern in rule['value']):
                        return False
                return True
            event = {'revision': 'a' * 40, 'request': 'b' * 32}
            assert accepts({'body': {'metadata': {'name': 'onboard-' + name}, 'data': event}})
            for change in [{'revision': 'invalid'}, {'request': ''}]:
                assert not accepts({'body': {'metadata': {'name': 'onboard-' + name}, 'data': {**event, **change}}}), 'Invalid apply event must not trigger onboarding'
            assert not accepts({'body': {'metadata': {'name': 'unrelated'}, 'data': event}})
            assert not accepts({'body': {}})
            trigger = sensor['spec']['triggers'][0]
            assert trigger['retryStrategy']['steps'] == 5
            job = trigger['template']['k8s']['source']['resource']
            assert trigger['template']['k8s']['operation'] == 'update'
            assert job['metadata']['name'] == sensor['metadata']['name']
            assert job['spec']['ttlSecondsAfterFinished'] == 0 and job['spec']['backoffLimit'] == 2
            container = job['spec']['template']['spec']['containers'][0]
            assert container['resources']['requests']['cpu'] == '50m' and 'cpu' not in container['resources']['limits']
            assert container['env'] == [{'name': 'CLUSTER_NAME', 'value': name}]
            assert '@sha256:' in container['image']
        settings_map = next(d for d in rendered if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'workload-onboarding-settings')
        assert 'clusterOnboarding' in json.loads(settings_map['data']['values.json'])
        onboarding_aso = next(d for d in rendered if d['kind'] == 'Secret' and d['metadata']['name'] == 'aso-credential' and d['metadata']['namespace'] == 'cluster-onboarding')
        assert {key: base64.b64decode(value).decode() for key, value in onboarding_aso['data'].items()} == {
            'AZURE_CLIENT_ID': runtime['ASO_CLIENT_ID'],
            'AZURE_TENANT_ID': runtime['AZURE_TENANT_ID'],
            'AZURE_SUBSCRIPTION_ID': runtime['AZURE_SUBSCRIPTION_ID'],
            'USE_WORKLOAD_IDENTITY_AUTH': 'true',
        }
        applications = [d for d in rendered if d['kind'] == 'Application']
        names = {app['metadata']['name'] for app in applications}
        assert len(applications) == len(names), 'Duplicate Application names'
        expected = {
            'argo-events', 'platform-events', 'namespaces', 'reflector', 'argo-rollouts', 'kargo-acr', 'kargo-identity', 'kargo', 'testkube', 'testkube-storage', 'testkube-nats', 'testkube-analysis', 'testkube-azure', 'testkube-reloader', 'testkube-checks', 'atlas-operator', 'atlas-database-access', 'atlas-azure', 'federation', 'envoy-gateway', 'external-secrets',
            'clickhouse-operator', 'cert-manager', 'cloudnative-pg', 'configuration',
            'external-dns', 'gateway', 'argocd-route', 'argocd-registry', 'argocd-github', 'plugin-barman-cloud', 'postgres', 'clickhouse',
            'loki', 'grafana', 'tempo', 'mimir', 'langfuse', 'langfuse-credentials', 'opentelemetry-collector',
            'k8s-monitoring', 'sonarqube', 'sonarqube-configure', 'routes',
            'azure-service-operator', 'foundry-publisher', 'atlantis', 'atlantis-azure', 'holmes', 'holmes-azure',
            'crossplane', 'crossplane-packages', 'crossplane-identity-api', 'service-identities', 'langfuse-reloader',
            'backstage', 'backstage-credentials', 'backstage-reloader',
            'devlake', 'devlake-credentials', 'devlake-configure', 'devlake-collection', 'devlake-reloader',
            'redis', 'redis-certificates', 'redis-reloader',
        }
        assert names == expected, 'A platform Application was omitted or renamed'
        kargo_acr = next(app for app in applications if app['metadata']['name'] == 'kargo-acr')
        acr_resources = [d for d in yaml.safe_load_all(kargo_acr['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert {d['kind'] for d in acr_resources} == {'UserAssignedIdentity', 'FederatedIdentityCredential', 'RoleAssignment'}
        assert next(d for d in acr_resources if d['kind'] == 'FederatedIdentityCredential')['spec']['subject'] == 'system:serviceaccount:kargo:kargo-controller'
        assert next(d for d in acr_resources if d['kind'] == 'RoleAssignment')['spec']['roleDefinitionReference']['wellKnownName'] == 'AcrPull'
        publisher_app = next(app for app in applications if app['metadata']['name'] == 'foundry-publisher')
        publisher_docs = [d for d in yaml.safe_load_all(publisher_app['spec']['source']['helm']['valuesObject']['manifests']) if d]
        publisher_identity = next(d for d in publisher_docs if d['kind'] == 'UserAssignedIdentity')
        assert publisher_identity['metadata']['namespace'] == 'azure-resources'
        assert next(d for d in publisher_docs if d['kind'] == 'FederatedIdentityCredential')['spec']['subject'] == 'system:serviceaccount:foundry-publisher:foundry-publisher'
        publisher_role = next(d for d in publisher_docs if d['kind'] == 'RoleAssignment')
        assert publisher_role['spec']['owner']['armId'].endswith('/accounts/foundry-management-portfolio-v2/projects/management')
        assert publisher_role['spec']['roleDefinitionReference']['wellKnownName'] == 'Foundry Project Manager'
        kargo_identity = next(app for app in applications if app['metadata']['name'] == 'kargo-identity')
        kargo_resources = [d for d in yaml.safe_load_all(kargo_identity['spec']['source']['helm']['valuesObject']['manifests']) if d]
        kargo_public_client = next(d for d in kargo_resources if d['kind'] == 'PublicOIDCApplication')
        assert kargo_public_client['spec']['redirectUri'] == 'https://kargo.example.test/login'
        assert any(d['kind'] == 'RoleAssignment' and d['spec']['forProvider']['resourceObjectIdRef']['name'] == 'kargo-sso'
                   for d in kargo_resources)
        identity_api = next(app for app in applications if app['metadata']['name'] == 'crossplane-identity-api')
        identity_docs = [d for d in yaml.safe_load_all(identity_api['spec']['source']['helm']['valuesObject']['manifests']) if d]
        public_composition = next(d for d in identity_docs if d['kind'] == 'Composition' and d['metadata']['name'] == 'entra-public-oidc')
        composed = public_composition['spec']['pipeline'][0]['input']['resources']
        assert {r['name'] for r in composed} == {'application', 'principal'}, 'Public client must not create a password'
        assert composed[0]['base']['spec']['forProvider']['singlePageApplication'] == {'redirectUris': []}
        assert composed[0]['connectionDetails'][0]['name'] == 'client-id'
        onboarding_app = next(app for app in applications if app['metadata']['name'] == 'platform-events')
        event_docs = [d for d in yaml.safe_load_all(onboarding_app['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert not any(d['kind'] == 'NetworkPolicy' for d in event_docs), 'Do not restore removed platform NetworkPolicies'
        source = next(d for d in event_docs if d['kind'] == 'EventSource')
        assert source['metadata']['annotations']['argocd.argoproj.io/sync-options'] == 'SkipDryRunOnMissingResource=true'
        assert source['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '30'
        assert 'webhook' not in source['spec'] and 'service' not in source['spec']
        watch = source['spec']['resource']['apply']
        assert watch['eventTypes'] == ['ADD', 'UPDATE'] and watch['filter']['afterStart'] is False
        bus = next(d for d in event_docs if d['kind'] == 'EventBus')['spec']['jetstream']
        assert bus['replicas'] == 1 and bus['persistence']['volumeSize'] == '1Gi'
        # GitHub delivery is active; consumers can still disable the shared receiver.
        assert any(d['kind'] == 'EventSource' and d['metadata']['name'] == 'github' for d in event_docs)
        disabled_render = [d for d in yaml.safe_load_all(command('helm', 'template', 'platform',
                           str(HELM_CHART / 'platform'), '-f', str(values),
                           '--set', 'events.github.enabled=false', '--set', 'argocd.onboarding.enabled=false')) if d]
        disabled_events = next(d for d in disabled_render if d['metadata']['name'] == 'platform-events')
        disabled_docs = yaml.safe_load_all(disabled_events['spec']['source']['helm']['valuesObject']['manifests'])
        assert not any(d and d['kind'] == 'EventSource' and d['metadata']['name'] == 'github' for d in disabled_docs)
        github_values = tmp / 'github-events.json'
        github_values.write_text(json.dumps({'runtime': runtime, 'sonarqubeReady': True,
                                            'events': {'github': {'enabled': True}}}))
        github_render = [d for d in yaml.safe_load_all(command('helm', 'template', 'platform',
                         str(HELM_CHART / 'platform'), '-f', str(github_values))) if d]
        github_apps = {d['metadata']['name']: d for d in github_render if d['kind'] == 'Application'}
        def manifests(app):
            return [d for d in yaml.safe_load_all(github_apps[app]['spec']['source']['helm']['valuesObject']['manifests']) if d]
        credentials = manifests('argocd-github')
        webhook_generator = next(d for d in credentials if d['kind'] == 'GithubAccessToken' and d['metadata']['name'] == 'github-webhook-api')
        assert webhook_generator['metadata']['namespace'] == 'argocd'
        assert webhook_generator['spec']['permissions'] == {'organization_hooks': 'write', 'actions': 'read'}
        assert webhook_generator['spec']['auth']['privateKey']['secretRef'] == {'name': 'argocd-github-app', 'key': 'private-key'}
        readonly = next(d for d in credentials if d['kind'] == 'GithubAccessToken' and d['metadata']['name'] == 'argocd-github-api')
        assert readonly['spec']['permissions'] == {'actions': 'read', 'contents': 'read', 'pull_requests': 'read', 'statuses': 'write'}
        mirrored = next(d for d in credentials if d['kind'] == 'ExternalSecret' and d['metadata']['name'] == 'github-webhook-api')
        assert mirrored['spec']['refreshInterval'] == '15m'
        assert mirrored['spec']['target']['template']['mergePolicy'] == 'Merge'
        assert mirrored['spec']['target']['template']['metadata']['annotations'] == {
            'reflector.v1.k8s.emberstack.com/reflection-allowed': 'true',
            'reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces': '^cluster-onboarding$',
            'reflector.v1.k8s.emberstack.com/reflection-auto-enabled': 'true',
            'reflector.v1.k8s.emberstack.com/reflection-auto-namespaces': '^cluster-onboarding$',
        }
        delivery = manifests('platform-events')
        receiver = next(d for d in delivery if d['kind'] == 'EventSource' and d['metadata']['name'] == 'github')
        github = receiver['spec']['github']['organization']
        assert github['organizations'] == ['ai-platform-portfolio'] and github['events'] == ['pull_request', 'workflow_run', 'push']
        assert github['apiToken'] == {'name': 'github-webhook-api', 'key': 'token'}
        assert github['webhookSecret'] == {'name': 'github-event-auth', 'key': 'webhook-secret'}
        assert 'githubApp' not in github and github['deleteHookOnFinish'] is False
        assert github['insecure'] is False and github['webhook']['maxPayloadSize'] == 1048576
        assert 'resource' not in receiver['spec']
        startup_source = next(d for d in delivery if d['kind'] == 'EventSource' and d['metadata']['name'] == 'preview-startup')
        assert 'github' not in startup_source['spec']
        assert startup_source['spec']['resource']['startup']['filter']['afterStart'] is False
        hmac = next(d for d in delivery if d['kind'] == 'ExternalSecret')
        assert hmac['spec']['data'] == [{'secretKey': 'webhook-secret', 'remoteRef': {'key': 'cluster-github-webhook-secret'}}]
        refresh = next(d for d in delivery if d['kind'] == 'Sensor' and d['metadata']['name'] == 'preview-refresh')
        dependencies = {d['name']: d for d in refresh['spec']['dependencies']}
        assert dependencies['startup']['eventSourceName'] == 'preview-startup'
        def matches(dependency, payload):
            for rule in dependency['filters']['data']:
                value = payload
                for key in rule['path'].split('.'):
                    value = value.get(key) if isinstance(value, dict) else None
                if not isinstance(value, str) or not any(re.fullmatch(pattern, value) for pattern in rule['value']):
                    return False
            return True
        for action in ['opened', 'reopened', 'synchronize', 'closed']:
            assert matches(dependencies['pr'], {'body': {'X-GitHub-Event': 'pull_request', 'action': action}})
        assert matches(dependencies['publication'], {'body': {'X-GitHub-Event': 'workflow_run', 'action': 'completed'}})
        assert not matches(dependencies['publication'], {'body': {'X-GitHub-Event': 'workflow_run', 'action': 'requested'}})
        assert matches(dependencies['branch-push'], {'body': {'X-GitHub-Event': 'push', 'ref': 'refs/heads/main'}})
        assert matches(dependencies['branch-push'], {'body': {'X-GitHub-Event': 'push', 'ref': 'refs/heads/delivery'}})
        assert not matches(dependencies['branch-push'], {'body': {'X-GitHub-Event': 'push', 'ref': 'refs/heads/feature'}})
        assert not matches(dependencies['pr'], {'body': {'X-GitHub-Event': 'issues', 'action': 'opened'}})
        assert not matches(dependencies['pr'], {'body': {}})
        trigger = refresh['spec']['triggers'][0]
        assert trigger['template']['conditions'] == 'pr || publication || branch-push || startup'
        assert not trigger['template']['k8s'].get('parameters'), 'GitHub payloads must not parameterize Kubernetes commands'
        job = trigger['template']['k8s']['source']['resource']
        assert job['spec']['activeDeadlineSeconds'] == 60 and job['spec']['ttlSecondsAfterFinished'] == 300
        pod = job['spec']['template']['spec']
        assert pod['serviceAccountName'] == 'preview-refresh'
        kubeconfig = next(d for d in delivery if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'preview-refresh-kubeconfig')
        config = yaml.safe_load(kubeconfig['data']['config'])
        assert config['users'][0]['user'] == {'tokenFile': '/var/run/secrets/kubernetes.io/serviceaccount/token'}
        assert config['clusters'][0]['cluster']['certificate-authority'] == '/var/run/secrets/kubernetes.io/serviceaccount/ca.crt'
        assert {'name': 'kubeconfig', 'configMap': {'name': 'preview-refresh-kubeconfig'}} in pod['volumes']
        for container, resource, selector, annotation in [
            (pod['initContainers'][0], 'applicationsets.argoproj.io', 'platform.ai-platform-portfolio/preview=true', 'argocd.argoproj.io/application-set-refresh=true'),
            (pod['initContainers'][1], 'applicationsets.argoproj.io', 'platform.ai-platform-portfolio/environment', 'argocd.argoproj.io/application-set-refresh=true'),
            (pod['initContainers'][2], 'applicationsets.argoproj.io', 'platform.ai-platform-portfolio/onboarding=true', 'argocd.argoproj.io/application-set-refresh=true'),
            (pod['containers'][0], 'applications.argoproj.io', 'platform.ai-platform-portfolio/preview=true', 'argocd.argoproj.io/refresh=hard'),
        ]:
            assert container['args'] == ['annotate', resource, '--namespace=argocd',
                '--selector=' + selector, annotation,
                '--overwrite', '--cache-dir=/tmp', '--request-timeout=15s']
            assert '@sha256:' in container['image'] and container['securityContext']['readOnlyRootFilesystem']
            assert container['env'] == [{'name': 'KUBECONFIG', 'value': '/etc/preview-refresh/config'}]
            assert {'name': 'kubeconfig', 'mountPath': '/etc/preview-refresh', 'readOnly': True} in container['volumeMounts']
        refresh_role = next(d for d in delivery if d['kind'] == 'Role')
        assert refresh_role['metadata']['namespace'] == 'argocd'
        assert refresh_role['rules'] == [{'apiGroups': ['argoproj.io'], 'resources': ['applications', 'applicationsets'], 'verbs': ['get', 'list', 'patch']}]
        listener = next(d for d in delivery if d['kind'] == 'ListenerSet')
        assert listener['metadata']['annotations'] == {'cert-manager.io/cluster-issuer': 'letsencrypt-azuredns'}
        assert not any(d['kind'] == 'Certificate' for d in delivery)
        namespace = next(d for d in manifests('namespaces') if d['kind'] == 'Namespace' and d['metadata']['name'] == 'cluster-onboarding')
        assert namespace['metadata']['labels']['gitops.platform/gateway'] == 'public'
        kargo_namespace = next(d for d in manifests('namespaces') if d['kind'] == 'Namespace' and d['metadata']['name'] == 'kargo')
        assert kargo_namespace['metadata']['labels']['gitops.platform/gateway'] == 'public'
        publisher_namespace = next(d for d in manifests('namespaces') if d['kind'] == 'Namespace' and d['metadata']['name'] == 'foundry-publisher')
        assert publisher_namespace['metadata']['labels']['pod-security.kubernetes.io/enforce'] == 'restricted'
        assert {'group': '', 'kind': 'ConfigMap', 'name': 'preview-refresh', 'namespace': 'cluster-onboarding',
                'jsonPointers': ['/data']} in github_apps['platform-events']['spec']['ignoreDifferences']
        sender = next(app for app in applications if app['metadata']['name'] == 'atlantis')['spec']['source']['helm']['valuesObject']
        assert 'config' not in sender and not any(e['name'] == 'ATLANTIS_WEBHOOK_HTTP_HEADERS' for e in sender['environmentRaw'])
        workflows = yaml.safe_load(sender['repoConfig'])
        assert workflows['repos'][0]['workflow'] == 'platform-onboarding'
        assert workflows['workflows']['platform-onboarding']['apply']['steps'] == [
            'apply', {'run': '/bin/bash /etc/platform-onboarding/publish-signal.sh'}]
        publisher = next(d for d in rendered if d['kind'] == 'Role' and d['metadata']['name'] == 'onboarding-signal-publisher')
        rule = publisher['rules'][0]
        assert rule['resources'] == ['configmaps'] and rule['verbs'] == ['patch']
        assert set(rule['resourceNames']) == {d['metadata']['name'] for d in event_docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] not in {'preview-refresh', 'preview-refresh-kubeconfig'}}
        ignored = [r for r in onboarding_app['spec']['ignoreDifferences'] if r['kind'] == 'ConfigMap']
        assert {r['name'] for r in ignored} == set(rule['resourceNames']) | {'preview-refresh'}
        assert all(r['jsonPointers'] == ['/data'] for r in ignored)
        registry_app = next(app for app in applications if app['metadata']['name'] == 'argocd-registry')
        registry_docs = list(yaml.safe_load_all(registry_app['spec']['source']['helm']['valuesObject']['manifests']))
        assert len(registry_docs) == 1 and registry_docs[0]['kind'] == 'Secret', 'Reuse the bootstrap identity; do not provision a second identity/reloader'
        assert registry_docs[0]['metadata']['labels']['argocd.argoproj.io/secret-type'] == 'repo-creds'
        assert registry_docs[0]['stringData'] == {'url': 'localacrk8s.azurecr.io', 'type': 'helm', 'enableOCI': 'true', 'useAzureWorkloadIdentity': 'true'}
        github_app = next(app for app in applications if app['metadata']['name'] == 'argocd-github')
        github_resources = list(yaml.safe_load_all(github_app['spec']['source']['helm']['valuesObject']['manifests']))
        assert len(github_resources) == 5 and {d['kind'] for d in github_resources} == {'ExternalSecret', 'GithubAccessToken'}
        assert all(d['metadata']['namespace'] == 'argocd' for d in github_resources)
        key_source = next(d for d in github_resources if d['metadata']['name'] == 'argocd-github-app')
        generator = next(d for d in github_resources if d['metadata']['name'] == 'argocd-github-api' and d['kind'] == 'GithubAccessToken')
        token = next(d for d in github_resources if d['kind'] == 'ExternalSecret' and d['metadata']['name'] == 'argocd-github-api')
        assert key_source['spec']['secretStoreRef'] == {'name': 'azure-kv', 'kind': 'ClusterSecretStore'}
        assert key_source['spec']['target']['name'] == 'argocd-github-app'
        assert key_source['spec']['data'] == [{'secretKey': 'private-key', 'remoteRef': {'key': 'ai-platform-clusters-pem-key'}}]
        assert generator['spec']['permissions'] == {'actions': 'read', 'contents': 'read', 'pull_requests': 'read', 'statuses': 'write'}
        assert 'repositories' not in generator['spec'], 'Use the existing installation without per-app access lists'
        assert all(re.fullmatch(r'[0-9]+', generator['spec'][key]) for key in ['appID', 'installID'])
        assert generator['spec']['auth'] == {'privateKey': {'secretRef': {'name': 'argocd-github-app', 'key': 'private-key'}}}
        assert token['spec']['refreshInterval'] == '15m', 'Refresh before the one-hour installation token expiry'
        assert token['spec']['target'] == {'name': 'argocd-github-api'}
        assert token['spec']['dataFrom'] == [{'sourceRef': {'generatorRef': {
            'apiVersion': generator['apiVersion'], 'kind': generator['kind'], 'name': generator['metadata']['name'],
        }}}]
        wave = lambda doc: int(doc['metadata'].get('annotations', {}).get('argocd.argoproj.io/sync-wave', 0))
        assert wave(key_source) < wave(generator) < wave(token)
        staged_config = next(app for app in applications if app['metadata']['name'] == 'configuration')
        staged_resources = list(yaml.safe_load_all(staged_config['spec']['source']['helm']['valuesObject']['manifests']))
        application_aso = next(doc for doc in staged_resources if doc['kind'] == 'Secret'
                               and doc['metadata']['name'] == 'aso-credential'
                               and doc['metadata']['namespace'] == 'azure-resources')
        assert application_aso['type'] == 'Opaque'
        assert {key: base64.b64decode(value).decode() for key, value in application_aso['data'].items()} == {
            'AZURE_CLIENT_ID': runtime['ASO_CLIENT_ID'],
            'AZURE_TENANT_ID': runtime['AZURE_TENANT_ID'],
            'AZURE_SUBSCRIPTION_ID': runtime['AZURE_SUBSCRIPTION_ID'],
            'USE_WORKLOAD_IDENTITY_AUTH': 'true',
        }
        assert not any(doc['kind'] == 'ExternalSecret'
                       and doc['metadata']['name'] == 'aso-credential'
                       and doc['metadata']['namespace'] == 'azure-resources' for doc in staged_resources)
        assert not application_aso['metadata'].get('annotations'), 'ASO credential source must not be reflected'
        assert not application_aso['metadata'].get('labels'), 'ASO credential source must not be reflected'
        health = configmap['data']['resource.customizations.health.external-secrets.io_ExternalSecret']
        assert 'condition.type == "Ready" and condition.status == "True"' in health
        validating_diff = yaml.safe_load(configmap['data']['resource.customizations.ignoreDifferences.admissionregistration.k8s.io_ValidatingWebhookConfiguration'])
        assert validating_diff['jqPathExpressions'] == [
            '.webhooks[]?.clientConfig.caBundle',
            '.webhooks[]?.namespaceSelector.matchExpressions[]? | select((.key == "control-plane" and .operator == "NotIn" and .values == ["true"]) or (.key == "kubernetes.azure.com/managedby" and .operator == "NotIn" and .values == ["aks"]))',
        ]
        database_health = configmap['data']['resource.customizations.health.dbforpostgresql.azure.com_FlexibleServersDatabase']
        assert 'c.observedGeneration == obj.metadata.generation' in database_health
        for kind in ['dbforpostgresql.azure.com_FlexibleServer', 'network.azure.com_PrivateEndpoint',
                     'network.azure.com_PrivateEndpointsPrivateDnsZoneGroup']:
            assert configmap['data']['resource.customizations.health.' + kind] == database_health
        waves = {app['metadata']['name']: int(app['metadata']['annotations']['argocd.argoproj.io/sync-wave'])
                 for app in applications}
        assert waves['namespaces'] < waves['external-secrets'] < waves['configuration'] < waves['testkube-azure']
        assert waves['configuration'] < waves['argocd-github']
        assert waves['argo-events'] < waves['configuration'] < waves['platform-events'] < waves['atlantis']
        assert waves['configuration'] == waves['azure-service-operator']
        assert waves['crossplane-identity-api'] < waves['kargo-identity'] < waves['kargo'] < waves['routes']
        assert waves['azure-service-operator'] < waves['kargo-acr'] < waves['kargo']
        assert waves['azure-service-operator'] < waves['foundry-publisher']
        atlas_token = next(doc for doc in staged_resources if doc['kind'] == 'ExternalSecret' and doc['metadata']['name'] == 'atlas-token-secret')
        assert atlas_token['metadata']['namespace'] == 'atlas-system'
        assert atlas_token['spec']['secretStoreRef'] == {'name': 'azure-kv', 'kind': 'ClusterSecretStore'}
        assert atlas_token['spec']['data'] == [{'secretKey': 'ATLAS_TOKEN', 'remoteRef': {'key': 'ai-platform-portfolio-atlas-bot'}}]
        assert atlas_token['spec']['target']['name'] == 'atlas-token-secret'
        atlas_app = next(app for app in applications if app['metadata']['name'] == 'atlas-operator')
        assert int(staged_config['metadata']['annotations']['argocd.argoproj.io/sync-wave']) < int(atlas_app['metadata']['annotations']['argocd.argoproj.io/sync-wave'])
        assert waves['configuration'] < waves['postgres'] < waves['atlas-database-access'] < waves['atlas-operator']
        assert waves['azure-service-operator'] < waves['atlas-azure'] < waves['atlas-operator']
        azure = next(app for app in applications if app['metadata']['name'] == 'atlas-azure')
        azure_docs = list(yaml.safe_load_all(azure['spec']['source']['helm']['valuesObject']['manifests']))
        assert {d['kind'] for d in azure_docs} == {'UserAssignedIdentity', 'FederatedIdentityCredential', 'FlexibleServersAdministrator'}
        admin = next(d for d in azure_docs if d['kind'] == 'FlexibleServersAdministrator')
        assert admin['spec']['owner']['armId'].endswith('/flexibleServers/pg-ai-platform-portfolio-management')
        assert admin['spec']['azureNameFromConfig'] == {'name': 'atlas-postgres-identity', 'key': 'principalId'}
        bootstrap_secret = next(d for d in staged_resources if d['kind'] == 'ExternalSecret'
                                and d['metadata']['name'] == 'atlas-role-manager')
        assert bootstrap_secret['metadata']['namespace'] == 'postgres'
        assert bootstrap_secret['spec']['refreshPolicy'] == 'CreatedOnce'
        assert bootstrap_secret['spec']['target']['immutable'] is True
        access = next(a for a in applications if a['metadata']['name'] == 'atlas-database-access')
        access_docs = list(yaml.safe_load_all(access['spec']['source']['helm']['valuesObject']['manifests']))
        readers = {d['metadata']['name']: d for d in access_docs if d['kind'] == 'Role'}
        assert readers['atlas-secret-reader']['rules'] == [{'apiGroups': [''], 'resources': ['secrets'],
            'resourceNames': ['atlas-postgres-client'], 'verbs': ['get']}]
        assert readers['atlas-credentials']['rules'] == [{'apiGroups': [''], 'resources': ['secrets'],
            'resourceNames': ['atlas-token-secret', 'atlas-postgres'], 'verbs': ['get']}]
        connection = next(d for d in access_docs if d['kind'] == 'ExternalSecret')
        url = connection['spec']['target']['template']['data']['url']
        assert '@platform-rw.postgres.svc.cluster.local:5432/postgres?' in url
        assert 'sslmode=verify-full&sslrootcert=/etc/atlas-postgres/ca.crt' in url
        assert 'sslcert=/etc/atlas-postgres/tls.crt&sslkey=/etc/atlas-postgres/tls.key' in url
        assert url.startswith('postgres://atlas_role_manager@') and 'password' not in connection['spec']['target']['template']['data']
        assert {d['remoteRef']['key'] for d in connection['spec']['data']} == {'atlas-postgres-client'}
        assert {d['remoteRef']['property'] for d in connection['spec']['data']} == {'tls.crt', 'tls.key', 'ca.crt'}
        assert set(connection['spec']['target']['template']['data']) == {'url', 'tls.crt', 'tls.key', 'ca.crt'}
        for kind in ['AtlasSchema', 'AtlasMigration']:
            health = configmap['data']['resource.customizations.health.db.atlasgo.io_' + kind]
            assert 'status.observedGeneration ~= obj.metadata.generation' in health
            assert 'condition.observedGeneration == obj.metadata.generation' in health
        staged_egress = next(doc for doc in staged_resources if doc['kind'] == 'NetworkPolicy' and doc['metadata']['name'] == 'holmes-egress')
        assert not any('ipBlock' in peer for rule in staged_egress['spec']['egress'] for peer in rule['to']), 'Staged Holmes policy must contain no unverified IP destinations'
        values.write_text(json.dumps({
            'runtime': runtime,
            'holmes': {
                'enabled': True,
                'model': 'azure/test',
                'modelEndpoint': 'https://model.example.test/v1',
                'modelApiVersion': '2025-04-01-preview',
                'modelCidrs': ['10.1.2.5/32'],
                'apiCidrs': ['10.1.0.5/32'],
                'entraCidrs': ['10.1.3.5/32'],
                'azure': {
                    'location': 'uksouth',
                    'foundryResourceGroupName': 'rg-foundry',
                    'foundryResourceName': 'foundry-test',
                },
            },
        }))
        with_holmes = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))))
        holmes = next(app for app in with_holmes if app['metadata']['name'] == 'holmes')
        assert holmes['spec']['source']['targetRevision'] == '0.45.0'
        assert holmes['spec']['source']['helm']['valuesObject']['enableTelemetry'] is False
        assert holmes['spec']['source']['helm']['valuesObject']['operator']['enabled'] is False
        assert holmes['spec']['source']['helm']['valuesObject']['modelList']['approved']['model'] == 'azure/test'
        assert 'api_key' not in holmes['spec']['source']['helm']['valuesObject']['modelList']['approved']
        holmes_azure = next(app for app in with_holmes if app['metadata']['name'] == 'holmes-azure')
        azure_resources = list(yaml.safe_load_all(holmes_azure['spec']['source']['helm']['valuesObject']['manifests']))
        assert {'UserAssignedIdentity', 'FederatedIdentityCredential', 'RoleAssignment'} == {resource['kind'] for resource in azure_resources}
        holmes_identity = next(resource for resource in azure_resources if resource['kind'] == 'UserAssignedIdentity')
        assert holmes_identity['spec']['owner']['armId'].endswith('/resourceGroups/example')
        config_app = next(app for app in with_holmes if app['metadata']['name'] == 'configuration')
        holmes_resources = list(yaml.safe_load_all(config_app['spec']['source']['helm']['valuesObject']['manifests']))
        holmes_role = next(resource for resource in holmes_resources if resource['kind'] == 'ClusterRole' and resource['metadata']['name'] == 'holmes-read')
        assert all(rule['verbs'] == ['get', 'list', 'watch'] for rule in holmes_role['rules'])
        assert not any('secrets' in rule['resources'] for rule in holmes_role['rules'])
        assert {'aso-credential', 'holmes-api-token', 'holmes-egress', 'holmes-ingress'} <= {
            resource['metadata']['name'] for resource in holmes_resources
        }
        aso_credential = next(resource for resource in holmes_resources if resource['metadata']['name'] == 'aso-credential')
        assert aso_credential['kind'] == 'Secret'
        assert base64.b64decode(aso_credential['data']['AZURE_CLIENT_ID']).decode() == runtime['ASO_CLIENT_ID']
        values.write_text(json.dumps({'runtime': runtime, 'sonarqubeReady': True, 'atlantis': atlantis}))
        for app_name, workload in [('testkube-storage', 'testkube-s3'), ('testkube', 'testkube-api-server'), ('testkube-nats', 'testkube-nats')]:
            app = next(a for a in applications if a['metadata']['name'] == app_name)
            assert any(rule.get('name') == workload and rule.get('jsonPointers') == ['/spec/template/metadata/annotations/reloader.stakater.com~1last-reloaded-from'] for rule in app['spec']['ignoreDifferences'])
        langfuse_app = next(a for a in applications if a['metadata']['name'] == 'langfuse')
        reload_rule = next(r for r in langfuse_app['spec']['ignoreDifferences'] if r.get('name') == 'langfuse-web')
        assert reload_rule == {'group': 'apps', 'kind': 'Deployment', 'name': 'langfuse-web', 'namespace': 'langfuse', 'jsonPointers': ['/spec/template/metadata/annotations/reloader.stakater.com~1last-reloaded-from']}
        assert 'RespectIgnoreDifferences=true' in langfuse_app['spec']['syncPolicy']['syncOptions']
        waves = {app['metadata']['name']: int(app['metadata']['annotations']['argocd.argoproj.io/sync-wave'])
                 for app in applications}
        assert waves['postgres'] < waves['langfuse-credentials'] < waves['langfuse']
        assert waves['cert-manager'] < waves['redis-certificates'] < waves['redis'] < waves['langfuse']
        assert waves['reflector'] < waves['redis-certificates'] and waves['redis-reloader'] < waves['redis']
        redis_app = next(a for a in applications if a['metadata']['name'] == 'redis')
        redis_docs = list(yaml.safe_load_all(redis_app['spec']['source']['helm']['valuesObject']['manifests']))
        assert {d['kind'] for d in redis_docs} == {'ServiceAccount', 'Service', 'ConfigMap', 'StatefulSet'}
        service = next(d for d in redis_docs if d['kind'] == 'Service')
        assert service['spec']['type'] == 'ClusterIP' and service['spec']['ports'][0]['port'] == 6379
        statefulset = next(d for d in redis_docs if d['kind'] == 'StatefulSet')
        pod = statefulset['spec']['template']['spec']
        assert pod['automountServiceAccountToken'] is False
        assert pod['nodeSelector'] == {'agentpool': 'platform'}
        assert pod['securityContext']['runAsNonRoot'] and pod['securityContext']['fsGroup'] == 1000
        assert pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
        container = pod['containers'][0]
        assert 'redis:8.10.3-alpine@sha256:' in container['image']
        assert container['securityContext'] == {'allowPrivilegeEscalation': False, 'readOnlyRootFilesystem': True, 'capabilities': {'drop': ['ALL']}}
        assert container['resources']['limits']['memory'] == '1Gi'
        assert statefulset['spec']['persistentVolumeClaimRetentionPolicy'] == {'whenDeleted': 'Retain', 'whenScaled': 'Retain'}
        assert all(v.get('secret', {}).get('secretName') != 'redis-ca' for v in pod['volumes'])
        for probe in ['startupProbe', 'readinessProbe', 'livenessProbe']:
            check = container[probe]['exec']['command'][-1]
            assert '--tls' in check and '--cacert' in check and '--cert' in check and '--key' in check
            assert '--insecure' not in check and '= PONG' in check
        config = next(d for d in redis_docs if d['kind'] == 'ConfigMap')['data']['redis.conf']
        assert all(line in config.splitlines() for line in ['port 0', 'tls-auth-clients yes', 'tls-auth-clients-user CN',
            'maxmemory-policy noeviction', 'appendonly yes', 'appendfsync everysec', 'user default reset off',
            'user redis-probe reset on resetpass +ping'])
        assert '~langfuse:* &langfuse:*' in config and '-@dangerous -@admin' in config and 'nopass' not in config
        certificate_app = next(a for a in applications if a['metadata']['name'] == 'redis-certificates')
        certs = list(yaml.safe_load_all(certificate_app['spec']['source']['helm']['valuesObject']['manifests']))
        leaves = [d for d in certs if d['kind'] == 'Certificate' and not d['spec'].get('isCA')]
        assert len(leaves) == 3
        for leaf in leaves:
            assert leaf['spec']['duration'] == '720h' and leaf['spec']['renewBefore'] == '168h'
            assert leaf['spec']['privateKey']['rotationPolicy'] == 'Always'
            template = leaf['spec'].get('secretTemplate', {})
            if leaf['metadata']['name'] == 'langfuse-redis':
                assert leaf['spec']['commonName'] == 'langfuse'
                assert template['annotations']['reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces'] == '^langfuse$'
                assert template['annotations']['reflector.v1.k8s.emberstack.com/reflection-auto-namespaces'] == '^langfuse$'
            else:
                assert not template, 'Never reflect the server or probe identity'
        for enabled, shared in [(False, False), (True, False), (True, True)]:
            values.write_text(json.dumps({'runtime': runtime, 'redis': {'enabled': enabled}, 'langfuse': {'redis': {'useShared': shared}}}))
            variants = [d for d in yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))) if d]
            names_variant = {d['metadata']['name'] for d in variants}
            assert ('redis' in names_variant) == enabled
            variant = next(d for d in variants if d['metadata']['name'] == 'langfuse')
            inputs = variant['spec']['source']['helm']['valuesObject']
            assert inputs['redis'].get('deploy', True) != shared
            if not shared:
                continue
            assert inputs['redis']['auth'] == {'username': '', 'password': '', 'existingSecret': '', 'usersExistingSecret': ''}
            tls = inputs['redis']['tls']
            assert tls['enabled'] and tls['keyPath'] == '/etc/langfuse-redis/tls.key'
            values.write_text(json.dumps(inputs))
            rendered_shared = [d for d in yaml.safe_load_all(command('helm', 'template', 'langfuse', '--repo', variant['spec']['source']['repoURL'],
                '--version', variant['spec']['source']['targetRevision'], '--namespace', 'langfuse',
                '--api-versions', 'clickhouse.com/v1alpha1/ClickHouseCluster', '--api-versions', 'clickhouse.com/v1alpha1/KeeperCluster', '-f', str(values))) if d]
            assert not any('redis' in d['metadata']['name'] and d['kind'] in ['Deployment', 'StatefulSet', 'PersistentVolumeClaim', 'Secret'] for d in rendered_shared)
            for d in rendered_shared:
                if d['kind'] != 'Deployment' or d['metadata']['name'] not in ['langfuse-web', 'langfuse-worker']:
                    continue
                client_pod = d['spec']['template']['spec']
                assert client_pod['securityContext']['fsGroup'] == 1000
                envs = {e['name']: e for e in client_pod['containers'][0]['env']}
                assert envs['REDIS_CONNECTION_STRING']['value'] == 'rediss://redis.redis.svc.cluster.local:6379/0'
                assert envs['REDIS_KEY_PREFIX']['value'] == 'langfuse:'
                assert envs['REDIS_TLS_ENABLED']['value'] == 'true'
                assert envs['REDIS_TLS_CA_PATH']['value'] == '/etc/langfuse-redis/ca.crt'
                assert envs['REDIS_TLS_CERT_PATH']['value'] == '/etc/langfuse-redis/tls.crt'
                assert not {'REDIS_PASSWORD', 'REDIS_AUTH'} & envs.keys()
                assert 'langfuse-redis' in d['metadata']['annotations']['secret.reloader.stakater.com/reload'].split(',')
        values.write_text(json.dumps({'runtime': runtime, 'redis': {'enabled': False}, 'langfuse': {'redis': {'useShared': True}}}))
        invalid = subprocess.run(['helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values)], text=True, capture_output=True)
        assert invalid.returncode and 'langfuse.redis.useShared requires redis.enabled' in invalid.stderr
        assert waves['configuration'] < waves['external-dns'] < min(waves[n] for n in ['platform-events', 'gateway', 'argocd-route', 'routes'])
        database_values = langfuse_app['spec']['source']['helm']['valuesObject']
        assert database_values['postgresql']['deploy'] is False
        assert database_values['clickhouse']['cluster']['image']['tag'] == '26.4'
        assert database_values['clickhouse']['keeper']['image']['tag'] == '26.4'
        credentials_app = next(a for a in applications if a['metadata']['name'] == 'langfuse-credentials')
        credentials_docs = list(yaml.safe_load_all(credentials_app['spec']['source']['helm']['valuesObject']['manifests']))
        reader = next(d for d in credentials_docs if d['kind'] == 'Role')
        assert reader['rules'][0]['resourceNames'] == ['langfuse-postgres-client', 'platform-ca']
        assert reader['rules'][0]['verbs'] == ['get']
        delivered = next(d for d in credentials_docs if d['kind'] == 'ExternalSecret')
        assert {d['secretKey'] for d in delivered['spec']['data']} == {'keystore.p12', 'ca.crt'}
        bootstrap_secret = next(d for d in credentials_docs if d['kind'] == 'ExternalSecret' and d['metadata']['name'] == 'langfuse-otel')
        target = bootstrap_secret['spec']['target']
        assert bootstrap_secret['spec']['refreshPolicy'] == 'CreatedOnce'
        assert target['immutable'] is True and target['creationPolicy'] == 'Orphan'
        annotations = target['template']['metadata']['annotations']
        assert annotations['reflector.v1.k8s.emberstack.com/reflection-allowed-namespaces'] == '^opentelemetry$'
        assert annotations['reflector.v1.k8s.emberstack.com/reflection-auto-namespaces'] == '^opentelemetry$'
        assert target['template']['data']['authorization'] == 'Basic {{ printf "pk-lf-%s:sk-lf-%s" (.password | sha256sum) .password | b64enc }}'
        assert target['template']['data']['public-key'] == '{{ printf "pk-lf-%s" (.password | sha256sum) }}'
        assert target['template']['data']['secret-key'] == '{{ printf "sk-lf-%s" .password }}'
        assert waves['langfuse-credentials'] < waves['langfuse'] < waves['opentelemetry-collector']
        for app in applications:
            assert re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', app['spec']['destination']['namespace']), 'Destination namespace must be a DNS label'
            assert app['spec']['destination']['server'] == 'https://kubernetes.default.svc', 'Packages must reconcile locally'
            source = app['spec'].get('source') or app['spec']['sources'][0]
            if 'path' in source:
                v = tmp / (app['metadata']['name'] + '.json')
                v.write_text(json.dumps(source['helm']['valuesObject']))
                documents = list(yaml.safe_load_all(command('helm', 'template', app['metadata']['name'],
                    str(ROOT / source['path']), '-f', str(v))))
                for doc in documents:
                    assert doc and 'fluxcd.io' not in doc['apiVersion'], 'Flux resource survived migration'
                    assert doc['kind'] != 'Certificate' or all('*' not in host for host in doc['spec'].get('dnsNames', [])), 'Wildcard certificates are forbidden'
                if app['metadata']['name'] == 'postgres':
                    postgres_documents = documents
                    cluster = next(d for d in documents if d['kind'] == 'Cluster')
                    langfuse_role = next(r for r in cluster['spec']['managed']['roles'] if r['name'] == 'langfuse')
                    assert not langfuse_role.get('passwordSecret') and langfuse_role['superuser'] is False
                    assert 'hostssl langfuse langfuse all cert' in cluster['spec']['postgresql']['pg_hba']
                    database = next(d for d in documents if d['kind'] == 'Database' and d['metadata']['name'] == 'langfuse')
                    assert database['spec']['owner'] == 'langfuse'
                    certificate = next(d for d in documents if d['kind'] == 'Certificate' and d['metadata']['name'] == 'langfuse-postgres-client')
                    assert certificate['spec']['commonName'] == 'langfuse'
                    assert certificate['spec']['privateKey']['rotationPolicy'] == 'Always'
                    assert certificate['spec']['keystores']['pkcs12']['create'] is True
                    role = next(r for r in cluster['spec']['managed']['roles'] if r['name'] == 'sonarqube')
                    assert role['passwordSecret']['name'] == 'sonarqube-role'
                    assert cluster['spec']['instances'] == 1
                    assert 'synchronous' not in cluster['spec']['postgresql']
                    assert next(d for d in documents if d['kind'] == 'Pooler')['spec']['instances'] == 1
                    assert cluster['spec']['bootstrap'] == {'initdb': {'database': 'app', 'owner': 'app'}}
                    assert 'externalClusters' not in cluster['spec'], 'Portfolio teardown has no automatic restore'
                    assert len([d for d in documents if d['kind'] == 'ObjectStore']) == 1
                    assert role['login'] and not any(role[k] for k in ['superuser', 'createdb', 'createrole', 'replication'])
                    database = next(d for d in documents if d['kind'] == 'Database')
                    assert database['spec']['owner'] == 'sonarqube' and database['spec']['databaseReclaimPolicy'] == 'retain'
                    manager_role = next(r for r in cluster['spec']['managed']['roles'] if r['name'] == 'atlas_role_manager')
                    assert manager_role['createrole'] is True and manager_role['createdb'] is False
                    assert manager_role['superuser'] is False and manager_role['replication'] is False
                    assert manager_role['passwordSecret'] == {'name': 'atlas-role-manager'}
                    hba = cluster['spec']['postgresql']['pg_hba']
                    assert 'hostssl postgres atlas_role_manager all cert map=atlas_clients' in hba
                    assert hba[-3:] == [
                        'hostssl samerole /^app_[a-z0-9_]+_migrator$ all cert map=atlas_clients',
                        'hostssl samerole /^app_[a-z0-9_]+_app$ all cert', 'host all all all reject']
                    assert hba[0] == 'hostnossl all all all reject'
                    assert cluster['spec']['postgresql']['pg_ident'] == [
                        'atlas_clients atlas-operator atlas_role_manager',
                        'atlas_clients atlas-operator /^app_[a-z0-9_]+_migrator$']
                    pki = {d['kind'] + '/' + d['metadata']['name']: d for d in documents
                           if d['metadata']['name'] in {'postgres-client-ca-reader', 'postgres-client-ca', 'postgres-client', 'atlas-postgres-client'}}
                    assert all(d['metadata']['namespace'] == 'postgres' for d in pki.values())
                    assert pki['Role/postgres-client-ca-reader']['rules'] == [{'apiGroups': [''], 'resources': ['secrets'],
                        'resourceNames': ['platform-ca'], 'verbs': ['get']}]
                    adapter = pki['ExternalSecret/postgres-client-ca']['spec']
                    assert adapter['refreshInterval'] == '1m'
                    assert adapter['target']['template']['type'] == 'kubernetes.io/tls'
                    assert adapter['target']['template']['data'] == {
                        'tls.crt': '{{ index . "ca.crt" }}', 'tls.key': '{{ index . "ca.key" }}'}
                    assert {d['remoteRef']['key'] for d in adapter['data']} == {'platform-ca'}
                    assert pki['Issuer/postgres-client']['spec']['ca'] == {'secretName': 'postgres-client-ca'}
                    leaf = pki['Certificate/atlas-postgres-client']
                    assert leaf['spec']['commonName'] == 'atlas-operator'
                    assert leaf['spec']['issuerRef'] == {'name': 'postgres-client', 'kind': 'Issuer'}
                    assert leaf['spec']['usages'] == ['digital signature', 'client auth']
                    assert leaf['spec']['privateKey']['rotationPolicy'] == 'Always'
                    assert leaf['spec']['duration'] == '720h' and leaf['spec']['renewBefore'] == '168h'
                    assert int(pki['Issuer/postgres-client']['metadata']['annotations']['argocd.argoproj.io/sync-wave']) < int(leaf['metadata']['annotations']['argocd.argoproj.io/sync-wave'])
                    assert not any(d['kind'] in {'DatabaseRole', 'AtlasSchema', 'AtlasMigration'} for d in pki.values()), 'PKI must not take ownership of preview roles'
                    testing_role = next(r for r in cluster['spec']['managed']['roles'] if r['name'] == 'testkube')
                    assert testing_role['passwordSecret']['name'] == 'testkube-role'
                    assert not any(testing_role[k] for k in ['superuser', 'createdb', 'createrole', 'replication'])
                    assert 'hostssl testkube testkube all scram-sha-256' in cluster['spec']['postgresql']['pg_hba']
                if app['metadata']['name'] == 'atlantis-azure':
                    assert {d['kind'] for d in documents} == {'ResourceGroup', 'UserAssignedIdentity', 'FederatedIdentityCredential', 'RoleAssignment'}
                    group = next(d for d in documents if d['kind'] == 'ResourceGroup')
                    assert group['spec']['azureName'] == 'rg-management-atlantis'
                    identity = next(d for d in documents if d['kind'] == 'UserAssignedIdentity')
                    assert identity['spec']['owner'] == {'name': 'atlantis'}
                    assert identity['metadata']['namespace'] == 'atlantis'
                    assert identity['spec']['operatorSpec']['configMaps']['clientId'] == {'name': 'atlantis-identity', 'key': 'clientId'}
                    federation = next(d for d in documents if d['kind'] == 'FederatedIdentityCredential')
                    assert federation['spec']['owner'] == {'name': 'atlantis'}
                    assert federation['spec']['subject'] == 'system:serviceaccount:atlantis:atlantis'
                    assert federation['spec']['issuer'] == runtime['AKS_OIDC_ISSUER_URL']
                    roles = [d for d in documents if d['kind'] == 'RoleAssignment']
                    assert len(roles) == 4
                    assert {r['spec']['roleDefinitionReference']['wellKnownName'] for r in roles} == {'Contributor', 'Storage Blob Data Contributor', 'Role Based Access Control Administrator', 'Key Vault Secrets User'}
                    assert all(r['spec']['principalIdFromConfig'] == {'name': 'atlantis-identity', 'key': 'principalId'} for r in roles)
                    assert all(re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', r['spec']['azureName']) for r in roles)
                    state_role = next(r for r in roles if r['metadata']['name'] == 'atlantis-state')
                    assert state_role['spec']['owner']['armId'].endswith('/resourceGroups/rg-state/providers/Microsoft.Storage/storageAccounts/examplestate')
                    assert not any('/containers/' in r['spec']['owner']['armId'] for r in roles)
                if app['metadata']['name'] == 'configuration':
                    secrets = {d['metadata']['name']: d for d in documents if d['kind'] == 'ExternalSecret'}
                    assert {'atlantis-github', 'atlantis-web'} <= secrets.keys()
                    credential = next(d for d in documents if d['kind'] == 'Secret' and d['metadata']['name'] == 'aso-credential' and d['metadata']['namespace'] == 'atlantis')
                    assert base64.b64decode(credential['data']['AZURE_CLIENT_ID']).decode() == runtime['ASO_CLIENT_ID']
                    assert base64.b64decode(credential['data']['USE_WORKLOAD_IDENTITY_AUTH']).decode() == 'true'
                    reader = next(d for d in documents if d['kind'] == 'Role' and d['metadata']['name'] == 'atlantis-azure-readiness')
                    assert all(set(r['verbs']) == {'get', 'list', 'watch'} and r['resourceNames'] for r in reader['rules'])
                    assert not any('secrets' in r['resources'] for r in reader['rules'])
                    assert {d['secretKey'] for d in secrets['atlantis-github']['spec']['data']} == {'key.pem', 'github_secret'}
                    assert {d['secretKey'] for d in secrets['atlantis-web']['spec']['data']} == {'username', 'password'}
                    assert {'testkube-role', 'testkube-storage'} <= secrets.keys()
                    for name in ['testkube-role', 'testkube-storage']:
                        assert secrets[name]['spec']['refreshPolicy'] == 'CreatedOnce'
                        assert secrets[name]['spec']['target']['immutable'] is True
                        assert '{{ .password }}' in json.dumps(secrets[name]['spec']['target']['template']['data'])
                if app['metadata']['name'] == 'testkube-storage':
                    reader = next(d for d in documents if d['kind'] == 'Role')
                    assert reader['rules'] == [{'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': ['testkube-role', 'platform-ca'], 'verbs': ['get']}]
                    secret = next(d for d in documents if d['kind'] == 'ExternalSecret')
                    data = secret['spec']['target']['template']['data']
                    assert 'sslmode=verify-full' in data['dsn'] and 'sslrootcert=' in data['dsn']
                    assert '{{ .password }}' in data['dsn']
                if app['metadata']['name'] == 'testkube-analysis':
                    template = next(d for d in documents if d['kind'] == 'ClusterAnalysisTemplate')
                    metric = template['spec']['metrics'][0]
                    job = metric['provider']['job']['spec']
                    assert metric['failureLimit'] == 0 and job['backoffLimit'] == 0 and job['activeDeadlineSeconds'] == 900
                    assert job['template']['spec']['automountServiceAccountToken'] is False
                    container = job['template']['spec']['containers'][0]
                    assert '--watch' in container['args'] and '--client=direct' in container['args']
                    assert '{{args.workflow}}' in container['args'] and '@sha256:' in container['image']
                if app['metadata']['name'] == 'crossplane-packages':
                    provider = next(d for d in documents if d['kind'] == 'Provider')
                    assert provider['spec']['package'].endswith(':v2.5.0')
                    activations = yaml.safe_load((HELM_CHART / 'platform/packages/crossplane/values.yaml').read_text())['provider']['defaultActivations']
                    policy = next(d for d in documents if d['kind'] == 'ManagedResourceActivationPolicy')
                    assert policy['metadata']['name'] == 'default' and policy['spec']['activate'] == activations
                    config = next(d for d in documents if d['kind'] == 'DeploymentRuntimeConfig')
                    assert config['spec']['serviceAccountTemplate']['metadata']['name'] == 'provider-azuread'
                    projection = config['spec']['deploymentTemplate']['spec']['template']['spec']['volumes'][0]['projected']['sources'][0]['serviceAccountToken']
                    assert projection['audience'] == 'api://AzureADTokenExchange'
                if app['metadata']['name'] == 'crossplane-identity-api':
                    config = next(d for d in documents if d['kind'] == 'ClusterProviderConfig')
                    assert config['spec']['credentials'] == {'source': 'OIDCTokenFile'}
                    assert config['spec']['oidcTokenFilePath'] == '/var/run/secrets/crossplane/azure/token'
                    definition = next(d for d in documents if d['kind'] == 'CompositeResourceDefinition')
                    assert definition['spec']['scope'] == 'Namespaced'
                    composition = next(d for d in documents if d['kind'] == 'Composition')
                    resources = composition['spec']['pipeline'][0]['input']['resources']
                    assert {d['base']['kind'] for d in resources} == {'Application', 'Principal', 'Password'}
                    principal = next(d for d in resources if d['name'] == 'principal')
                    assert principal['base']['spec']['forProvider']['appRoleAssignmentRequired'] is True
                    details = [v for d in resources for v in d.get('connectionDetails', [])]
                    assert {d['name'] for d in details} == {'client-id', 'client-secret', 'tenant-id'}
                    assert next(d for d in details if d['name'] == 'client-secret')['fromConnectionSecretKey'] == 'attribute.value'
                if app['metadata']['name'] == 'service-identities':
                    identity = next(d for d in documents if d['kind'] == 'OIDCApplication')
                    assert identity['spec']['redirectUri'] == 'https://langfuse.example.test/api/auth/callback/azure-ad'
                    assignments = [d for d in documents if d['kind'] == 'RoleAssignment' and d['metadata']['namespace'] == 'langfuse']
                    assert assignments and all(d['spec']['forProvider']['resourceObjectIdRef']['name'] == 'langfuse-sso' for d in assignments)
                if app['metadata']['name'] == 'sonarqube-configure':
                    job = next(d for d in documents if d['kind'] == 'Job')
                    annotations = job['metadata'].get('annotations', {})
                    assert 'argocd.argoproj.io/hook' not in annotations, 'A hook-only Application can appear synced without running its configuration'
                    assert re.fullmatch(r'sonarqube-configure-[0-9a-f]{12}', job['metadata']['name'])
                    script = job['spec']['template']['spec']['containers'][0]['command'][2]
                    subprocess.run(['sh', '-n'], input=script, text=True, check=True)
                    assert 'sonar.auth.saml.enabled true' in script and 'sonar.forceAuthentication true' in script
                    assert 'api/settings/values?keys=sonar.auth.saml.enabled' in script
                if app['metadata']['name'] == 'argocd-route':
                    listener = next(d for d in documents if d['kind'] == 'ListenerSet')
                    assert listener['spec']['listeners'][0]['hostname'] == 'argocd-management.example.test'
                    assert listener['metadata']['annotations']['cert-manager.io/cluster-issuer'] == 'letsencrypt-azuredns'
                    assert not any(d['kind'] == 'Certificate' for d in documents)
                    route = next(d for d in documents if d['kind'] == 'HTTPRoute' and d['metadata']['name'] == 'argocd')
                    assert route['spec']['rules'][0]['backendRefs'] == [{'name': 'argocd-server', 'port': 80}]
                    assert route['spec']['parentRefs'][0]['kind'] == 'ListenerSet'
                if app['metadata']['name'] == 'gateway':
                    gateway = next(d for d in documents if d['kind'] == 'Gateway')
                    assert gateway['spec']['allowedListeners']['namespaces']['from'] == 'Selector'
                    assert gateway['metadata']['annotations']['cert-manager.io/cluster-issuer'] == 'letsencrypt-azuredns'
                    http, https = gateway['spec']['listeners']
                    assert http['name'] == 'http' and http['protocol'] == 'HTTP' and http['port'] == 80
                    assert https == {
                        'name': 'preview-https', 'hostname': '*.previews.example.test',
                        'protocol': 'HTTPS', 'port': 443,
                        'tls': {'mode': 'Terminate', 'certificateRefs': [{'name': 'preview-tls'}]},
                        'allowedRoutes': {'namespaces': {'from': 'Selector', 'selector': {'matchLabels': {
                            'gitops.platform/gateway': 'public', 'gitops.platform/preview': 'true',
                        }}}},
                    }
                if app['metadata']['name'] == 'routes':
                    listeners = [d for d in documents if d['kind'] == 'ListenerSet']
                    assert len(listeners) == 7
                    kargo_route = next(d for d in documents if d['kind'] == 'HTTPRoute' and d['metadata']['name'] == 'kargo')
                    assert kargo_route['spec']['rules'][0]['backendRefs'] == [{'name': 'kargo-api', 'port': 80}]
                    portal_route = next(d for d in documents if d['kind'] == 'HTTPRoute' and d['metadata']['name'] == 'backstage')
                    assert portal_route['spec']['rules'][0]['backendRefs'] == [{'name': 'backstage', 'port': 7007}]
                    route = next(d for d in documents if d['kind'] == 'HTTPRoute' and d['metadata']['name'] == 'atlantis')
                    assert route['spec']['rules'][0]['backendRefs'] == [{'name': 'atlantis', 'port': 80}]
                    assert not any(d['kind'] == 'Certificate' for d in documents), 'cert-manager owns ListenerSet certificates'
                    for listener in listeners:
                        assert listener['metadata']['annotations']['cert-manager.io/cluster-issuer'] == 'letsencrypt-azuredns'
                        for entry in listener['spec']['listeners']:
                            assert '*' not in entry['hostname'] and entry['tls']['certificateRefs'][0]['name']
                    for doc in documents:
                        if doc['kind'] == 'HTTPRoute' and doc['metadata']['name'] != 'https-redirect':
                            assert doc['spec']['parentRefs'][0]['kind'] == 'ListenerSet'

        values.write_text(json.dumps({'runtime': runtime, 'cluster': {'name': 'dev', 'role': 'workload'}, 'atlantis': atlantis}))
        workload = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))))
        workload = [d for d in workload if d and d['kind'] == 'Application']
        workload_names = {a['metadata']['name'] for a in workload}
        assert workload_names == expected - {'backstage', 'backstage-credentials', 'backstage-reloader',
                                              'sonarqube', 'sonarqube-configure', 'crossplane',
                                              'crossplane-packages', 'crossplane-identity-api', 'argocd-route', 'argo-events', 'platform-events',
                                              'kargo-acr', 'kargo-identity', 'kargo', 'foundry-publisher'}, 'Workload must install every ready package except management services'
        assert {'atlantis', 'atlantis-azure', 'holmes', 'holmes-azure', 'azure-service-operator',
                'service-identities',
                'langfuse-reloader', 'federation', 'testkube', 'devlake'} <= workload_names
        assert not {'backstage', 'backstage-credentials', 'backstage-reloader'} & workload_names
        assert waves['configuration'] < waves['atlantis-azure'] < waves['atlantis'] < waves['routes']
        assert waves['azure-service-operator'] < waves['atlantis-azure']
        assert waves['crossplane'] < waves['crossplane-packages'] < waves['crossplane-identity-api'] < waves['service-identities'] < waves['langfuse']
        workload_namespaces = next(a for a in workload if a['metadata']['name'] == 'namespaces')
        assert 'azureserviceoperator-system' in workload_namespaces['spec']['source']['helm']['valuesObject']['manifests']
        workload_azure = next(a for a in workload if a['metadata']['name'] == 'holmes-azure')
        workload_azure_docs = [d for d in yaml.safe_load_all(workload_azure['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert next(d for d in workload_azure_docs if d['kind'] == 'UserAssignedIdentity')['spec']['owner']['armId'].endswith('/resourceGroups/example')
        workload_atlantis = next(a for a in workload if a['metadata']['name'] == 'atlantis-azure')
        workload_atlantis_docs = [d for d in yaml.safe_load_all(workload_atlantis['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert next(d for d in workload_atlantis_docs if d['kind'] == 'ResourceGroup')['spec']['azureName'] == 'rg-dev-atlantis'
        assert not any(a['metadata']['name'].startswith('sonarqube') for a in workload), 'Sonar must wait for cluster credential preparation'
        initial_postgres = next(a for a in workload if a['metadata']['name'] == 'postgres')
        initial_database_docs = [d for d in yaml.safe_load_all(initial_postgres['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert not any(d['kind'] == 'Database' and d['metadata']['name'] == 'sonarqube' for d in initial_database_docs), 'Sonar database waits for credentials'
        assert any(d['kind'] == 'Database' and d['metadata']['name'] == 'testkube' for d in initial_database_docs)
        routes = next(a for a in workload if a['metadata']['name'] == 'routes')
        initial_routes = [d for d in yaml.safe_load_all(routes['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert not any(d['metadata']['namespace'] == 'sonarqube' for d in initial_routes), 'Initial routes must not block on a Sonar backend that has not been installed'
        assert waves['configuration'] < waves['postgres'] < waves['devlake-credentials'] < waves['grafana'] < waves['devlake'] < waves['devlake-configure'] < waves['devlake-collection'] < waves['routes']
        assert waves['service-identities'] < waves['devlake']
        assert waves['routes'] > waves['sonarqube'], 'Activate routes after backend health'
        assert waves['envoy-gateway'] < waves['cert-manager'] < waves['configuration'], 'Install Gateway API CRDs before cert-manager and issuers after it'
        assert waves['azure-service-operator'] > waves['cert-manager'], 'ASO certificates require cert-manager'
        assert waves['cert-manager'] < waves['clickhouse-operator'] < waves['clickhouse'], 'ClickHouse webhook certificates require cert-manager before the operator and database'
        assert waves['external-secrets'] < waves['configuration'] < waves['postgres'] < waves['testkube-storage'] < waves['testkube']
        assert waves['configuration'] < waves['testkube-azure'] < waves['testkube-storage'] < waves['testkube']
        assert waves['testkube'] < waves['testkube-checks'] < waves['testkube-analysis']
        assert any(a['metadata']['name'].startswith('testkube') for a in workload), 'Each cluster runs its own Testkube'
        assert waves['configuration'] < waves['testkube-nats'] < waves['testkube']
        assert waves['argo-rollouts'] < waves['testkube-analysis'] and waves['testkube'] < waves['testkube-analysis']
        assert waves['envoy-gateway'] < waves['argo-rollouts'] < waves['routes'], 'Gateway API must exist before Rollouts starts'
        assert 'argo-rollouts' in {a['metadata']['name'] for a in workload}, 'Each cluster needs its own Rollouts controller'
        assert not {'kargo', 'kargo-acr', 'kargo-identity'} & {a['metadata']['name'] for a in workload}


        # Exercise the opt-in package separately so default package ownership
        # remains unchanged and workloads cannot acquire a second portal.
        portal = {'enabled': True, 'image': 'registry.example.test/backstage@sha256:' + 'a' * 64,
                  'egress': [{'to': [{'ipBlock': {'cidr': '10.20.0.10/32'}}],
                              'ports': [{'protocol': 'TCP', 'port': 443}]}]}
        backstage_resources = []
        for role in ['management', 'workload']:
            values.write_text(json.dumps({'runtime': runtime, 'cluster': {'name': role, 'role': role},
                                         'sonarqubeReady': True, 'backstage': portal, 'atlantis': atlantis}))
            enabled_apps = list(yaml.safe_load_all(command('helm', 'template', 'platform',
                                str(HELM_CHART / 'platform'), '-f', str(values))))
            enabled_apps = [a for a in enabled_apps if a and a['kind'] == 'Application']
            by_name = {a['metadata']['name']: a for a in enabled_apps}
            assert ('backstage' in by_name) == (role == 'management')
            assert ('backstage-credentials' in by_name) == (role == 'management')
            resources = [d for a in enabled_apps
                         for d in yaml.safe_load_all(a['spec'].get('source', {}).get('helm', {}).get('valuesObject', {}).get('manifests', '')) if d]
            db = [d for d in resources if d['kind'] == 'Database' and d['metadata']['name'] == 'backstage']
            assert bool(db) == (role == 'management')
            cluster = next(d for d in resources if d['kind'] == 'Cluster')
            roles = cluster['spec'].get('managed', {}).get('roles', [])
            assert any(r['name'] == 'backstage' for r in roles) == (role == 'management')
            routes = [d for d in resources if d['kind'] == 'ListenerSet' and d['metadata']['namespace'] == 'backstage']
            assert bool(routes) == (role == 'management')
            if role == 'management':
                backstage_resources = [d for d in resources if d['metadata']['name'].startswith('backstage')]
                r = next(r for r in roles if r['name'] == 'backstage')
                assert not any(r[k] for k in ['superuser', 'createdb', 'createrole', 'replication'])
                assert db[0]['spec']['owner'] == 'backstage'
                identities = list(yaml.safe_load_all(by_name['service-identities']['spec']['source']['helm']['valuesObject']['manifests']))
                identity = next(d for d in identities if d and d['kind'] == 'OIDCApplication' and d['metadata']['name'] == 'backstage-sso')
                assert identity['spec']['redirectUri'] == 'https://backstage.example.test/api/auth/microsoft/handler/frame'
                assert identity['spec']['requiredResourceAccess'][0]['resourceAccess'] == [{'id': 'e1fe6dd8-ba31-4d61-89e7-88639da4683d', 'type': 'Scope'}]
                enabled_waves = {name: int(app['metadata']['annotations']['argocd.argoproj.io/sync-wave']) for name, app in by_name.items()}
                assert enabled_waves['configuration'] < enabled_waves['postgres'] < enabled_waves['backstage-credentials'] < enabled_waves['backstage'] < enabled_waves['routes']
                early = list(yaml.safe_load_all(by_name['configuration']['spec']['source']['helm']['valuesObject']['manifests']))
                for secret_name in ['backstage-role', 'backstage-session']:
                    es = next(d for d in early if d and d['kind'] == 'ExternalSecret' and d['metadata']['name'] == secret_name)
                    assert es['spec']['refreshPolicy'] == 'CreatedOnce'
                    assert es['spec']['target']['immutable'] is True
                    assert es['spec']['target']['creationPolicy'] == 'Orphan'
                organization = next(d for d in early if d and d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'backstage-identity')
                org_entities = list(yaml.safe_load_all(organization['data']['catalog-org.yaml']))
                assert any('graph.microsoft.com/user-id' in d['metadata'].get('annotations', {}) for d in org_entities if d['kind'] == 'User')
                assert not any(d and d['kind'] == 'ExternalSecret' and d['metadata']['name'] in {'backstage-auth', 'backstage-identity', 'backstage-integrations'} for d in early)
                credentials = list(yaml.safe_load_all(by_name['backstage-credentials']['spec']['source']['helm']['valuesObject']['manifests']))
                reader = next(d for d in credentials if d['kind'] == 'Role')
                assert reader['metadata']['namespace'] == 'postgres'
                assert reader['rules'] == [{'apiGroups': [''], 'resources': ['secrets'], 'resourceNames': ['backstage-role', 'platform-ca'], 'verbs': ['get']}]
                copied = next(d for d in credentials if d['kind'] == 'ExternalSecret')
                assert {(d['remoteRef']['key'], d['remoteRef']['property']) for d in copied['spec']['data']} == {('backstage-role', 'password'), ('platform-ca', 'ca.crt')}
                deployment = next(d for d in resources if d['kind'] == 'Deployment' and d['metadata']['name'] == 'backstage')
                pod = deployment['spec']['template']['spec']
                container = pod['containers'][0]
                assert '@sha256:' in container['image']
                assert pod['automountServiceAccountToken'] is False
                native_env = {e['name']: e for e in container['env']}
                assert native_env['APP_CONFIG_backend_database_ensureExists']['value'] == 'false'
                assert native_env['APP_CONFIG_backend_database_ensureSchemaExists']['value'] == 'true'
                assert native_env['AUTH_MICROSOFT_CLIENT_SECRET']['valueFrom']['secretKeyRef'] == {'name': 'backstage-sso', 'key': 'client-secret'}
                assert any(v.get('configMap', {}).get('name') == 'backstage-identity' for v in pod['volumes'])
                assert 'backstage-reloader' in by_name
                assert container['securityContext']['readOnlyRootFilesystem']
                assert container['resources']['requests']['memory'] == '512Mi'
                assert not any(d['kind'] in {'ClusterRole', 'ClusterRoleBinding'} and
                               'backstage' in d['metadata']['name'] for d in resources)
                network = next(d for d in resources if d['kind'] == 'NetworkPolicy' and d['metadata']['namespace'] == 'backstage')
                assert network['spec']['policyTypes'] == ['Ingress', 'Egress']
                assert network['spec']['egress'][-1] == portal['egress'][0]
        for invalid in [dict(portal, image='registry.example.test/backstage:latest'),
                        dict(portal, egress=[{'to': [{'ipBlock': {'cidr': '0.0.0.0/0'}}], 'ports': [{'protocol': 'TCP', 'port': 443}]}])]:
            values.write_text(json.dumps({'runtime': runtime, 'backstage': invalid}))
            result = subprocess.run(['helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values)], text=True, capture_output=True)
            assert result.returncode and 'schema' in result.stderr, 'Unsafe Backstage settings must fail Helm validation'
        print('Backstage: configuration, credential gates, database wiring and staged policy rendering verified; live isolation not checked.')

        devlake_apps = {a['metadata']['name']: a for a in applications}
        devlake_resources = [d for a in applications for d in yaml.safe_load_all(
            a['spec'].get('source', {}).get('helm', {}).get('valuesObject', {}).get('manifests', '')) if d]
        for name in ['devlake-role', 'devlake-grafana-role', 'devlake-encryption']:
            es = next(d for d in devlake_resources if d['kind'] == 'ExternalSecret' and d['metadata']['name'] == name)
            assert '{{ .password }}' in es['spec']['target']['template']['data'].values(), 'ESO templates must survive Helm evaluation'
        admin = next(d for d in devlake_resources if d['kind'] == 'ExternalSecret' and d['metadata']['name'] == 'devlake-automation')
        assert admin['spec']['target']['template']['data']['credential'] == 'Basic {{ printf "platform-admin:%s" .password | b64enc }}'
        core = next(d for d in devlake_resources if d['kind'] == 'Deployment' and d['metadata']['name'] == 'devlake')
        container = core['spec']['template']['spec']['containers'][0]
        assert container['image'] == 'apache/devlake:v1.0.2@sha256:bae4e753959584b152fd2d66b2b13c4bc6c5ce02b9bd7c4a000e3e1296b09343'
        assert 'for name in dora webhook org refdiff github github_graphql gitextractor' in container['command'][2]
        assert not any(e['name'].startswith('OIDC_') for e in container['env']), 'Stable DevLake has no native OIDC'
        assert container['securityContext']['readOnlyRootFilesystem']
        network = next(d for d in devlake_resources if d['kind'] == 'NetworkPolicy' and d['metadata']['name'] == 'devlake')
        core_egress = next(rule for rule in network['spec']['egress'] if {'protocol': 'TCP', 'port': 8080} in rule['ports'])
        assert core_egress['to'] == [{'podSelector': {'matchLabels': {'app.kubernetes.io/name': 'devlake'}}}]
        assert not any(d['kind'] == 'PersistentVolumeClaim' and d['metadata'].get('namespace') == 'devlake' for d in devlake_resources)
        policy = next(d for d in devlake_resources if d['kind'] == 'SecurityPolicy' and d['metadata']['name'] == 'devlake-sso')
        assert policy['spec']['oidc']['clientIDRef'] == {'name': 'devlake-sso'}
        assert policy['spec']['oidc']['clientSecret'] == {'name': 'devlake-sso'}
        machine = next(d for d in devlake_resources if d['kind'] == 'HTTPRoute' and d['metadata']['name'] == 'devlake-ingestion')
        assert 'filters' not in machine['spec']['rules'][0], 'Machine ingestion must never receive an injected administration credential'
        ui = next(d for d in devlake_resources if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'devlake-ui')['data']['default.conf']
        assert 'auth_basic_user_file /etc/devlake-auth/.htpasswd;' in ui and 'if ($devlake_reject_origin)' in ui
        assert next(d for d in devlake_resources if d['kind'] == 'CronJob' and d['metadata']['name'] == 'devlake-collection')['spec']['suspend'] is True
        access = next(d for d in devlake_resources if d['kind'] == 'Job' and d['metadata']['name'].startswith('devlake-access-'))
        assert access['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '1'
        values.write_text(json.dumps({'runtime': runtime, 'devlake': {'enabled': False}}))
        without = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))))
        without = [a for a in without if a and a['kind'] == 'Application']
        assert not any('devlake' in json.dumps(a) for a in without), 'Disabling DevLake must remove every package dependency'
        assert next(a for a in without if a['metadata']['name'] == 'grafana')['metadata']['annotations']['argocd.argoproj.io/sync-wave'] == '30'
        print('DevLake: stable images, deferred ESO templates, PostgreSQL reuse, SSO/API isolation and access gates verified.')



        def render(app):
            source = app['spec'].get('source') or app['spec']['sources'][0]
            if 'chart' not in source:
                return
            name = app['metadata']['name']
            values = tmp / (name + '.json')
            values.write_text(json.dumps(source['helm']['valuesObject']))
            repo = source['repoURL']
            chart = source['chart']
            args = ([chart, '--repo', repo] if repo.startswith('https://')
                    else ['oci://' + repo + '/' + chart])
            try:
                for api_version in source['helm'].get('apiVersions', []):
                    args.extend(['--api-versions', api_version])
                output = command('helm', 'template', name, *args, '--version', source['targetRevision'],
                    '--namespace', app['spec']['destination']['namespace'], '--include-crds',
                    '--kube-version', '1.34.1', '-f', str(values))
                docs = [d for d in yaml.safe_load_all(output) if d]
                assert docs, f'{name} rendered no resources'
                if name == 'kargo':
                    controller = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'kargo-controller')
                    pod = controller['spec']['template']['spec']
                    controller_env = {e['name']: e for e in pod['containers'][0]['env']}
                    assert controller_env['AZURE_CLIENT_ID']['valueFrom']['configMapKeyRef'] == {'name': 'kargo-acr-identity', 'key': 'clientId'}
                    assert controller_env['AZURE_FEDERATED_TOKEN_FILE']['value'].endswith('/azure-identity-token')
                    assert any(v['name'] == 'azure-identity-token' for v in pod['volumes'])
                    api = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'kargo-api')
                    env = {e['name']: e for e in api['spec']['template']['spec']['containers'][0]['env']}
                    assert env['OIDC_CLIENT_ID']['valueFrom']['secretKeyRef'] == {'name': 'kargo-sso', 'key': 'client-id'}
                    config = next(d for d in docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'kargo-api')['data']
                    assert config['OIDC_ENABLED'] == 'true' and 'ADMIN_ACCOUNT_ENABLED' not in config
                    service = next(d for d in docs if d['kind'] == 'Service' and d['metadata']['name'] == 'kargo-api')
                    assert service['spec']['ports'][0]['port'] == 80
                    assert not any(d['kind'] == 'Ingress' for d in docs)
                if name == 'atlantis':
                    runner = next(d for d in docs if d['kind'] == 'StatefulSet')['spec']['template']['spec']
                    env = {e['name']: e for e in runner['containers'][0]['env']}
                    assert 'ATLANTIS_WEBHOOK_HTTP_HEADERS' not in env
                    volumes = {v['name']: v for v in runner['volumes']}
                    projection = volumes['onboarding-api-token']['projected']['sources'][0]['serviceAccountToken']
                    assert projection == {'expirationSeconds': 600, 'path': 'token'}
                    mounts = {v['name']: v for v in runner['containers'][0]['volumeMounts']}
                    assert mounts['onboarding-script']['readOnly'] and mounts['onboarding-api-token']['readOnly']
                if name == 'argo-events':
                    assert {'EventBus', 'EventSource', 'Sensor'} <= {d['spec']['names']['kind'] for d in docs if d['kind'] == 'CustomResourceDefinition'}
                    assert not any(d['kind'] == 'ClusterRoleBinding' for d in docs), 'Keep the controller namespaced'
                if name == 'testkube':
                    assert not any(d['kind'] in {'StatefulSet', 'Ingress', 'HTTPRoute', 'PersistentVolumeClaim', 'Job'} for d in docs)
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    pod = deployment['spec']['template']['spec']
                    assert pod['serviceAccountName'] == 'testkube-api'
                    container = pod['containers'][0]
                    env = {e['name']: e for e in container['env']}
                    assert 'API_MONGO_DSN' not in env
                    assert env['API_POSTGRES_DSN']['valueFrom']['secretKeyRef'] == {'name': 'testkube-database', 'key': 'dsn'}
                    assert env['NATS_URI']['value'] == 'tls://testkube-nats.testkube.svc.cluster.local:4222'
                    assert env['NATS_SECURE']['value'] == 'true' and env.get('NATS_SKIP_VERIFY', {'value': 'false'})['value'] == 'false'
                    assert env['STORAGE_SSL']['value'] == 'true'
                    for prefix, directory in [('NATS', 'nats'), ('STORAGE', 'storage')]:
                        for suffix, filename in [('CERT_FILE', 'tls.crt'), ('KEY_FILE', 'tls.key'), ('CA_FILE', 'ca.crt')]:
                            assert env[prefix + '_' + suffix]['value'] == f'/etc/client-certs/{directory}/{filename}'
                    assert env['SSL_CERT_FILE']['value'] == '/etc/client-certs/storage/ca.crt'
                    assert env['STORAGE_SECRETACCESSKEY']['valueFrom']['secretKeyRef'] == {'name': 'testkube-storage', 'key': 'secretKey'}
                    assert env['FEATURE_CLOUD_STORAGE']['value'] == 'false' and env['SKIP_DB_CREATION']['value'] == 'true'
                    assert env['ENABLE_SECRETS_ENDPOINT']['value'] == 'false'
                    workflow = json.loads(env['TESTKUBE_GLOBAL_WORKFLOW_TEMPLATE_INLINE']['value'])
                    assert workflow['pod']['serviceAccountName'] == 'testkube-tests' and workflow['pod']['automountServiceAccountToken'] is False
                    assert pod['securityContext']['runAsNonRoot'] and container['securityContext']['readOnlyRootFilesystem']
                    cluster_roles = [d for d in docs if d['kind'] == 'ClusterRole']
                    assert len(cluster_roles) == 1 and cluster_roles[0]['rules'] == [{'apiGroups': ['apiextensions.k8s.io'], 'resources': ['customresourcedefinitions'], 'verbs': ['get', 'list', 'watch']}]
                    assert {'TestWorkflow', 'TestWorkflowTemplate'} <= {d['spec']['names']['kind'] for d in docs if d['kind'] == 'CustomResourceDefinition'}
                if name == 'testkube-nats':
                    statefulset = next(d for d in docs if d['kind'] == 'StatefulSet')
                    assert statefulset['spec']['replicas'] == 1
                    assert statefulset['spec']['template']['spec']['automountServiceAccountToken'] is False
                    config = next(d for d in docs if d['kind'] == 'ConfigMap')['data']['nats.conf']
                    assert 'jetstream' in config and 'verify' in config and 'ca_file' in config
                    assert '$NATS_AUTH_TOKEN' not in config
                    assert statefulset['spec']['volumeClaimTemplates'][0]['spec']['storageClassName'] == 'managed-csi'
                if name == 'argo-rollouts':
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    pod = deployment['spec']['template']['spec']
                    container = pod['containers'][0]
                    assert deployment['spec']['replicas'] == 2 and '--leader-elect' in container['args']
                    assert pod['securityContext']['runAsNonRoot'] and pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
                    for c in [container, *pod['initContainers']]:
                        security = c['securityContext']
                        assert security['allowPrivilegeEscalation'] is False and security['readOnlyRootFilesystem']
                        assert security['capabilities']['drop'] == ['ALL']
                        assert c['resources']['requests'] and c['resources']['limits']['memory']
                    assert '@sha256:' in pod['initContainers'][0]['image']
                    assert container['readinessProbe']['httpGet']['path'] == '/healthz'
                    assert {'plugin-bin', 'tmp', 'gateway-plugin'} <= {v['name'] for v in pod['volumes']}
                    config = next(d for d in docs if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'argo-rollouts-config')
                    plugin = yaml.safe_load(config['data']['trafficRouterPlugins'])[0]
                    assert plugin['name'] == 'argoproj-labs/gatewayAPI' and plugin['location'] == 'file:///plugins/gateway-api'
                    role = next(d for d in docs if d['kind'] == 'ClusterRole')
                    routes = [r for r in role['rules'] if 'gateway.networking.k8s.io' in r['apiGroups']]
                    assert routes == [{'apiGroups': ['gateway.networking.k8s.io'], 'resources': ['httproutes', 'grpcroutes'], 'verbs': ['get', 'list', 'watch', 'update', 'patch']}]
                    assert all(set(r['verbs']) <= {'get', 'list', 'watch'} for r in role['rules'] if 'configmaps' in r['resources'])
                    assert not any('aggregationRule' in d or d['kind'] in {'Ingress', 'HTTPRoute'} for d in docs)
                    assert {d['spec']['names']['kind'] for d in docs if d['kind'] == 'CustomResourceDefinition'} == {'Rollout', 'AnalysisTemplate', 'ClusterAnalysisTemplate', 'AnalysisRun', 'Experiment'}
                    assert next(d for d in docs if d['kind'] == 'PodDisruptionBudget')['spec']['minAvailable'] == 1
                    policy = next(d for d in docs if d['kind'] == 'NetworkPolicy')
                    assert policy['metadata']['namespace'] == 'argo-rollouts'
                    assert policy['spec']['ingress'][0]['from'][0]['namespaceSelector']['matchLabels']['kubernetes.io/metadata.name'] == 'monitoring'
                    monitoring = next(a for a in applications if a['metadata']['name'] == 'k8s-monitoring')['spec']['source']['helm']['valuesObject']
                    discovery = monitoring['annotationAutodiscovery']
                    assert discovery['enabled'] and 'argo-rollouts' in discovery['namespaces'] and not discovery['bearerToken']['enabled']
                    assert pod['serviceAccountName'] == 'argo-rollouts'
                if name == 'reflector':
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    assert deployment['spec']['replicas'] == 1
                    pod = deployment['spec']['template']['spec']
                    container = pod['containers'][0]
                    assert container['image'] == 'docker.io/emberstack/kubernetes-reflector:10.0.65'
                    assert pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
                    security = container['securityContext']
                    assert security['runAsNonRoot'] and security['readOnlyRootFilesystem']
                    assert security['allowPrivilegeEscalation'] is False and security['capabilities']['drop'] == ['ALL']
                    env = {e['name']: e.get('value') for e in container['env']}
                    assert env['ES_Ignite__KubernetesClient__SkipTlsVerify'] == 'false'
                    assert not any(d['kind'] in {'Secret', 'ConfigMap', 'Service', 'Ingress', 'PersistentVolumeClaim'} for d in docs)
                    role = next(d for d in docs if d['kind'] == 'ClusterRole')
                    assert role['rules'] == [{'apiGroups': [''], 'resources': ['configmaps', 'secrets'], 'verbs': ['*']},
                                             {'apiGroups': [''], 'resources': ['namespaces'], 'verbs': ['watch', 'list']}]
                if name == 'atlas-operator':
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    pod = deployment['spec']['template']['spec']
                    manager = pod['containers'][0]
                    assert manager['image'] == 'arigaio/atlas-operator:0.8.4'
                    assert manager['args'] == ['--namespace=atlas-system']
                    assert manager['securityContext']['runAsNonRoot'] is True
                    assert manager['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
                    assert manager['securityContext']['allowPrivilegeEscalation'] is False
                    env = {e['name']: e for e in manager['env']}
                    assert {name: env[name]['value'] for name in ['PREWARM_DEVDB', 'ALLOW_CUSTOM_CONFIG', 'WATCH_SECRETS']} == {
                        'PREWARM_DEVDB': 'false', 'ALLOW_CUSTOM_CONFIG': 'true', 'WATCH_SECRETS': 'false'}
                    assert env['ATLAS_TOKEN']['valueFrom']['secretKeyRef'] == {'name': 'atlas-token-secret', 'key': 'ATLAS_TOKEN'}
                    assert env['AZURE_CLIENT_ID']['valueFrom']['configMapKeyRef'] == {'name': 'atlas-postgres-identity', 'key': 'clientId'}
                    assert len(env) == 5
                    assert deployment['spec']['template']['metadata']['labels']['azure.workload.identity/use'] == 'true'
                    assert manager['volumeMounts'] == [{'name': 'postgres-ca', 'mountPath': '/etc/atlas-postgres', 'readOnly': True}]
                    assert pod['volumes'] == [{'name': 'postgres-ca', 'secret': {'secretName': 'atlas-postgres',
                        'defaultMode': 0o640, 'items': [{'key': 'ca.crt', 'path': 'ca.crt'},
                        {'key': 'tls.crt', 'path': 'tls.crt'}, {'key': 'tls.key', 'path': 'tls.key'}]}}]
                    assert isinstance(pod['securityContext']['fsGroup'], int), 'Non-root Atlas must be able to read the group-readable client key'
                    assert pod['serviceAccountName'] == 'atlas-operator'
                    assert not any('secrets' in rule.get('resources', [])
                                   for d in docs if d['kind'] == 'ClusterRole' for rule in d['rules'])
                    assert not any(d['kind'] in {'Secret', 'PersistentVolumeClaim', 'Service', 'Ingress', 'AtlasSchema', 'AtlasMigration'} for d in docs)
                    assert {'AtlasSchema', 'AtlasMigration'} <= {d['spec']['names']['kind'] for d in docs if d['kind'] == 'CustomResourceDefinition'}
                if name == 'azure-service-operator':
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    pod = deployment['spec']['template']['spec']
                    manager = next(c for c in pod['containers'] if c['name'] == 'manager')
                    assert deployment['spec']['replicas'] == 1
                    assert manager['image'].endswith(':v2.22.0')
                    assert '--crd-pattern=resources.azure.com/*;managedidentity.azure.com/*;authorization.azure.com/*;storage.azure.com/*;dbforpostgresql.azure.com/*;network.azure.com/PrivateEndpoint;network.azure.com/PrivateEndpointsPrivateDnsZoneGroup' in manager['args']
                    assert pod['serviceAccountName'] == 'azureserviceoperator-default'
                    assert any(s.get('serviceAccountToken', {}).get('audience') == 'api://AzureADTokenExchange'
                               for v in pod['volumes'] for s in v.get('projected', {}).get('sources', []))
                    settings = next(d for d in docs if d['kind'] == 'Secret' and d['metadata']['name'] == 'aso-controller-settings')['data']
                    assert all(settings[key] == '' for key in ['AZURE_CLIENT_ID', 'AZURE_TENANT_ID', 'AZURE_SUBSCRIPTION_ID'])
                    assert settings['USE_WORKLOAD_IDENTITY_AUTH'] == 'dHJ1ZQ=='
                    assert base64.b64decode(settings['AZURE_TARGET_NAMESPACES']).decode() == 'azure-resources,cluster-onboarding,testkube,holmes,atlantis'
                    namespaces = base64.b64decode(settings['AZURE_TARGET_NAMESPACES']).decode().split(',')
                    checksum = hashlib.sha256(json.dumps(namespaces, separators=(',', ':')).encode()).hexdigest()
                    assert deployment['spec']['template']['metadata']['annotations']['gitops.platform/watch-namespaces'] == checksum
                    assert not any(d['kind'] in {'PodDisruptionBudget', 'NetworkPolicy', 'CustomResourceDefinition'} for d in docs)
                    assert any(d['kind'] == 'Certificate' for d in docs), 'ASO needs internal webhook TLS'
                if name in {'langfuse-reloader', 'backstage-reloader', 'testkube-reloader', 'devlake-reloader', 'redis-reloader'}:
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    pod = deployment['spec']['template']['spec']
                    assert pod['securityContext']['runAsNonRoot'] is True
                    assert pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
                    for container in pod['containers']:
                        security = container['securityContext']
                        assert security['allowPrivilegeEscalation'] is False
                        assert security['readOnlyRootFilesystem'] is True
                        assert security['capabilities']['drop'] == ['ALL']
                if name == 'opentelemetry-collector':
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    container = deployment['spec']['template']['spec']['containers'][0]
                    auth = next(e for e in container['env'] if e['name'] == 'LANGFUSE_AUTHORIZATION')
                    assert auth['valueFrom']['secretKeyRef'] == {'name': 'langfuse-otel', 'key': 'authorization'}
                    configmap = next(d for d in docs if d['kind'] == 'ConfigMap')
                    config = yaml.safe_load(configmap['data']['relay'])
                    pipelines = config['service']['pipelines']
                    assert pipelines['traces']['exporters'] == ['otlp_grpc/tempo']
                    assert pipelines['traces/langfuse'] == {'receivers': ['otlp'], 'processors': ['memory_limiter', 'filter/langfuse', 'batch'], 'exporters': ['otlp_http/langfuse']}
                    assert config['processors']['filter/langfuse']['error_mode'] == 'propagate'
                    assert config['exporters']['otlp_http/langfuse']['headers']['Authorization'] == '${env:LANGFUSE_AUTHORIZATION}'
                if name == 'langfuse':
                    if database_values['redis'].get('deploy') is False:
                        assert not any(d['metadata']['name'] == 'langfuse-redis' and d['kind'] in ['Deployment', 'StatefulSet', 'PersistentVolumeClaim', 'Secret'] for d in docs)
                    web = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'langfuse-web')
                    assert not any(d['kind'] == 'StatefulSet' and 'postgres' in d['metadata']['name'] for d in docs)
                    assert not any(d['kind'] == 'Secret' and 'postgres' in d['metadata']['name'] for d in docs)
                    for deployment in [d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] in {'langfuse-web', 'langfuse-worker'}]:
                        assert 'langfuse-postgres' in deployment['metadata']['annotations']['secret.reloader.stakater.com/reload'].split(',')
                        pod = deployment['spec']['template']['spec']
                        assert any(v.get('secret', {}).get('secretName') == 'langfuse-postgres' for v in pod['volumes'])
                        container = pod['containers'][0]
                        assert container['image'].endswith(':4.76.0')
                        envs = {e['name']: e for e in container['env']}
                        assert envs['LANGFUSE_INIT_ORG_ID']['value'] == 'platform'
                        assert envs['LANGFUSE_INIT_PROJECT_ID']['value'] == 'agents'
                        assert envs['LANGFUSE_INIT_PROJECT_SECRET_KEY']['valueFrom']['secretKeyRef'] == {'name': 'langfuse-otel', 'key': 'secret-key'}
                        assert not any(key.startswith('LANGFUSE_INIT_USER_') for key in envs)
                        assert 'DATABASE_PASSWORD' not in envs
                        url = envs['DATABASE_URL']['value']
                        assert url.startswith('postgresql://langfuse@platform-rw.postgres.svc.cluster.local:5432/langfuse?')
                        assert all(arg in url for arg in ['sslmode=require', 'sslaccept=strict', 'sslidentity=/etc/langfuse-postgres/keystore.p12'])
                        assert any(v['mountPath'] == '/etc/langfuse-postgres' and v['readOnly'] for v in container['volumeMounts'])
                    assert web['metadata']['annotations']['secret.reloader.stakater.com/reload'] == ('langfuse-postgres,langfuse-redis,langfuse-sso' if database_values['redis'].get('deploy') is False else 'langfuse-postgres,langfuse-sso')
                    env = {e['name']: e for e in web['spec']['template']['spec']['containers'][0]['env']}
                    assert env.get('AUTH_DISABLE_USERNAME_PASSWORD', {}).get('value', 'false') == 'false', 'Keep recovery login until interactive Entra verification'
                    for env_name, key in [('AUTH_AZURE_AD_CLIENT_ID', 'client-id'), ('AUTH_AZURE_AD_CLIENT_SECRET', 'client-secret'), ('AUTH_AZURE_AD_TENANT_ID', 'tenant-id')]:
                        assert env[env_name]['valueFrom']['secretKeyRef'] == {'name': 'langfuse-sso', 'key': key}
                    assert env['AUTH_AZURE_AD_ALLOW_ACCOUNT_LINKING']['value'] == 'true'
                if name == 'cloudnative-pg':
                    schemas = {d['spec']['names']['kind']: next(v['schema']['openAPIV3Schema']
                        for v in d['spec']['versions'] if v['name'] == 'v1')
                        for d in docs if d['kind'] == 'CustomResourceDefinition'}
                    assert {'Cluster', 'Database', 'Backup', 'ScheduledBackup'} <= schemas.keys(), 'CNPG must expose database and backup APIs'
                    assert 'roles' in schemas['Cluster']['properties']['spec']['properties']['managed']['properties'], 'CNPG must support declarative roles'
                    for resource in postgres_documents:
                        if resource['kind'] in {'Cluster', 'Database'}:
                            properties = schemas[resource['kind']]['properties']['spec']['properties']
                            assert resource['spec'].keys() <= properties.keys(), 'CNPG spec uses unsupported fields'
                            if resource['kind'] == 'Cluster':
                                role_fields = properties['managed']['properties']['roles']['items']['properties']
                                assert resource['spec']['managed']['roles'][0].keys() <= role_fields.keys()
                    assert {'applied', 'observedGeneration'} <= schemas['Database']['properties']['status']['properties'].keys()
                if name == 'external-secrets':
                    crds = {d['metadata']['name'] for d in docs if d['kind'] == 'CustomResourceDefinition'}
                    assert {'externalsecrets.external-secrets.io', 'clustersecretstores.external-secrets.io',
                            'passwords.generators.external-secrets.io', 'githubaccesstokens.generators.external-secrets.io'} <= crds, 'Secret delivery and password generation APIs must be installed'
                    schemas = {d['spec']['names']['kind']: next(v['schema']['openAPIV3Schema'] for v in d['spec']['versions'] if v.get('storage'))
                               for d in docs if d['kind'] == 'CustomResourceDefinition'}
                    for resource in backstage_resources:
                        if resource['kind'] not in {'ExternalSecret', 'SecretStore', 'Password'}:
                            continue
                        properties = schemas[resource['kind']]['properties']['spec']['properties']
                        assert resource['spec'].keys() <= properties.keys(), 'Backstage uses fields absent from pinned ESO CRDs'
                    for resource in github_resources:
                        schema = schemas[resource['kind']]['properties']['spec']
                        assert resource['spec'].keys() <= schema['properties'].keys(), 'GitHub credentials use unsupported ESO fields'
                        assert set(schema.get('required', [])) <= resource['spec'].keys()
                    github = schemas['GithubAccessToken']['properties']['spec']['properties']
                    assert {'permissions', 'auth', 'appID', 'installID'} <= github.keys()
                    assert 'secretRef' in github['auth']['properties']['privateKey']['properties']
                    es = schemas['ExternalSecret']['properties']['spec']['properties']
                    assert 'CreatedOnce' in es['refreshPolicy']['enum']
                    assert 'immutable' in es['target']['properties']
                    auth = schemas['SecretStore']['properties']['spec']['properties']['provider']['properties']['kubernetes']['properties']['auth']['properties']
                    assert 'serviceAccount' in auth
                    assert any('serviceaccounts/token' in rule.get('resources', []) and 'create' in rule.get('verbs', [])
                               for d in docs if d['kind'] == 'ClusterRole' for rule in d['rules']), 'ESO must be allowed to request a bounded reader token'

                if name == 'plugin-barman-cloud':
                    assert any(d['kind'] == 'CustomResourceDefinition'
                               and d['metadata']['name'] == 'objectstores.barmancloud.cnpg.io' for d in docs), 'Barman must expose backup configuration API'
                if name == 'cert-manager':
                    webhook = next(d for d in docs if d['kind'] == 'ValidatingWebhookConfiguration')
                    exclusions = {e['key']: (e['operator'], e['values'])
                                  for e in webhook['webhooks'][0]['namespaceSelector']['matchExpressions']}
                    assert exclusions == {
                        'cert-manager.io/disable-validation': ('NotIn', ['true']),
                        'control-plane': ('NotIn', ['true']),
                        'kubernetes.azure.com/managedby': ('NotIn', ['aks']),
                    }, 'Preserve cert-manager and AKS namespace exclusions to avoid perpetual webhook drift'
                    config = next(d for d in docs if d['kind'] == 'ConfigMap' and 'config.yaml' in d.get('data', {}))
                    settings = yaml.safe_load(config['data']['config.yaml'])
                    assert settings['gatewayAPI'] == {'enabled': True, 'enableListenerSet': True}
                    assert settings['featureGates']['ListenerSets'] is True
                    assert any('listenersets' in rule.get('resources', []) for d in docs if d['kind'] == 'ClusterRole' for rule in d['rules']), 'cert-manager needs ListenerSet RBAC'
                if name == 'envoy-gateway':
                    assert any(d['kind'] == 'CustomResourceDefinition' and d['metadata']['name'] == 'listenersets.gateway.networking.k8s.io' for d in docs), 'Envoy must install ListenerSet CRDs'
                if name == 'external-dns':
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    assert '--gateway-listener-sets' in deployment['spec']['template']['spec']['containers'][0]['args']
                    assert any('listenersets' in rule.get('resources', []) for d in docs if d['kind'] == 'ClusterRole' for rule in d['rules'])
                if name == 'mimir':
                    webhook = next(d for d in docs if d['kind'] == 'ValidatingWebhookConfiguration'
                                   and d['metadata']['name'] == 'pod-eviction-lgtm')
                    assert webhook['webhooks'][0]['failurePolicy'] == 'Fail'
                    override = list(yaml.safe_load_all(app['spec']['sources'][1]['helm']['valuesObject']['manifests']))[0]
                    assert override['metadata']['name'] == webhook['metadata']['name']
                    assert override['webhooks'][0]['failurePolicy'] == 'Ignore'
                    assert [d for d in docs if d['kind'] == 'MutatingWebhookConfiguration']
                    assert all(w['failurePolicy'] == 'Fail' for d in docs if d['kind'] == 'MutatingWebhookConfiguration' for w in d['webhooks'])
                if name == 'atlantis':
                    server = next(d for d in docs if d['kind'] == 'StatefulSet')
                    assert server['spec']['replicas'] == 1
                    pod = server['spec']['template']
                    assert 'azure.workload.identity/use' not in pod['metadata']['labels']
                    assert any(s.get('serviceAccountToken', {}).get('audience') == 'api://AzureADTokenExchange'
                               for v in pod['spec']['volumes'] for s in v.get('projected', {}).get('sources', []))
                    assert pod['spec']['automountServiceAccountToken'] is False
                    gate = next(c for c in pod['spec']['initContainers'] if c['name'] == 'await-azure')
                    assert gate['image'].startswith('registry.k8s.io/kubectl:v1.35.0@sha256:')
                    assert '--for=condition=Ready' in gate['args']
                    assert 'resourcegroups.resources.azure.com/atlantis' in gate['args']
                    assert 'roleassignments.authorization.azure.com/atlantis-state' in gate['args']
                    assert {m['name'] for m in gate['volumeMounts']} == {'readiness-api-token', 'readiness-cache'}
                    container = pod['spec']['containers'][0]
                    assert 'readiness-api-token' not in {m['name'] for m in container['volumeMounts']}
                    assert container['image'].endswith(':v0.48.2')
                    env = {e['name']: e for e in container['env']}
                    assert env['ATLANTIS_REPO_ALLOWLIST']['value'] == 'github.com/example/*,!github.com/example/gitops,!github.com/example/terraform'
                    assert env['ATLANTIS_DISABLE_AUTOPLAN']['value'] == 'true'
                    assert env['ATLANTIS_ALLOW_COMMANDS']['value'] == 'version,plan,apply,unlock'
                    assert env['ATLANTIS_GH_TEAM_ALLOWLIST']['value'] == 'platform:version,platform:plan,platform:apply,platform:unlock'
                    assert env['ATLANTIS_DISABLE_APPLY_ALL']['value'] == 'true'
                    assert env['ATLANTIS_WEB_BASIC_AUTH']['value'] == 'true'
                    assert env['ATLANTIS_GH_WEBHOOK_SECRET']['valueFrom']['secretKeyRef'] == {'name': 'atlantis-github', 'key': 'github_secret'}
                    assert env['ATLANTIS_WEB_PASSWORD']['valueFrom']['secretKeyRef'] == {'name': 'atlantis-web', 'key': 'password'}
                    assert env['ARM_CLIENT_ID']['valueFrom']['configMapKeyRef'] == {'name': 'atlantis-identity', 'key': 'clientId'}
                    assert env['ARM_USE_CLI']['value'] == 'false'
                    assert env['ARM_USE_OIDC']['value'] == 'true'
                    assert env['ARM_OIDC_TOKEN_FILE_PATH']['value'] == '/var/run/secrets/azure/tokens/azure-identity-token'
                    assert not any(d['kind'] in {'Secret', 'ClusterRole', 'ClusterRoleBinding', 'Ingress'} for d in docs)
                    service = next(d for d in docs if d['kind'] == 'Service')
                    assert service['spec']['type'] == 'ClusterIP'
                    assert service['spec']['ports'][0]['targetPort'] == 4141
                    pvc = next(d for d in docs if d['kind'] == 'PersistentVolumeClaim')
                    assert pvc['spec']['resources']['requests']['storage'] == '5Gi'
                    config = next(d for d in docs if d['kind'] == 'ConfigMap' and 'repos.yaml' in d.get('data', {}))
                    repo = yaml.safe_load(config['data']['repos.yaml'])['repos'][0]
                    assert repo['plan_requirements'] == ['mergeable', 'undiverged']
                    assert repo['apply_requirements'] == ['mergeable', 'undiverged']
                    assert repo['allowed_overrides'] == [] and repo['allow_custom_workflows'] is False
                    assert repo['repo_locks']['mode'] == 'on_plan'
                if name == 'sonarqube':
                    assert not any(d['kind'] == 'Job' and 'change-admin-password' in d['metadata']['name'] for d in docs)
                    statefulset = next(d for d in docs if d['kind'] == 'StatefulSet' and d['metadata']['name'] == 'sonarqube')
                    sonar = next(c for c in statefulset['spec']['template']['spec']['containers'] if c['name'] == 'sonarqube')
                    assert sonar['resources']['requests']['cpu'] == '200m'
                second = command('helm', 'template', name, *args, '--version', source['targetRevision'],
                    '--namespace', app['spec']['destination']['namespace'], '--include-crds',
                    '--kube-version', '1.34.1', '-f', str(values))
                def secret_data(documents):
                    return {d['metadata']['name']: d.get('data') for d in documents if d and d['kind'] == 'Secret'}
                assert secret_data(docs) == secret_data(yaml.safe_load_all(second)), f'{name} rotates secrets while rendering'
                print(f'{name}: {len(docs)} resources rendered; secrets stable', flush=True)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(f'{name}: {error.stderr}') from error
        with ThreadPoolExecutor(max_workers=4) as executor:
            list(executor.map(render, applications))
        print(f'{len(applications)} management Applications rendered; workload package parity and upstream charts verified.')


if __name__ == '__main__':
    main()
