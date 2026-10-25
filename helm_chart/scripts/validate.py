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
        values.write_text(json.dumps({'runtime': runtime, 'sonarqubeReady': True, 'atlantis': atlantis}))
        argo_values = tmp / 'argocd.json'
        argo_values.write_text(json.dumps({**bootstrap_values, 'extraObjects': [configmap]}))
        version = re.search(r'chart_version\s*=\s*"([^"]+)"', (bootstrap / 'terraform.tfvars').read_text())[1]
        argo = list(yaml.safe_load_all(command('helm', 'template', 'argocd', 'argo-cd', '--repo',
            'https://argoproj.github.io/argo-helm', '--version', version, '--namespace', 'argocd', '-f', str(argo_values))))
        maps = [d for d in argo if d and d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'argocd-cm']
        assert len(maps) == 1 and maps[0]['data'] == configmap['data'], 'Helm must install exactly the file-defined Argo ConfigMap'
        rendered = [d for d in yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))) if d]
        settings_map = next(d for d in rendered if d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'workload-onboarding-settings')
        assert 'clusterOnboarding' in json.loads(settings_map['data']['values.json'])
        applications = [d for d in rendered if d['kind'] == 'Application']
        names = {app['metadata']['name'] for app in applications}
        assert len(applications) == len(names), 'Duplicate Application names'
        expected = {
            'namespaces', 'reflector', 'argo-rollouts', 'testkube', 'testkube-storage', 'testkube-nats', 'testkube-analysis', 'testkube-azure', 'testkube-reloader', 'testkube-checks', 'atlas-operator', 'federation', 'envoy-gateway', 'external-secrets',
            'clickhouse-operator', 'cert-manager', 'cloudnative-pg', 'configuration',
            'external-dns', 'gateway', 'argocd-route', 'plugin-barman-cloud', 'postgres', 'clickhouse',
            'loki', 'grafana', 'tempo', 'mimir', 'langfuse', 'opentelemetry-collector',
            'k8s-monitoring', 'sonarqube', 'sonarqube-configure', 'routes',
            'azure-service-operator', 'atlantis', 'atlantis-azure', 'holmes', 'holmes-azure',
            'crossplane', 'crossplane-packages', 'crossplane-identity-api', 'service-identities', 'langfuse-reloader',
            'backstage', 'backstage-credentials', 'backstage-reloader',
            'devlake', 'devlake-credentials', 'devlake-configure', 'devlake-collection', 'devlake-reloader',
        }
        assert names == expected, 'A platform Application was omitted or renamed'
        staged_config = next(app for app in applications if app['metadata']['name'] == 'configuration')
        staged_resources = list(yaml.safe_load_all(staged_config['spec']['source']['helm']['valuesObject']['manifests']))
        staged_egress = next(doc for doc in staged_resources if doc['kind'] == 'NetworkPolicy' and doc['metadata']['name'] == 'holmes-egress')
        assert not any('ipBlock' in peer for rule in staged_egress['spec']['egress'] for peer in rule['to']), 'Unknown Holmes egress destinations must stay denied'
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
                    assert all(listener['protocol'] == 'HTTP' for listener in gateway['spec']['listeners'])
                if app['metadata']['name'] == 'routes':
                    listeners = [d for d in documents if d['kind'] == 'ListenerSet']
                    assert len(listeners) == 6
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
                                              'crossplane-packages', 'crossplane-identity-api', 'argocd-route'}, 'Workload must install every ready package except management services'
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
        print('Backstage: GitOps configuration, ESO credential gates, shared database and network isolation verified.')

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
                    env = {e['name']: e.get('value') for e in manager['env']}
                    assert env == {'PREWARM_DEVDB': 'false', 'ALLOW_CUSTOM_CONFIG': 'false', 'WATCH_SECRETS': 'false'}
                    assert not any('secrets' in rule.get('resources', [])
                                   for d in docs if d['kind'] == 'ClusterRole' for rule in d['rules'])
                    assert not any(d['kind'] in {'Secret', 'PersistentVolumeClaim', 'Service', 'Ingress', 'AtlasSchema', 'AtlasMigration'} for d in docs)
                    assert {'AtlasSchema', 'AtlasMigration'} <= {d['spec']['names']['kind'] for d in docs if d['kind'] == 'CustomResourceDefinition'}
                if name == 'azure-service-operator':
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    pod = deployment['spec']['template']['spec']
                    manager = next(c for c in pod['containers'] if c['name'] == 'manager')
                    assert deployment['spec']['replicas'] == 1
                    assert manager['image'].endswith(':v2.21.1')
                    assert '--crd-pattern=resources.azure.com/*;managedidentity.azure.com/*;authorization.azure.com/*;storage.azure.com/*' in manager['args']
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
                if name in {'langfuse-reloader', 'backstage-reloader', 'testkube-reloader', 'devlake-reloader'}:
                    deployment = next(d for d in docs if d['kind'] == 'Deployment')
                    pod = deployment['spec']['template']['spec']
                    assert pod['securityContext']['runAsNonRoot'] is True
                    assert pod['securityContext']['seccompProfile']['type'] == 'RuntimeDefault'
                    for container in pod['containers']:
                        security = container['securityContext']
                        assert security['allowPrivilegeEscalation'] is False
                        assert security['readOnlyRootFilesystem'] is True
                        assert security['capabilities']['drop'] == ['ALL']
                if name == 'langfuse':
                    web = next(d for d in docs if d['kind'] == 'Deployment' and d['metadata']['name'] == 'langfuse-web')
                    assert web['metadata']['annotations']['secret.reloader.stakater.com/reload'] == 'langfuse-sso'
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
                            'passwords.generators.external-secrets.io'} <= crds, 'Secret delivery and password generation APIs must be installed'
                    schemas = {d['spec']['names']['kind']: next(v['schema']['openAPIV3Schema'] for v in d['spec']['versions'] if v.get('storage'))
                               for d in docs if d['kind'] == 'CustomResourceDefinition'}
                    for resource in backstage_resources:
                        if resource['kind'] not in {'ExternalSecret', 'SecretStore', 'Password'}:
                            continue
                        properties = schemas[resource['kind']]['properties']['spec']['properties']
                        assert resource['spec'].keys() <= properties.keys(), 'Backstage uses fields absent from pinned ESO CRDs'
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
