"""Provision Graph-only Langfuse sign-in dependencies from GitOps settings.

ASO manages ARM resources, not Microsoft Graph application registrations. Secrets
are written directly to the existing Key Vault, never Terraform or process argv.
"""
import base64
from datetime import datetime, timedelta, timezone
import json
import re
from pathlib import Path
import subprocess
import tempfile
from urllib.parse import quote, urlparse

GRAPH = 'https://graph.microsoft.com/v1.0/'


class Azure:
    def call(self, args, missing=None):
        result = subprocess.run(['az', *args, '--only-show-errors', '-o', 'json'],
                                text=True, capture_output=True)
        if result.returncode:
            if missing and missing in result.stderr:
                return None
            # CLI errors can include tenant IDs, URLs or credential response bodies.
            raise RuntimeError('Entra/Key Vault operation failed; inspect Azure permissions and resource health')
        return json.loads(result.stdout) if result.stdout.strip() else None

    def graph(self, method, path, body=None):
        args = ['rest', '--method', method, '--url', GRAPH + path]
        with tempfile.TemporaryDirectory(prefix='platform-graph-') as directory:
            if body is not None:
                filename = Path(directory) / 'request.json'
                filename.write_text(json.dumps(body))
                filename.chmod(0o600)
                args += ['--body', '@' + str(filename), '--headers', 'Content-Type=application/json']
            return self.call(args)

    def collection(self, path):
        result = self.graph('GET', path)
        items = result.get('value', [])
        while result.get('@odata.nextLink'):
            url = result['@odata.nextLink']
            if not url.startswith(GRAPH):
                raise RuntimeError('Unexpected Microsoft Graph pagination origin')
            result = self.graph('GET', url[len(GRAPH):])
            items.extend(result.get('value', []))
        return items

    def owner(self):
        token = self.call(['account', 'get-access-token', '--resource-type', 'ms-graph'])['accessToken']
        # The token is supplied by the authenticated CLI, not an HTTP caller.
        claims = json.loads(base64.urlsafe_b64decode(token.split('.')[1] + '==='))
        return claims['oid']

    def secret(self, vault, name):
        value = self.call(['keyvault', 'secret', 'show', '--vault-name', vault, '--name', name],
                          missing='SecretNotFound')
        return json.loads(value['value']) if value else None

    def save_secret(self, vault, name, value):
        with tempfile.TemporaryDirectory(prefix='platform-sso-secret-') as directory:
            filename = Path(directory) / 'secret.json'
            filename.write_text(json.dumps(value))
            filename.chmod(0o600)
            # Query only metadata so a successful write never returns the value.
            self.call(['keyvault', 'secret', 'set', '--vault-name', vault, '--name', name,
                       '--file', str(filename), '--query', 'attributes.enabled'])


def lookup(azure, resource, field, value):
    expression = field + " eq '" + value.replace("'", "''") + "'"
    return azure.collection(resource + '?$filter=' + quote(expression, safe=''))


def configure(azure, settings, tenant, vault, now=None):
    now = now or datetime.now(timezone.utc)
    marker = 'platform-gitops:' + settings['applicationName']
    apps = lookup(azure, 'applications', 'displayName', settings['applicationName'])
    if len(apps) > 1 or (apps and marker not in apps[0].get('tags', [])):
        raise RuntimeError('SSO application name is already in use; explicitly resolve ownership before adoption')
    if not 30 <= settings['credentialValidityDays'] <= 365:
        raise ValueError('Langfuse client credentials must be valid for 30 to 365 days')

    # Direct user assignments work without premium group-assignment licensing.
    allowed = set()
    for email in settings['users']:
        found = {u['id']: u for field in ['mail', 'userPrincipalName']
                 for u in lookup(azure, 'users', field, email)}
        if len(found) != 1:
            raise RuntimeError('Each Langfuse SSO allowlist entry must identify exactly one Entra user')
        if not next(iter(found.values())).get('mail'):
            raise RuntimeError('Langfuse SSO users need a nonempty Entra mail attribute for the email claim')
        allowed.update(found)
    if not allowed:
        raise RuntimeError('Configure at least one Langfuse SSO user')

    owner = azure.owner()
    desired = {
        'displayName': settings['applicationName'], 'signInAudience': 'AzureADMyOrg',
        'web': {'redirectUris': [settings['redirectUri']]},
        'optionalClaims': {'idToken': [{'name': 'email', 'essential': True}]},
    }
    if apps:
        app = apps[0]
        azure.graph('PATCH', 'applications/' + app['id'], desired)
    else:
        app = azure.graph('POST', 'applications', {
            **desired, 'tags': [marker],
            'owners@odata.bind': [GRAPH + 'directoryObjects/' + owner],
        })
    principals = lookup(azure, 'servicePrincipals', 'appId', app['appId'])
    if len(principals) > 1:
        raise RuntimeError('Expected one Langfuse enterprise application')
    if principals:
        principal = principals[0]
        azure.graph('PATCH', 'servicePrincipals/' + principal['id'], {'appRoleAssignmentRequired': True})
    else:
        principal = azure.graph('POST', 'servicePrincipals', {
            'appId': app['appId'], 'appRoleAssignmentRequired': True,
            'owners@odata.bind': [GRAPH + 'directoryObjects/' + owner],
        })

    path = 'servicePrincipals/' + principal['id'] + '/appRoleAssignedTo'
    assignments = azure.collection(path)
    for user in allowed - {a['principalId'] for a in assignments}:
        azure.graph('POST', path, {'principalId': user, 'resourceId': principal['id'],
                                 'appRoleId': '00000000-0000-0000-0000-000000000000'})
    for assignment in assignments:
        if assignment['principalId'] not in allowed:
            azure.graph('DELETE', path + '/' + assignment['id'])

    saved = azure.secret(vault, settings['vaultSecretName'])
    if saved and saved.get('AUTH_AZURE_AD_CLIENT_ID') != app['appId']:
        raise RuntimeError('Existing SSO credential belongs to another application; refusing to overwrite it')
    credentials = azure.graph('GET', 'applications/' + app['id'] + '?$select=passwordCredentials')['passwordCredentials']
    if saved and any(c['keyId'] == saved.get('credentialKeyId') for c in credentials):
        expiry = datetime.fromisoformat(saved['expiresAt'].replace('Z', '+00:00'))
        if expiry > now + timedelta(days=14):
            return saved, False

    expiry = (now + timedelta(days=settings['credentialValidityDays'])).isoformat()
    credential = azure.graph('POST', 'applications/' + app['id'] + '/addPassword', {
        'passwordCredential': {'displayName': marker, 'endDateTime': expiry},
    })
    value = {
        'AUTH_AZURE_AD_CLIENT_ID': app['appId'],
        'AUTH_AZURE_AD_CLIENT_SECRET': credential['secretText'],
        'AUTH_AZURE_AD_TENANT_ID': tenant,
        'credentialKeyId': credential['keyId'], 'expiresAt': expiry,
    }
    try:
        azure.save_secret(vault, settings['vaultSecretName'], value)
    except Exception:
        # Do not leave an unrecoverable newly generated credential after KV failure.
        azure.graph('POST', 'applications/' + app['id'] + '/removePassword', {'keyId': credential['keyId']})
        raise
    # Keep previous credentials valid during ESO refresh and the web rollout.
    # They can be retired after confirming the replacement is in use.
    return value, True


def git_revision(settings):
    revision = settings['revision']
    if re.fullmatch('[0-9a-f]{40}', revision):
        return revision
    refs = ['refs/heads/' + revision, 'refs/tags/' + revision + '^{}', 'refs/tags/' + revision]
    result = subprocess.run(['git', 'ls-remote', settings['repository'], *refs], text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Cannot resolve the configured GitOps revision')
    found = {line.split()[1]: line.split()[0] for line in result.stdout.splitlines()}
    for ref in refs:
        if ref in found:
            return found[ref]
    raise RuntimeError('GitOps revision must be a branch, tag or full commit SHA')


def prepare(cluster, wait):
    revision = git_revision(cluster.settings['git'])

    def available():
        root = cluster.object('application', 'platform', 'argocd') or {}
        app = cluster.object('application', 'configuration', 'argocd') or {}
        status = app.get('status', {})
        # Wait for the current root to supply current configuration values. The
        # ExternalSecret may still be Progressing until this step fills Key Vault.
        return (root.get('status', {}).get('sync', {}).get('revision') == revision
                and any(r.get('name') == 'configuration' and r.get('status') == 'Synced'
                        for r in root.get('status', {}).get('resources', []))
                and status.get('sync', {}).get('comparedTo', {}).get('source') == app.get('spec', {}).get('source')
                and any(r.get('kind') == 'ConfigMap' and r.get('name') == 'langfuse-sso-settings'
                        and r.get('status') == 'Synced' for r in status.get('resources', [])))

    wait(available, 'Waiting for current GitOps Langfuse SSO settings')
    settings = json.loads(cluster.object('configmap', 'langfuse-sso-settings', 'langfuse')['data']['settings'])
    if not settings['enabled']:
        return
    value, rotated = configure(Azure(), settings, cluster.account['tenant_id'],
                              urlparse(cluster.settings['runtime']['AZURE_KEY_VAULT_URI']).hostname.split('.')[0])

    def delivered():
        secret = cluster.object('secret', 'langfuse-sso', 'langfuse') or {}
        encoded = secret.get('data', {}).get('client-secret', '')
        return base64.b64decode(encoded).decode() == value['AUTH_AZURE_AD_CLIENT_SECRET']
    wait(delivered, 'Waiting for ESO to deliver Langfuse SSO credentials')
    if rotated and cluster.object('deployment', 'langfuse-web', 'langfuse'):
        # Environment-backed credentials are read at process start. A rollout is
        # an operational restart; the deployment spec/SSO settings remain in Git.
        subprocess.run([*cluster.kube, '-n', 'langfuse', 'rollout', 'restart',
                        'deployment/langfuse-web'], check=True, capture_output=True)
