#!/usr/bin/env python3
"""Seed and operate locally reconciled Argo platforms using the Azure CLI login."""
import argparse
import base64
import json
import os
import re
import socket
from pathlib import Path
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

if __package__:
    from . import certificates
    from .azure import current_account
    from .credentials import prepare as prepare_credentials, prepare_sonarqube
else:
    import certificates
    from azure import current_account
    from credentials import prepare as prepare_credentials, prepare_sonarqube


def run(*args, capture=False, timeout=None):
    return subprocess.run(args, check=True, text=True, timeout=timeout,
                          stdout=subprocess.PIPE if capture else None).stdout


def foundry_account_name(vault_uri):
    # Scoped Terraform applies can leave aggregate outputs stale; Key Vault holds
    # the generated account name that the Foundry apply actually created.
    secret_id = vault_uri.rstrip('/') + '/secrets/foundry-management-account-name'
    name = run('az', 'keyvault', 'secret', 'show', '--id', secret_id,
               '--query', 'value', '-o', 'tsv', '--only-show-errors',
               capture=True, timeout=30).strip()
    if not re.fullmatch(r'[a-z][a-z0-9-]{0,62}[a-z0-9]', name):
        raise RuntimeError('Key Vault returned an invalid Foundry account name')
    return name


def wait_for(check, description, timeout=3600, retry_command_errors=False):
    print(description, flush=True)
    deadline = time.monotonic() + timeout
    command_failures = 0
    while True:
        try:
            if check():
                return
            command_failures = 0
        except subprocess.CalledProcessError:
            if not retry_command_errors:
                raise
            # A busy AKS API can briefly return 503 while Argo installs CRDs.
            # Keep the health wait bounded, but fail on a persistent command error.
            command_failures += 1
            if command_failures >= 6:
                raise
        if time.monotonic() >= deadline:
            raise TimeoutError(description)
        time.sleep(10)


def dns_fields(record):
    return [set(''.join(txt.get('value', [])).strip('"').split(','))
            for txt in record.get('TXTRecords', record.get('txtRecords', []))]


def owned_dns(account, config):
    """Return verified IPv4 registry pairs; never infer ownership from a name alone."""
    if not config.get('owner') or not all(config.get('zone', {}).get(k) for k in ['name', 'resource_group_name']):
        raise ValueError('DNS owner and zone scope must be explicit')
    records = json.loads(run('az', 'network', 'dns', 'record-set', 'list',
        '--subscription', account['subscription_id'], '-g', config['zone']['resource_group_name'],
        '-z', config['zone']['name'], '-o', 'json', '--only-show-errors', capture=True, timeout=30))
    owner = 'external-dns/owner=' + config['owner']
    pairs = []
    for marker in records:
        fields = dns_fields(marker)
        if not any(owner in entry for entry in fields):
            continue
        if (not marker['type'].endswith('/TXT') or not marker['name'].startswith('external-dns-a-')
                or not all({f for f in entry if f.startswith('external-dns/owner=')} == {owner}
                           and 'heritage=external-dns' in entry
                           and any(f.startswith('external-dns/resource=httproute/') for f in entry) for entry in fields)):
            raise RuntimeError('Owned DNS registry contains unsupported or mixed ownership; records were not changed')
        label = marker['name'].removeprefix('external-dns-a-')
        targets = [r for r in records if r['name'] == label and r['type'].endswith('/A')]
        if not label or len(targets) > 1 or not marker.get('etag') or any(not r.get('etag') for r in targets):
            raise RuntimeError('Cannot verify owned DNS record identity/ETags; records were not changed')
        pairs.append((marker, targets[0] if targets else None))
    return pairs


def cleanup_stale_dns(account, config):
    # Only used after empty state AND absence of the disposable resource group.
    scope = ['--subscription', account['subscription_id'], '-g', config['zone']['resource_group_name'],
             '-z', config['zone']['name'], '--only-show-errors']
    for marker, target in owned_dns(account, config):
        current = json.loads(run('az', 'network', 'dns', 'record-set', 'txt', 'show',
                                 '-n', marker['name'], *scope, '-o', 'json', capture=True, timeout=30))
        if current.get('etag') != marker['etag']:
            raise RuntimeError('DNS ownership changed during cleanup; stopped without removing its record')
        if target:
            run('az', 'network', 'dns', 'record-set', 'a', 'delete', '-n', target['name'],
                *scope, '--if-match', target['etag'], '--yes', capture=True, timeout=30)
        run('az', 'network', 'dns', 'record-set', 'txt', 'delete', '-n', marker['name'],
            *scope, '--if-match', marker['etag'], '--yes', capture=True, timeout=30)
    if owned_dns(account, config):
        raise RuntimeError('Owned DNS records remain after stale-record cleanup')


def already_down(infra, tfvars):
    tf = [os.environ.get('TERRAFORM', 'terraform'), f'-chdir={infra}']
    outputs = json.loads(run(*tf, 'output', '-json', capture=True, timeout=30))
    if 'aks_connection' in outputs:
        return False
    if run(*tf, 'state', 'list', capture=True).strip():
        return False  # Partial destruction must pass the teardown-only recovery checks.
    account = current_account()
    env = {**os.environ, 'ARM_SUBSCRIPTION_ID': account['subscription_id'], 'ARM_TENANT_ID': account['tenant_id']}
    # Terraform owns input parsing and TFVARS precedence, even after outputs vanish.
    expression = 'jsonencode({owner=var.platform.cluster_name, zone=var.dns.zone, group=var.azure.resource_group_name})'
    result = subprocess.run([*tf, 'console', f'-var-file={tfvars}'], input=expression + '\n',
                            text=True, capture_output=True, env=env, timeout=60)
    if result.returncode:
        raise RuntimeError('Cannot read teardown inputs from Terraform; DNS was not changed')
    config = json.loads(json.loads(result.stdout))
    exists = json.loads(run('az', 'group', 'exists', '-n', config['group'],
                            '--subscription', account['subscription_id'], '-o', 'json', '--only-show-errors', capture=True, timeout=30))
    if exists:
        raise RuntimeError('Disposable resource group still exists beside empty state; DNS was not changed')
    cleanup_stale_dns(account, config)
    print('Already down: empty state, resource group absent, owned DNS drained; backend preserved.', flush=True)
    return True


def teardown_connection(tf):
    # State can contain secrets: capture internally and retain only connection metadata.
    state = json.loads(run(*tf, 'show', '-json', capture=True, timeout=60))
    modules = [state['values']['root_module']]
    resources = []
    while modules:
        module = modules.pop()
        resources.extend(r for r in module.get('resources', []) if r.get('mode') == 'managed')
        modules.extend(module.get('child_modules', []))
    if any(r['type'].startswith(('helm_', 'kubernetes_')) for r in resources):
        raise RuntimeError('Bootstrap outputs missing with tracked Helm/Kubernetes resources; cannot recover teardown')
    clusters = [r['values'] for r in resources if r['type'] == 'azurerm_kubernetes_cluster']
    if len(clusters) > 1:
        raise RuntimeError('Ambiguous AKS state; cannot recover teardown')
    if not clusters:
        return None  # Terraform already removed AKS, or never created it.
    cluster = clusters[0]
    if not all(cluster.get(k) for k in ['id', 'name', 'resource_group_name']):
        raise RuntimeError('Incomplete AKS state; cannot recover teardown')
    return {'id': cluster['id'], 'name': cluster['name'], 'resource_group': cluster['resource_group_name']}


class Cluster:
    def __init__(self, infra, kubeconfig, teardown=False):
        self.tf = [os.environ.get('TERRAFORM', 'terraform'), f'-chdir={infra}']
        if teardown:
            outputs = json.loads(run(*self.tf, 'output', '-json', capture=True, timeout=30))
            complete = {'aks_connection', 'platform_settings'} <= outputs.keys()
            self.connection = outputs['aks_connection']['value'] if complete else teardown_connection(self.tf)
            self.settings = outputs['platform_settings']['value'] if complete else None
        else:
            self.connection = json.loads(run(*self.tf, 'output', '-json', 'aks_connection', capture=True))
            self.settings = json.loads(run(*self.tf, 'output', '-json', 'platform_settings', capture=True))
        self.account = current_account()
        if self.settings is None:
            if os.environ.get('HUB_KUBECONFIG'):
                raise RuntimeError('Cannot verify hub detachment without bootstrap outputs; teardown stopped')
            if self.connection is None:
                self.kube, self.hub = None, None
                return
            expected = (f"/subscriptions/{self.account['subscription_id']}/resourceGroups/"
                        f"{self.connection['resource_group']}/providers/Microsoft.ContainerService/"
                        f"managedClusters/{self.connection['name']}")
            if self.connection['id'].lower() != expected.lower():
                raise RuntimeError('AKS state does not match authenticated account; teardown stopped')
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
        return json.loads(run(*self.kube, 'get', kind, *scope, '--request-timeout=15s', '-o', 'json', capture=True))['items']

    def object(self, kind, name, namespace, kube=None):
        value = run(*(kube or self.kube), 'get', kind, name, '-n', namespace,
                    '--ignore-not-found', '--request-timeout=15s', '-o', 'json', capture=True)
        return json.loads(value) if value.strip() else None

    def manifest(self, doc, kube=None):
        # Avoid last-applied copies, especially for cluster registration Secrets.
        result = subprocess.run([*(kube or self.kube), 'apply', '--server-side',
                                 '--field-manager=platform-lifecycle', '--force-conflicts', '-f', '-'],
                                input=json.dumps(doc), text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError('Applying lifecycle manifest failed; inspect Kubernetes events')

    def delete(self, kind, names, namespace=None, foreground=False):
        api = run(*self.kube, 'api-resources', '-o', 'name', capture=True).splitlines()
        if not any(r == kind or r.startswith(kind + '.') for r in api):
            return
        present = {o['metadata']['name'] for o in self.objects(kind, namespace)}
        names = sorted(set(names) & present)
        if not names:
            return
        scope = ['-n', namespace] if namespace else []
        propagation = ['--cascade=foreground'] if foreground else []
        run(*self.kube, 'delete', kind, *names, *scope, *propagation, '--ignore-not-found', '--wait=false')
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
            self.resume_stale_root_sync()
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

    def resume_stale_root_sync(self, app=None):
        if app is None:
            app = self.object('applications.argoproj.io', 'platform', 'argocd')
        status = (app or {}).get('status', {})
        operation = status.get('operationState', {})
        active_revision = operation.get('syncResult', {}).get('revision')
        current_revision = status.get('sync', {}).get('revision')
        if operation.get('phase') != 'Running' or not active_revision or not current_revision:
            return
        if active_revision == current_revision:
            return

        # A prior wave can remain blocked on an old manifest after main advances.
        # Argo will not begin the new automated sync until that operation ends.
        print('Terminating stale platform sync so Argo can reconcile the current revision', flush=True)
        run(*self.kube, '-n', 'argocd', 'patch', 'applications.argoproj.io', 'platform',
            '--type=merge', '-p', json.dumps({'status': {'operationState': {'phase': 'Terminating'}}}))

        def old_operation_finished():
            latest = self.object('applications.argoproj.io', 'platform', 'argocd')
            state = (latest or {}).get('status', {}).get('operationState', {})
            return state.get('syncResult', {}).get('revision') != active_revision or state.get('phase') not in {'Running', 'Terminating'}

        wait_for(old_operation_finished, 'Waiting for stale platform sync to stop', timeout=120,
                 retry_command_errors=True)

    def ready(self):
        started = time.monotonic()
        last_report = None

        def report(blockers):
            nonlocal last_report
            summary = tuple(sorted(blockers))
            if summary != last_report:
                print(f'[{int(time.monotonic() - started)}s] Waiting: ' + ', '.join(summary), flush=True)
                last_report = summary

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
            if root:
                self.resume_stale_root_sync(root)
            if not root or not root.get('status', {}).get('resources'):
                report(['platform: resource inventory not ready'])
                return False
            expected = {r['name'] for r in root['status']['resources'] if r['kind'] == 'Application'}
            required = {'sonarqube', 'sonarqube-configure'}
            if self.settings['cluster']['role'] == 'management':
                required.add('argocd-route')
            if not required <= expected:
                report(['platform: required package inventory incomplete'])
                return False
            found = {a['metadata']['name']: a for a in apps}
            blockers = []
            for name in sorted(expected | {'platform'}):
                app = found.get(name)
                if not app or not current(app):
                    status = (app or {}).get('status', {})
                    blockers.append(f"{name}: {status.get('sync', {}).get('status', 'Missing')}/"
                                    f"{status.get('health', {}).get('status', 'Unknown')}")

            discovery_names = {r['name'] for r in root['status']['resources']
                               if r['kind'] == 'ApplicationSet'}
            if discovery_names:
                sets = {a['metadata']['name']: a for a in self.objects('applicationsets.argoproj.io', 'argocd')}
                for name in sorted(discovery_names):
                    conditions = {c['type']: c for c in sets.get(name, {}).get('status', {}).get('conditions', [])}
                    if any(conditions.get(kind, {}).get('status') != state for kind, state in [
                            ('ParametersGenerated', 'True'), ('ResourcesUpToDate', 'True'), ('ErrorOccurred', 'False')]):
                        blockers.append(f'{name}: repository discovery not reconciled')
                for app in apps:
                    if app['metadata'].get('labels', {}).get('platform.ai-platform-portfolio/registration') == 'true' and not current(app):
                        blockers.append(f"{app['metadata']['name']}: registration not reconciled")
            report(blockers)
            return bool(expected) and not blockers

        wait_for(reconciled, 'Waiting for every platform Application to be Synced and Healthy',
                 retry_command_errors=True)

    def check_dns(self, runtime):
        """Refuse to adopt an existing exact record owned by another deployment."""
        hostname = runtime['ARGOCD_HOSTNAME']
        zone = runtime['AZURE_DNS_ZONE']
        if not hostname.endswith('.' + zone):
            raise ValueError('Argo CD hostname must be below the configured Azure DNS zone')
        label = hostname[:-len(zone) - 1]
        result = subprocess.run(['az', 'network', 'dns', 'record-set', 'list',
            '--subscription', self.account['subscription_id'], '--resource-group', runtime['AZURE_DNS_RESOURCE_GROUP'],
            '--zone-name', zone, '--only-show-errors', '-o', 'json'], text=True, capture_output=True)
        if result.returncode:
            raise RuntimeError('Cannot check Argo CD DNS ownership; no root was seeded')
        records = json.loads(result.stdout)
        existing = [r for r in records if r['name'] == label
                    and r['type'].rsplit('/', 1)[-1].upper() in {'A', 'AAAA', 'CNAME'}]
        owner = 'external-dns/owner=' + self.settings['cluster']['name']
        resources = {'external-dns/resource=httproute/argocd/argocd',
                     'external-dns/resource=httproute/argocd/https-redirect'}
        ownership_records = [r for r in records if r['name'] in {
            'external-dns-' + label, 'external-dns-a-' + label,
            'external-dns-aaaa-' + label, 'external-dns-cname-' + label}]
        ownership = [fields for record in ownership_records for fields in dns_fields(record)]
        owned = bool(ownership) and all(owner in fields and resources & fields for fields in ownership)
        if (existing or ownership_records) and not owned:
            raise RuntimeError(f'DNS clash at {hostname}; choose a fresh argocd.dns_label. Existing records were not changed')

    def ingress_ready(self, runtime):
        hostname = runtime['ARGOCD_HOSTNAME']

        def available():
            # A repeated deployment may find its root still installing controllers.
            apis = run(*self.kube, 'api-resources', '-o', 'name', capture=True).splitlines()
            if not {'certificates.cert-manager.io', 'gateways.gateway.networking.k8s.io'} <= set(apis):
                return False
            certificate = self.object('certificates.cert-manager.io', 'argocd-tls', 'argocd')
            if not certificate or not any(c.get('type') == 'Ready' and c.get('status') == 'True'
                    and c.get('observedGeneration') == certificate['metadata']['generation']
                    for c in certificate.get('status', {}).get('conditions', [])):
                return False
            gateway = self.object('gateways.gateway.networking.k8s.io', 'public', 'envoy-gateway-system')
            addresses = {a['value'] for a in (gateway or {}).get('status', {}).get('addresses', [])
                         if a.get('type', 'IPAddress') == 'IPAddress'}
            if not addresses:
                return False
            try:
                resolved = {a[4][0] for a in socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)}
                if not resolved or not resolved <= addresses:
                    return False
                # Default TLS verification checks both the hostname and CA chain.
                with urllib.request.urlopen('https://' + hostname + '/healthz', timeout=10) as response:
                    return response.status == 200
            except (OSError, urllib.error.URLError):
                return False

        wait_for(available, 'Waiting for Argo CD certificate, DNS and verified HTTPS',
                 retry_command_errors=True)
        print('Argo CD: https://' + hostname, flush=True)

    def cancel_node_upgrades(self):
        """Release rolling-upgrade locks only after disposable workloads are gone."""
        if self.connection is None:
            return
        scope = ['--subscription', self.connection['subscription_id'], '--only-show-errors']
        cluster = json.loads(run('az', 'aks', 'show', '-g', self.connection['resource_group'],
            '-n', self.connection['name'], *scope, '-o', 'json', capture=True, timeout=30))
        group = cluster['nodeResourceGroup']
        owner = json.loads(run('az', 'group', 'show', '-n', group, *scope,
                              '--query', 'managedBy', '-o', 'json', capture=True, timeout=30))
        if not owner or owner.lower() != cluster['id'].lower():
            raise RuntimeError('AKS node resource group ownership is unverified; teardown stopped')
        pools = {pool['name'] for pool in cluster['agentPoolProfiles']}
        scales = json.loads(run('az', 'vmss', 'list', '-g', group, *scope,
                                '-o', 'json', capture=True, timeout=30))
        if any(scale.get('tags', {}).get('aks-managed-poolName') not in pools for scale in scales):
            raise RuntimeError('Unverified scale set in AKS node resource group; teardown stopped')

        def status(name):
            result = subprocess.run(['az', 'vmss', 'rolling-upgrade', 'get-latest',
                '-g', group, '-n', name, *scope, '-o', 'json'],
                text=True, capture_output=True, timeout=30)
            if result.returncode:
                # No upgrade history, or a scale set already removed by AKS.
                if result.stderr.startswith(('ERROR: (NotFound)', 'ERROR: (ResourceNotFound)')):
                    return None
                raise RuntimeError('Cannot read AKS scale set rolling upgrade; teardown stopped')
            code = json.loads(result.stdout)['runningStatus']['code']
            if code not in {'RollingForward', 'Completed', 'Cancelled', 'Faulted'}:
                raise RuntimeError('Unknown AKS scale set rolling upgrade status; teardown stopped')
            return code

        for scale in scales:
            name = scale['name']
            if status(name) != 'RollingForward':
                continue
            print(f'Cancelling rolling upgrade before deleting AKS scale set {name}', flush=True)
            # Use the native long-running-operation wait, bounded locally as well.
            run('az', 'vmss', 'rolling-upgrade', 'cancel', '-g', group, '-n', name,
                *scope, '-o', 'none', capture=True, timeout=180)
            if status(name) == 'RollingForward':
                raise RuntimeError('AKS scale set rolling upgrade is still active; teardown stopped')

    def verify_drained(self):
        if self.connection is None:
            return
        apis = set(run(*self.kube, 'api-resources', '-o', 'name', capture=True).splitlines())
        # Empty namespaces may remain after Helm uninstall; workloads may not.
        for kind in ['pods', 'deployments.apps', 'statefulsets.apps', 'daemonsets.apps', 'jobs.batch', 'cronjobs.batch']:
            if any(obj['metadata']['namespace'] != 'kube-system' for obj in self.objects(kind)):
                raise RuntimeError('Workloads remain without bootstrap outputs; teardown stopped')
        # Argo's AppProjects can survive Helm uninstall; they are configuration.
        consumers = {kind for kind in apis if kind != 'appprojects.argoproj.io' and kind.endswith(
            ('.argoproj.io', '.db.atlasgo.io', '.postgresql.cnpg.io', '.upbound.io'))}
        crds = self.objects('customresourcedefinitions.apiextensions.k8s.io')
        consumers.update(crd['metadata']['name'] for crd in crds
                         if crd['metadata'].get('labels', {}).get('app.kubernetes.io/name') == 'azure-service-operator')
        for kind in sorted(consumers):
            if self.objects(kind):
                raise RuntimeError(f'Operator/application consumers remain ({kind}); teardown stopped')
        print('Partial teardown: live workloads and application/operator consumers are drained.', flush=True)

    def cleanup(self):
        if self.settings is None:
            self.verify_drained()
            return
        # Detach hub onboarding before suspending the local root. Workload packages
        # are retained if management is unavailable; teardown then fails closed.
        if self.settings['cluster']['role'] == 'workload' and not self.hub:
            raise RuntimeError('Set HUB_KUBECONFIG to detach workload onboarding before teardown')
        if self.hub:
            name = self.settings['cluster']['name']
            run(*self.hub, '-n', 'argocd', 'delete', 'secret', 'cluster-' + name, '--ignore-not-found')
            wait_for(lambda: self.object('applications.argoproj.io', 'onboard-' + name, 'argocd', self.hub) is None,
                     'Waiting for hub onboarding to detach')

        def suspend(applications):
            for app in applications:
                run(*self.kube, '-n', 'argocd', 'patch', 'applications.argoproj.io', app['metadata']['name'],
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
                    run(*self.kube, '-n', 'argocd', 'patch', 'applications.argoproj.io', app['metadata']['name'],
                        '--type=merge', '-p', json.dumps(change))
                return idle

            wait_for(stopped, 'Waiting for Argo operations to stop')

        apps = self.objects('applications.argoproj.io', 'argocd')
        roots = [a for a in apps if a['metadata']['name'] == 'platform']
        if roots:
            # Stop creation before discovering the final registration inventory.
            suspend(roots)
            apps = self.objects('applications.argoproj.io', 'argocd')
            root = next(a for a in apps if a['metadata']['name'] == 'platform')
            # Bootstrap Sensors are root manifests, not children of the event
            # package. Stop them while their controller and EventBus still exist.
            sensor_refs = [(r.get('namespace'), r['name']) for r in root.get('status', {}).get('resources', [])
                           if r.get('group') == 'argoproj.io' and r.get('kind') == 'Sensor']
            if sensor_refs:
                apis = run(*self.kube, 'api-resources', '-o', 'name', capture=True).splitlines()
                if 'sensors.argoproj.io' in apis:
                    sensors = {}
                    for namespace, name in sensor_refs:
                        if not namespace:
                            raise RuntimeError(f'Cannot safely drain root Sensor {name}: namespace is missing')
                        sensor = self.object('sensors.argoproj.io', name, namespace)
                        if sensor is None:
                            continue
                        tracking = sensor['metadata'].get('annotations', {}).get('argocd.argoproj.io/tracking-id')
                        if tracking != f'platform:argoproj.io/Sensor:{namespace}/{name}':
                            raise RuntimeError(f'Cannot safely drain root Sensor {namespace}/{name}: ownership is unverified')
                        sensors.setdefault(namespace, []).append(name)
                    for namespace, names in sensors.items():
                        self.delete('sensors.argoproj.io', names, namespace)

            registrations = {r['name'] for r in root.get('status', {}).get('resources', [])
                             if r.get('group') == 'argoproj.io' and r.get('kind') == 'ApplicationSet'
                             and r.get('namespace') == 'argocd'}
            if registrations:
                onboarding = []
                appsets = self.objects('applicationsets.argoproj.io', 'argocd')
                for appset in appsets:
                    metadata = appset['metadata']
                    name = metadata['name']
                    if name not in registrations or metadata.get('labels', {}).get('platform.ai-platform-portfolio/onboarding') != 'true':
                        continue
                    tracking = metadata.get('annotations', {}).get('argocd.argoproj.io/tracking-id')
                    spec = appset['spec']
                    finalizers = spec.get('template', {}).get('metadata', {}).get('finalizers', [])
                    if (tracking != f'platform:argoproj.io/ApplicationSet:argocd/{name}'
                            or spec.get('syncPolicy', {}).get('preserveResourcesOnDeletion') is not False
                            or 'resources-finalizer.argocd.argoproj.io' not in finalizers):
                        raise RuntimeError(f'Cannot safely drain onboarding ApplicationSet {name}: verify ownership and cascading cleanup')
                    onboarding.append(name)
                if onboarding:
                    # Nested ApplicationSets can disappear before their generated
                    # Applications finish PostDelete. Retain native ownership/UIDs
                    # so we can follow that cleanup after parent objects vanish.
                    owned = {('ApplicationSet', a['metadata']['name']): a['metadata']['uid']
                             for a in appsets if a['metadata']['name'] in onboarding}

                    def applications_gone():
                        inventory = [('ApplicationSet', a) for a in self.objects('applicationsets.argoproj.io', 'argocd')]
                        inventory += [('Application', a) for a in self.objects('applications.argoproj.io', 'argocd')]
                        previous = -1
                        while previous != len(owned):
                            previous = len(owned)
                            for kind, obj in inventory:
                                metadata = obj['metadata']
                                name = metadata['name']
                                references = metadata.get('ownerReferences', [])
                                parent_owned = any(
                                    ref.get('apiVersion') == 'argoproj.io/v1alpha1'
                                    and ref.get('uid') and owned.get((ref.get('kind'), ref.get('name'))) == ref['uid']
                                    for ref in references)
                                tracking = metadata.get('annotations', {}).get('argocd.argoproj.io/tracking-id', '')
                                parent = tracking.partition(':')[0]
                                tracked = (('Application', parent) in owned
                                           and tracking == f'{parent}:argoproj.io/{kind}:argocd/{name}')
                                if parent_owned or tracked:
                                    owned[(kind, name)] = metadata['uid']
                        return not any((kind, obj['metadata']['name']) in owned
                                       and owned[(kind, obj['metadata']['name'])] == obj['metadata']['uid']
                                       for kind, obj in inventory)

                    applications_gone()  # Capture descendants before deleting their parents.
                    self.delete('applicationsets.argoproj.io', onboarding, 'argocd', foreground=True)
                    wait_for(applications_gone, 'Waiting for registered applications and PostDelete cleanup')
                apps = self.objects('applications.argoproj.io', 'argocd')

        if any(a['metadata']['name'] in {'postgres', 'atlas-operator'} for a in apps):
            apis = set(run(*self.kube, 'api-resources', '-o', 'name', capture=True).splitlines())
            # Atlas CR deletion does not remove SQL roles or databases. Finish
            # preview cleanup while its controller, credentials and server exist.
            for kind in ['atlasschemas.db.atlasgo.io', 'atlasmigrations.db.atlasgo.io',
                         'databaseroles.postgresql.cnpg.io']:
                if kind in apis:
                    def consumers_gone():
                        remaining = self.objects(kind)
                        if any(not obj['metadata'].get('deletionTimestamp') for obj in remaining):
                            raise RuntimeError('Complete preview database/Atlas cleanup before teardown')
                        return not remaining

                    # PostDelete hook deletion can outlive its Application in
                    # API observations. Wait only for already-deleting objects;
                    # active consumers still fail the guard immediately.
                    wait_for(consumers_gone, f'Waiting for deleting {kind}')
            if 'databases.postgresql.cnpg.io' in apis:
                owned = {(r.get('namespace'), r.get('name')) for app in apps
                         if app['metadata']['name'] == 'postgres'
                         and app['metadata'].get('labels', {}).get('platform.ai-platform-portfolio/managed') == 'true'
                         for r in app.get('status', {}).get('resources', [])
                         if r.get('group') == 'postgresql.cnpg.io' and r.get('kind') == 'Database'}
                if any((obj['metadata']['namespace'], obj['metadata']['name']) not in owned
                       for obj in self.objects('databases.postgresql.cnpg.io')):
                    raise RuntimeError('Remove preview PostgreSQL databases and wait for finalizers before teardown')
        if any(a['metadata']['name'] == 'argo-rollouts' for a in apps):
            if self.objects('rollouts.argoproj.io'):
                raise RuntimeError('Remove application Rollouts before tearing down their controller and traffic-routing dependencies')
        aso_resources = []
        if any(a['metadata']['name'] == 'azure-service-operator' for a in apps):
            # Consumer cleanup is complete. Verify child-owned Azure resources
            # and root-owned shared roles before removing either ownership layer.
            root = next((a for a in apps if a['metadata']['name'] == 'platform'), {})
            packages = {r['name'] for r in root.get('status', {}).get('resources', [])
                        if r.get('group') == 'argoproj.io' and r.get('kind') == 'Application'
                        and r.get('namespace') == 'argocd'}
            root_roles = {(r['group'], r['kind'], r['namespace'], r['name']):
                          f"platform:{r['group']}/{r['kind']}:{r['namespace']}/{r['name']}"
                          for r in root.get('status', {}).get('resources', [])
                          if r.get('group') == 'authorization.azure.com'
                          and r.get('kind') == 'RoleDefinition' and r.get('namespace')}
            owned = dict(root_roles)
            for app in apps:
                metadata = app['metadata']
                name = metadata['name']
                tracking = metadata.get('annotations', {}).get('argocd.argoproj.io/tracking-id')
                if (name not in packages
                        or metadata.get('labels', {}).get('platform.ai-platform-portfolio/managed') != 'true'
                        or tracking != f'platform:argoproj.io/Application:argocd/{name}'):
                    continue
                for resource in app.get('status', {}).get('resources', []):
                    key = (resource.get('group'), resource.get('kind'), resource.get('namespace'), resource.get('name'))
                    owned[key] = f'{name}:{key[0]}/{key[1]}:{key[2]}/{key[3]}'

            # Use ASO's own reserved CRD ownership label. Native AKS ACN APIs
            # also end in .azure.com and must not be treated as ASO resources.
            aso_resources = run(*self.kube, 'get', 'customresourcedefinitions.apiextensions.k8s.io',
                            '-l', 'app.kubernetes.io/name=azure-service-operator',
                            '-o', 'jsonpath={range .items[*]}{.metadata.name}{"\\n"}{end}',
                            capture=True).splitlines()
            shared_roles = []
            for resource in aso_resources:
                for obj in self.objects(resource):
                    key = (obj.get('apiVersion', '').split('/')[0], obj.get('kind'),
                           obj['metadata'].get('namespace'), obj['metadata']['name'])
                    tracking = obj['metadata'].get('annotations', {}).get('argocd.argoproj.io/tracking-id')
                    if key not in owned or tracking != owned[key]:
                        raise RuntimeError('Remove ASO Azure resources and wait for finalizers before teardown: '
                                           f'unverified ownership for {key[1]} {key[2]}/{key[3]}')
                    if key in root_roles:
                        shared_roles.append((resource, key[2], key[3]))

            # Registrations and PostDelete hooks drained above. Keep ASO and its
            # credentials available until these shared role finalizers finish.
            for resource, namespace, name in shared_roles:
                self.delete(resource, [name], namespace)
        managed = [a for a in self.objects('applications.argoproj.io', 'argocd')
                   if a['metadata'].get('labels', {}).get('platform.ai-platform-portfolio/managed') == 'true']
        suspend(managed)
        # Remove clients before databases and keep operators until their CRs disappear.
        aso_drained = False
        for app in sorted(managed, key=lambda a: int(a['metadata'].get('annotations', {}).get('argocd.argoproj.io/sync-wave', 0)), reverse=True):
            name = app['metadata']['name']
            if name == 'external-dns':
                runtime = self.settings['runtime']
                dns = {'owner': self.settings['cluster']['name'], 'zone': {
                    'name': runtime['AZURE_DNS_ZONE'], 'resource_group_name': runtime['AZURE_DNS_RESOURCE_GROUP']}}
                wait_for(lambda: not owned_dns(self.account, dns),
                         'Waiting for ExternalDNS owned records to drain', timeout=180)
            if name in {'configuration', 'azure-service-operator'} and aso_resources and not aso_drained:
                wait_for(lambda: all(not self.objects(resource) for resource in aso_resources),
                         'Waiting for ASO Azure resources to finish deletion')
                aso_drained = True
            self.delete('applications.argoproj.io', [name], 'argocd')
        self.delete('applications.argoproj.io', ['platform'], 'argocd')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['up', 'down'])
    parser.add_argument('--infra-dir', type=Path, required=True)
    parser.add_argument('--tfvars', type=Path, help='Effective bootstrap inputs for an already-down retry')
    args = parser.parse_args()
    infra = args.infra_dir.resolve()
    tfvars = args.tfvars or Path('terraform.tfvars')
    tfvars = (tfvars if tfvars.is_absolute() else infra / tfvars).resolve()
    if args.action == 'down' and already_down(infra, tfvars):
        return
    with tempfile.TemporaryDirectory(prefix='platform-aks-') as directory:
        cluster = Cluster(args.infra_dir.resolve(), Path(directory) / 'kubeconfig', teardown=args.action == 'down')
        if args.action == 'down':
            if cluster.settings is not None:
                try:
                    certificates.save(cluster)
                except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired):
                    # A requested shutdown must still remove disposable compute.
                    print('WARNING: public TLS preservation failed; continuing requested teardown. '
                          'A future up may require new certificates and encounter CA limits.', flush=True)
            cluster.cleanup()
            cluster.cancel_node_upgrades()
            return
        runtime = {**cluster.settings['runtime'],
                   'AZURE_TENANT_ID': cluster.account['tenant_id'],
                   'AZURE_SUBSCRIPTION_ID': cluster.account['subscription_id']}
        runtime['FOUNDRY_ACCOUNT_NAME'] = foundry_account_name(runtime['AZURE_KEY_VAULT_URI'])
        cluster.check_dns(runtime)
        existing = cluster.object('applications.argoproj.io', 'platform', 'argocd')
        runtime.update(cluster.sso())
        runtime.update({'STORAGE_' + key.upper(): value for key, value in cluster.settings['storage_containers'].items()})
        # Generic context for GitOps-owned service federation.
        runtime['AKS_OIDC_ISSUER_URL'] = cluster.oidc_issuer()
        certificates.restore(cluster)
        prepare_credentials(cluster)
        sonar_ready = bool(existing and existing['spec']['source']['helm']['valuesObject'].get('sonarqubeReady'))
        cluster.seed(cluster.root(runtime, sonar_ready))
        prepare_sonarqube(cluster, run, wait_for)
        cluster.seed(cluster.root(runtime, True))
        cluster.ready()
        cluster.ingress_ready(runtime)
        print('Platform reconciled; portfolio data is disposable on teardown.', flush=True)


if __name__ == '__main__':
    main()
