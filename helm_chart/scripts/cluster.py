#!/usr/bin/env python3
"""Seed and operate locally reconciled Argo platforms using the Azure CLI login."""
import argparse
import base64
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time

if __package__:
    from .azure import current_account
    from .credentials import prepare as prepare_credentials, prepare_sonarqube
else:
    from azure import current_account
    from credentials import prepare as prepare_credentials, prepare_sonarqube


def run(*args, capture=False):
    return subprocess.run(args, check=True, text=True,
                          stdout=subprocess.PIPE if capture else None).stdout


def wait_for(check, description, timeout=3600):
    print(description, flush=True)
    deadline = time.monotonic() + timeout
    while not check():
        if time.monotonic() >= deadline:
            raise TimeoutError(description)
        time.sleep(10)


class Cluster:
    def __init__(self, infra, kubeconfig):
        self.tf = [os.environ.get('TERRAFORM', 'terraform'), f'-chdir={infra}']
        self.connection = json.loads(run(*self.tf, 'output', '-json', 'aks_connection', capture=True))
        self.settings = json.loads(run(*self.tf, 'output', '-json', 'platform_settings', capture=True))
        self.account = current_account()
        self.connection.update(self.account)
        connection = self.connection
        run('az', 'aks', 'get-credentials', '--subscription', connection['subscription_id'],
            '--resource-group', connection['resource_group'], '--name', connection['name'],
            '--file', str(kubeconfig), '--overwrite-existing')
        run('kubelogin', 'convert-kubeconfig', '--kubeconfig', str(kubeconfig), '-l', 'azurecli')
        self.kube = ['kubectl', '--kubeconfig', str(kubeconfig)]
        self.hub = (['kubectl', '--kubeconfig', os.environ['HUB_KUBECONFIG']]
                    if os.environ.get('HUB_KUBECONFIG') else None)

    def objects(self, kind, namespace=None):
        scope = ['-n', namespace] if namespace else ['-A']
        return json.loads(run(*self.kube, 'get', kind, *scope, '-o', 'json', capture=True))['items']

    def object(self, kind, name, namespace, kube=None):
        value = run(*(kube or self.kube), 'get', kind, name, '-n', namespace,
                    '--ignore-not-found', '-o', 'json', capture=True)
        return json.loads(value) if value.strip() else None

    def manifest(self, doc, kube=None):
        # Avoid last-applied copies, especially for cluster registration Secrets.
        result = subprocess.run([*(kube or self.kube), 'apply', '--server-side',
                                 '--field-manager=platform-lifecycle', '--force-conflicts', '-f', '-'],
                                input=json.dumps(doc), text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError('Applying lifecycle manifest failed; inspect Kubernetes events')

    def delete(self, kind, names, namespace=None):
        api = run(*self.kube, 'api-resources', '-o', 'name', capture=True).splitlines()
        if not any(r == kind or r.startswith(kind + '.') for r in api):
            return
        present = {o['metadata']['name'] for o in self.objects(kind, namespace)}
        names = sorted(set(names) & present)
        if not names:
            return
        scope = ['-n', namespace] if namespace else []
        run(*self.kube, 'delete', kind, *names, *scope, '--ignore-not-found', '--wait=false')
        wait_for(lambda: not any(o['metadata']['name'] in names for o in self.objects(kind, namespace)),
                 f'Waiting for {kind}: {", ".join(names)}')

    def sso(self):
        # Read public SAML metadata only. Private signing keys remain in Entra.
        display_name = self.settings['sonarqube_sso_display_name']
        principals = json.loads(run('az', 'ad', 'sp', 'list', '--display-name', display_name,
                                    '-o', 'json', capture=True))
        principals = [p for p in principals if p['displayName'] == display_name]
        if len(principals) != 1:
            raise RuntimeError('Expected one retained SonarQube Entra service principal')
        principal = json.loads(run('az', 'rest', '--method', 'GET', '--url',
            'https://graph.microsoft.com/v1.0/servicePrincipals/' + principals[0]['id'] +
            '?$select=appId,keyCredentials,preferredTokenSigningKeyThumbprint', capture=True))
        thumbprint = (principal.get('preferredTokenSigningKeyThumbprint') or '').upper()
        certs = [c for c in principal.get('keyCredentials', [])
                 if c.get('usage') == 'Verify' and c.get('key')
                 and base64.b64decode(c.get('customKeyIdentifier') or '').hex().upper() == thumbprint]
        if len(certs) != 1 or not certs[0].get('key'):
            raise RuntimeError('Cannot find the active public Entra SAML certificate')
        return {'SONAR_SAML_APPLICATION_ID': 'api://' + principal['appId'],
                'SONAR_SAML_CERTIFICATE': certs[0]['key']}

    def oidc_issuer(self):
        connection = self.connection
        result = subprocess.run(['az', 'aks', 'show', '--subscription', connection['subscription_id'],
            '--resource-group', connection['resource_group'], '--name', connection['name'],
            '--query', 'oidcIssuerProfile.issuerUrl', '-o', 'tsv', '--only-show-errors'],
            text=True, capture_output=True)
        issuer = result.stdout.strip()
        if result.returncode or not issuer.startswith('https://'):
            raise RuntimeError('Cannot read the AKS OIDC issuer required by ASO-managed federation')
        return issuer

    def root(self, runtime, sonar_ready):
        settings = self.settings
        return {'apiVersion': 'argoproj.io/v1alpha1', 'kind': 'Application',
                'metadata': {'name': 'platform', 'namespace': 'argocd'},
                'spec': {'project': 'default',
                    'destination': {'server': 'https://kubernetes.default.svc', 'namespace': 'argocd'},
                    'source': {'repoURL': settings['git']['repository'],
                               'targetRevision': settings['git']['revision'], 'path': 'helm_chart/platform',
                               'helm': {'valuesObject': {'cluster': settings['cluster'], 'git': settings['git'],
                                                        'runtime': runtime, 'sonarqubeReady': sonar_ready}}},
                    'syncPolicy': {'automated': {'prune': True, 'selfHeal': True},
                                   'retry': {'limit': 10, 'backoff': {'duration': '10s', 'factor': 2, 'maxDuration': '3m'}}}}}

    def seed(self, root):
        if not self.hub:
            self.manifest(root)
            if self.settings['cluster']['role'] == 'management':
                self.manifest({'apiVersion': 'v1', 'kind': 'ConfigMap',
                    'metadata': {'name': 'platform-hub', 'namespace': 'argocd'},
                    'data': {'identity': json.dumps(self.settings['argocd_identity']),
                             'tenant_id': self.account['tenant_id']}})
            return
        if self.settings['cluster']['role'] != 'workload':
            raise RuntimeError('HUB_KUBECONFIG is only valid for workload clusters')
        hub = json.loads(run(*self.hub, '-n', 'argocd', 'get', 'configmap', 'platform-hub', '-o', 'json', capture=True))
        identity = json.loads(hub['data']['identity'])
        # This library uses Kubernetes RBAC with Entra authentication (Azure RBAC off).
        self.manifest({'apiVersion': 'rbac.authorization.k8s.io/v1', 'kind': 'ClusterRoleBinding',
            'metadata': {'name': 'argocd-management-onboarding'},
            'roleRef': {'apiGroup': 'rbac.authorization.k8s.io', 'kind': 'ClusterRole', 'name': 'cluster-admin'},
            'subjects': [{'kind': 'User', 'apiGroup': 'rbac.authorization.k8s.io', 'name': identity['principal_id']}]})
        config = {'execProviderConfig': {'command': 'argocd-k8s-auth', 'args': ['azure'],
                  'apiVersion': 'client.authentication.k8s.io/v1beta1',
                  'env': {'AAD_LOGIN_METHOD': 'workloadidentity', 'AZURE_CLIENT_ID': identity['client_id'],
                          'AZURE_TENANT_ID': hub['data']['tenant_id'],
                          'AZURE_FEDERATED_TOKEN_FILE': '/var/run/secrets/azure/tokens/azure-identity-token'}},
                  'tlsClientConfig': {'insecure': False, 'caData': self.connection['ca_data']}}
        name = self.settings['cluster']['name']
        self.manifest({'apiVersion': 'v1', 'kind': 'Secret', 'type': 'Opaque',
            'metadata': {'name': 'cluster-' + name, 'namespace': 'argocd',
                'labels': {'argocd.argoproj.io/secret-type': 'cluster', 'platform.ai-platform-portfolio/role': 'workload'},
                'annotations': {'platform.ai-platform-portfolio/root': json.dumps(root)}},
            'stringData': {'name': name, 'server': self.connection['server'], 'config': json.dumps(config)}}, self.hub)

    def ready(self):
        def current(app):
            status = app.get('status', {})
            compared = status.get('sync', {}).get('comparedTo', {})
            spec = app['spec']
            return (status.get('sync', {}).get('status') == 'Synced'
                    and status.get('health', {}).get('status') == 'Healthy'
                    and (not spec.get('source') or compared.get('source') == spec['source'])
                    and (not spec.get('sources') or compared.get('sources') == spec['sources']))

        def reconciled():
            apps = self.objects('applications.argoproj.io', 'argocd')
            root = next((a for a in apps if a['metadata']['name'] == 'platform'), None)
            if not root or not root.get('status', {}).get('resources'):
                return False
            expected = {r['name'] for r in root['status']['resources'] if r['kind'] == 'Application'}
            if not {'sonarqube', 'sonarqube-configure'} <= expected:
                return False
            found = {a['metadata']['name']: a for a in apps}
            return bool(expected) and expected <= found.keys() and all(
                current(a) for a in [root, *(found[n] for n in expected)])
        wait_for(reconciled, 'Waiting for every platform Application to be Synced and Healthy')

    def cleanup(self):
        # Detach hub onboarding before suspending the local root. Workload packages
        # are retained if management is unavailable; teardown then fails closed.
        if self.settings['cluster']['role'] == 'workload' and not self.hub:
            raise RuntimeError('Set HUB_KUBECONFIG to detach workload onboarding before teardown')
        if self.hub:
            name = self.settings['cluster']['name']
            run(*self.hub, '-n', 'argocd', 'delete', 'secret', 'cluster-' + name, '--ignore-not-found')
            wait_for(lambda: self.object('application', 'onboard-' + name, 'argocd', self.hub) is None,
                     'Waiting for hub onboarding to detach')
        apps = self.objects('applications.argoproj.io', 'argocd')
        if any(a['metadata']['name'] == 'azure-service-operator' for a in apps):
            # External consumer CRs must finish deletion first. Only exact Azure
            # resources tracked by the platform-owned Atlantis Application may
            # remain: reverse waves delete them before ASO and its credential.
            owned = {(r.get('group'), r.get('kind'), r.get('namespace'), r.get('name'))
                     for app in apps if app['metadata']['name'] == 'atlantis-azure'
                     and app['metadata'].get('labels', {}).get('platform.ai-platform-portfolio/managed') == 'true'
                     for r in app.get('status', {}).get('resources', [])}
            resources = run(*self.kube, 'api-resources', '--namespaced=true',
                            '--verbs=list', '-o', 'name', capture=True).splitlines()
            for resource in resources:
                if not resource.endswith('.azure.com'):
                    continue
                for obj in self.objects(resource):
                    key = (obj.get('apiVersion', '').split('/')[0], obj.get('kind'),
                           obj['metadata'].get('namespace'), obj['metadata']['name'])
                    if key not in owned:
                        raise RuntimeError('Remove ASO Azure resources and wait for finalizers before teardown')
        roots = [a for a in apps if a['metadata']['name'] == 'platform']

        def suspend(applications):
            for app in applications:
                run(*self.kube, '-n', 'argocd', 'patch', 'application', app['metadata']['name'],
                    '--type=merge', '-p', '{"spec":{"syncPolicy":{"automated":null}}}')
            names = {a['metadata']['name'] for a in applications}

            def stopped():
                idle = True
                for app in self.objects('applications.argoproj.io', 'argocd'):
                    if app['metadata']['name'] not in names:
                        continue
                    phase = app.get('status', {}).get('operationState', {}).get('phase')
                    if not app.get('operation') and phase not in {'Running', 'Terminating'}:
                        continue
                    idle = False
                    if phase == 'Terminating':
                        continue
                    # Match Argo's termination transition for running syncs;
                    # cancel queued operations before the controller starts them.
                    change = ({'status': {'operationState': {'phase': 'Terminating'}}}
                              if phase == 'Running' else {'operation': None})
                    # Fail closed if the controller changed the operation since
                    # this read, rather than overwriting its newer state.
                    change['metadata'] = {'resourceVersion': app['metadata']['resourceVersion']}
                    run(*self.kube, '-n', 'argocd', 'patch', 'application', app['metadata']['name'],
                        '--type=merge', '-p', json.dumps(change))
                return idle

            wait_for(stopped, 'Waiting for Argo operations to stop')
        # Stop the root before taking the child inventory; it may create
        # additional Applications while termination is being processed.
        suspend(roots)
        managed = [a for a in self.objects('applications.argoproj.io', 'argocd')
                   if a['metadata'].get('labels', {}).get('platform.ai-platform-portfolio/managed') == 'true']
        suspend(managed)
        # Remove clients before databases and keep operators until their CRs disappear.
        for app in sorted(managed, key=lambda a: int(a['metadata'].get('annotations', {}).get('argocd.argoproj.io/sync-wave', 0)), reverse=True):
            name = app['metadata']['name']
            self.delete('applications.argoproj.io', [name], 'argocd')
            if name == 'routes':
                deadline = time.monotonic() + 70
                wait_for(lambda: time.monotonic() >= deadline, 'Allowing ExternalDNS a record cleanup interval', timeout=100)
        self.delete('applications.argoproj.io', ['platform'], 'argocd')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['up', 'down'])
    parser.add_argument('--infra-dir', type=Path, required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='platform-aks-') as directory:
        cluster = Cluster(args.infra_dir.resolve(), Path(directory) / 'kubeconfig')
        if args.action == 'down':
            cluster.cleanup()
            return
        runtime = {**cluster.settings['runtime'], **cluster.sso(),
                   'AZURE_TENANT_ID': cluster.account['tenant_id'],
                   'AZURE_SUBSCRIPTION_ID': cluster.account['subscription_id'],
                   **{'STORAGE_' + key.upper(): value for key, value in cluster.settings['storage_containers'].items()}}
        # Generic cluster context for GitOps-owned federated identities. Package
        # enablement and deployment choices are never read from Terraform outputs.
        if cluster.settings['cluster']['role'] == 'management':
            runtime['AKS_OIDC_ISSUER_URL'] = cluster.oidc_issuer()
        prepare_credentials(cluster)
        existing = next((app for app in cluster.objects('applications.argoproj.io', 'argocd')
                         if app['metadata']['name'] == 'platform'), None)
        sonar_ready = bool(existing and existing['spec']['source']['helm']['valuesObject'].get('sonarqubeReady'))
        cluster.seed(cluster.root(runtime, sonar_ready))
        prepare_sonarqube(cluster, run, wait_for)
        cluster.seed(cluster.root(runtime, True))
        cluster.ready()
        print('Platform reconciled; portfolio data is disposable on teardown.', flush=True)


if __name__ == '__main__':
    main()
