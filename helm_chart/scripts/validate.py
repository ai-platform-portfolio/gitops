#!/usr/bin/env python3
"""Render the complete platform and pinned upstream charts without Azure access."""
from concurrent.futures import ThreadPoolExecutor
import base64
import json
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

ROOT = Path(__file__).resolve().parents[2]
HELM_CHART = ROOT / 'helm_chart'


def command(*args):
    return subprocess.run(args, check=True, text=True, capture_output=True).stdout


def main():
    bootstrap = ROOT / 'bootstrap'
    bootstrap_values = yaml.safe_load((bootstrap / 'argocd-values.yaml').read_text())
    configmap = yaml.safe_load((bootstrap / 'argocd-configmap.yaml').read_text())
    assert bootstrap_values['configs']['cm'] == {'create': False}, 'Argo configuration must live in the ConfigMap file'
    assert configmap['kind'] == 'ConfigMap' and configmap['metadata']['name'] == 'argocd-cm'
    outputs = (bootstrap / 'outputs.tf').read_text()
    assert 'data.azurerm_client_config' not in outputs and 'var.account' not in outputs and 'oidc_issuer_url' not in outputs, 'Account identifiers must not be Terraform outputs'
    runtime = {name: 'example' for path in (HELM_CHART / 'platform/packages').rglob('*') if path.is_file()
               for name in re.findall(r'\.Values.runtime\.([A-Z_]+)', path.read_text())}
    runtime.update(DNS_ZONE='example.test', ACME_EMAIL='ops@example.test', CLUSTER_NAME='test',
                   AZURE_KEY_VAULT_URI='https://example.vault.azure.net/',
                   SONAR_SAML_CERTIFICATE='YWJj', SONAR_SAML_APPLICATION_ID='api://example',
                   AZURE_LOCATION='uksouth', AKS_OIDC_ISSUER_URL='https://issuer.example.test/')
    for key in ['AZURE_TENANT_ID', 'AZURE_SUBSCRIPTION_ID', 'AZURE_CLIENT_ID',
                'POSTGRES_AZURE_CLIENT_ID']:
        runtime[key] = '00000000-0000-0000-0000-000000000001'
    with tempfile.TemporaryDirectory(prefix='platform-render-') as directory:
        tmp = Path(directory)
        values = tmp / 'values.json'
        atlantis = {
            'enabled': True, 'github_app_id': '12345', 'github_app_slug': 'platform-atlantis',
            'github_team': 'platform',
            'repositories': ['github.com/example/*', '!github.com/example/gitops', '!github.com/example/terraform'],
            'azure': {
                'identity_resource_group_name': 'rg-service-identities',
                'role_assignments': {
                    'state': {'resource_group_name': 'rg-state', 'storage_account_name': 'examplestate',
                              'role_definition_name': 'Storage Blob Data Contributor'},
                    'deployment': {'resource_group_name': 'rg-developer', 'role_definition_name': 'Contributor'},
                },
            },
        }
        values.write_text(json.dumps({'runtime': runtime, 'sonarqubeReady': True}))
        disabled = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))))
        assert 'atlantis' not in {app['metadata']['name'] for app in disabled}, 'Atlantis must be opt-in'
        values.write_text(json.dumps({'runtime': runtime, 'sonarqubeReady': True, 'atlantis': atlantis}))
        argo_values = tmp / 'argocd.json'
        argo_values.write_text(json.dumps({**bootstrap_values, 'extraObjects': [configmap]}))
        version = re.search(r'chart_version\s*=\s*"([^"]+)"', (bootstrap / 'terraform.tfvars').read_text())[1]
        argo = list(yaml.safe_load_all(command('helm', 'template', 'argocd', 'argo-cd', '--repo',
            'https://argoproj.github.io/argo-helm', '--version', version, '--namespace', 'argocd', '-f', str(argo_values))))
        maps = [d for d in argo if d and d['kind'] == 'ConfigMap' and d['metadata']['name'] == 'argocd-cm']
        assert len(maps) == 1 and maps[0]['data'] == configmap['data'], 'Helm must install exactly the file-defined Argo ConfigMap'
        applications = list(yaml.safe_load_all(command('helm', 'template', 'platform', str(HELM_CHART / 'platform'), '-f', str(values))))
        names = {app['metadata']['name'] for app in applications}
        assert len(applications) == len(names), 'Duplicate Application names'
        expected = {
            'namespaces', 'federation', 'envoy-gateway', 'external-secrets',
            'clickhouse-operator', 'cert-manager', 'cloudnative-pg', 'configuration',
            'external-dns', 'gateway', 'plugin-barman-cloud', 'postgres', 'clickhouse',
            'loki', 'grafana', 'tempo', 'mimir', 'langfuse', 'opentelemetry-collector',
            'k8s-monitoring', 'sonarqube', 'sonarqube-configure', 'routes',
            'azure-service-operator', 'atlantis', 'atlantis-azure',
        }
        assert names == expected, 'A platform Application was omitted or renamed'
        waves = {app['metadata']['name']: int(app['metadata']['annotations']['argocd.argoproj.io/sync-wave'])
                 for app in applications}
        for app in applications:
            assert app['spec']['destination']['server'] == 'https://kubernetes.default.svc', 'Packages must reconcile locally'
            source = app['spec'].get('source') or app['spec']['sources'][0]
            if 'path' in source:
                v = tmp / (app['metadata']['name'] + '.json')
                v.write_text(json.dumps(source['helm']['valuesObject']))
                documents = list(yaml.safe_load_all(command('helm', 'template', app['metadata']['name'],
                    str(ROOT / source['path']), '-f', str(v))))
                for doc in documents:
                    assert doc and 'fluxcd.io' not in doc['apiVersion'], 'Flux resource survived migration'
                    assert doc['kind'] != 'Certificate' or all('*' not in host for host in doc['spec']['dnsNames']), 'Wildcard certificates are forbidden'
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
                if app['metadata']['name'] == 'atlantis-azure':
                    assert {d['kind'] for d in documents} == {'UserAssignedIdentity', 'FederatedIdentityCredential', 'RoleAssignment'}
                    identity = next(d for d in documents if d['kind'] == 'UserAssignedIdentity')
                    assert identity['metadata']['namespace'] == 'atlantis'
                    assert identity['spec']['operatorSpec']['configMaps']['clientId'] == {'name': 'atlantis-identity', 'key': 'clientId'}
                    federation = next(d for d in documents if d['kind'] == 'FederatedIdentityCredential')
                    assert federation['spec']['owner'] == {'name': 'atlantis'}
                    assert federation['spec']['subject'] == 'system:serviceaccount:atlantis:atlantis'
                    assert federation['spec']['issuer'] == runtime['AKS_OIDC_ISSUER_URL']
                    roles = [d for d in documents if d['kind'] == 'RoleAssignment']
                    assert len(roles) == 2
                    assert {r['spec']['roleDefinitionReference']['wellKnownName'] for r in roles} == {'Contributor', 'Storage Blob Data Contributor'}
                    assert all(r['spec']['principalIdFromConfig'] == {'name': 'atlantis-identity', 'key': 'principalId'} for r in roles)
                    assert all(re.fullmatch(r'[0-9a-f]{8}(-[0-9a-f]{4}){3}-[0-9a-f]{12}', r['spec']['azureName']) for r in roles)
                    state_role = next(r for r in roles if r['metadata']['name'] == 'atlantis-state')
                    assert state_role['spec']['owner']['armId'].endswith('/resourceGroups/rg-state/providers/Microsoft.Storage/storageAccounts/examplestate')
                    assert not any('/containers/' in r['spec']['owner']['armId'] for r in roles)
                if app['metadata']['name'] == 'configuration':
                    secrets = {d['metadata']['name']: d for d in documents if d['kind'] == 'ExternalSecret'}
                    assert {'atlantis-github', 'atlantis-web'} <= secrets.keys()
                    credential = next(d for d in documents if d['kind'] == 'Secret' and d['metadata']['name'] == 'aso-credential')
                    assert credential['metadata']['namespace'] == 'atlantis'
                    assert base64.b64decode(credential['data']['AZURE_CLIENT_ID']).decode() == runtime['ASO_AZURE_CLIENT_ID']
                    assert base64.b64decode(credential['data']['USE_WORKLOAD_IDENTITY_AUTH']).decode() == 'true'
                    assert {d['secretKey'] for d in secrets['atlantis-github']['spec']['data']} == {'key.pem', 'github_secret'}
                    assert {d['secretKey'] for d in secrets['atlantis-web']['spec']['data']} == {'username', 'password'}
                if app['metadata']['name'] == 'gateway':
                    gateway = next(d for d in documents if d['kind'] == 'Gateway')
                    assert gateway['spec']['allowedListeners']['namespaces']['from'] == 'Selector'
                    assert all(listener['protocol'] == 'HTTP' for listener in gateway['spec']['listeners'])
                if app['metadata']['name'] == 'routes':
                    listeners = [d for d in documents if d['kind'] == 'ListenerSet']
                    assert len(listeners) == 4
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
        assert 'atlantis' not in {a['metadata']['name'] for a in workload}, 'Workload must not install Atlantis even when enabled'
        assert not any('namespace: atlantis' in json.dumps(a) for a in workload), 'Workload must not create Atlantis prerequisites or routes'
        assert waves['configuration'] < waves['atlantis-azure'] < waves['atlantis'] < waves['routes']
        assert waves['azure-service-operator'] < waves['atlantis-azure']
        assert 'federation' not in {a['metadata']['name'] for a in workload}, 'Workload must not manage the hub'
        assert 'azure-service-operator' not in {a['metadata']['name'] for a in workload}, 'ASO belongs on management only'
        workload_namespaces = next(a for a in workload if a['metadata']['name'] == 'namespaces')
        assert 'azureserviceoperator-system' not in workload_namespaces['spec']['source']['helm']['valuesObject']['manifests']
        assert not any(a['metadata']['name'].startswith('sonarqube') for a in workload), 'Sonar must wait for cluster credential preparation'
        initial_postgres = next(a for a in workload if a['metadata']['name'] == 'postgres')
        initial_database_docs = [d for d in yaml.safe_load_all(initial_postgres['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert not any(d['kind'] == 'Database' for d in initial_database_docs), 'Sonar database waits for credentials'
        assert 'managed' not in next(d for d in initial_database_docs if d['kind'] == 'Cluster')['spec']
        routes = next(a for a in workload if a['metadata']['name'] == 'routes')
        initial_routes = [d for d in yaml.safe_load_all(routes['spec']['source']['helm']['valuesObject']['manifests']) if d]
        assert not any(d['metadata']['namespace'] == 'sonarqube' for d in initial_routes), 'Initial routes must not block on a Sonar backend that has not been installed'
        assert waves['routes'] > waves['sonarqube'], 'Activate routes after backend health'
        assert waves['envoy-gateway'] < waves['cert-manager'] < waves['configuration'], 'Install Gateway API CRDs before cert-manager and issuers after it'
        assert waves['azure-service-operator'] > waves['cert-manager'], 'ASO certificates require cert-manager'


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
                    assert base64.b64decode(settings['AZURE_TARGET_NAMESPACES']).decode() == 'azure-resources,atlantis'
                    assert not any(d['kind'] in {'PodDisruptionBudget', 'NetworkPolicy', 'CustomResourceDefinition'} for d in docs)
                    assert any(d['kind'] == 'Certificate' for d in docs), 'ASO needs internal webhook TLS'
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
                if name == 'plugin-barman-cloud':
                    assert any(d['kind'] == 'CustomResourceDefinition'
                               and d['metadata']['name'] == 'objectstores.barmancloud.cnpg.io' for d in docs), 'Barman must expose backup configuration API'
                if name == 'cert-manager':
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
                    container = pod['spec']['containers'][0]
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
                    assert repo['apply_requirements'] == ['approved', 'mergeable', 'undiverged']
                    assert repo['allowed_overrides'] == [] and repo['allow_custom_workflows'] is False
                    assert repo['repo_locks']['mode'] == 'on_plan'
                if name == 'sonarqube':
                    assert not any(d['kind'] == 'Job' and 'change-admin-password' in d['metadata']['name'] for d in docs)
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
    print(f'{len(applications)} management Applications rendered; workload isolation and upstream charts verified.')


if __name__ == '__main__':
    main()
