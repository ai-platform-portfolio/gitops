"""Reconcile GitOps-owned DevLake projects and a path-scoped ingestion key.

Uses the private core service; stable DevLake has no native user authenticator.
Never prints credentials or API response bodies (connections can contain tokens).
"""
import base64
import json
import os
from pathlib import Path
import re
import ssl
import urllib.error
import urllib.request


def request(url, method='GET', body=None, headers=None, context=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={'Content-Type': 'application/json', **(headers or {})})
    try:
        with urllib.request.urlopen(req, context=context, timeout=30) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        # Response bodies and URLs may contain integration metadata. Log only status.
        raise RuntimeError(f'{method} request failed: HTTP {error.code}') from None


def configure():
    base = os.environ.get('DEVLAKE_URL', 'http://devlake:8080')
    headers = {'Authorization': os.environ['AUTOMATION_CREDENTIAL']}
    def api(path, method='GET', body=None):
        result = request(base + path, method, body, headers)
        return result.get('data') if isinstance(result, dict) and 'code' in result else result
    projects = json.loads(Path('/config/projects.json').read_text())
    reconcile_projects(api, projects)

    kube = os.environ.get('KUBERNETES_URL', 'https://kubernetes.default.svc')
    service_account = Path('/var/run/secrets/kubernetes.io/serviceaccount')
    kube_headers = {'Authorization': 'Bearer ' + (service_account / 'token').read_text().strip()}
    context = ssl.create_default_context(cafile=str(service_account / 'ca.crt'))
    secret_url = kube + '/api/v1/namespaces/devlake/secrets/devlake-ingestion'
    secret = request(secret_url, headers=kube_headers, context=context)
    if reconcile_key(api, projects, secret):
        request(secret_url, 'PUT', secret, kube_headers, context)
    print(f'DevLake configured: {len(projects)} service projects; scoped ingestion credential ready.')


def reconcile_projects(api, projects):
    for name, settings in projects.items():
        # Enumerating projects propagates DB/auth failures instead of assuming absence.
        exists = any(p['name'] == name for p in api('/projects?pageSize=1000')['projects'])
        if not exists:
            api('/projects', 'POST', {'name': name, 'description': settings['description'],
                                     'metrics': [{'pluginName': 'dora', 'enable': True}],
                                     'blueprint': {'name': name + '-Blueprint', 'projectName': name,
                                                   'mode': 'NORMAL', 'enable': True,
                                                   'cronConfig': '*/5 * * * *', 'isManual': False}})
        else:
            api('/projects/' + name, 'PATCH', {'description': settings['description'],
                'metrics': [{'pluginName': 'dora', 'enable': True}]})
        connections = api('/plugins/webhook/connections')
        connection = next((c for c in connections if c['name'] == name), None)
        if connection is None:
            connection = api('/plugins/webhook/connections', 'POST', {'name': name})
        project = api('/projects/' + name)
        blueprint = project['blueprint']
        connections = blueprint.get('connections') or []
        if not any(c['pluginName'] == 'webhook' and c['connectionId'] == connection['id'] for c in connections):
            connections.append({'pluginName': 'webhook', 'connectionId': connection['id'], 'scopes': []})
        # Update only this project's webhook membership; leave other configured sources.
        api('/blueprints/' + str(blueprint['id']), 'PATCH', {'connections': connections,
            'enable': True, 'cronConfig': '*/5 * * * *', 'isManual': False})


def reconcile_key(api, projects, secret):
    keys = api('/api-keys?pageSize=1000')['apikeys']
    key = next((k for k in keys if k['name'] == 'gitops-service-events'), None)
    names = '|'.join(re.escape(n) for n in projects)
    allowed = '^/plugins/webhook/connections/by-name/(' + names + ')/(deployments|issues)(/.*)?$' if projects else '^$'
    # Reuse the current key across normal reruns. Membership changes deliberately
    # rotate the single integration credential; consumers receive the new Secret.
    if key is None or key.get('allowedPath') != allowed or not secret.get('data', {}).get('token'):
        if key:
            api('/api-keys/' + str(key['id']), 'DELETE')
        created = api('/api-keys', 'POST', {'name': 'gitops-service-events',
                                           'type': 'devlake', 'allowedPath': allowed})
        secret.setdefault('data', {})['token'] = base64.b64encode(created['apiKey'].encode()).decode()
        return True
    return False


if __name__ == '__main__':
    configure()
