"""Capture explicitly versioned, healthy local releases with a small durable outbox.

Argo histories are not a complete event log. Only the current observed release is
collected; source provenance must be in its reconciled Helm values, not mutable
Application annotations. Pending payloads survive pod/DevLake restarts in etcd.
"""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import ssl

from configure import request


def matches(desired, observed):
    if isinstance(desired, dict):
        return isinstance(observed, dict) and all(matches(v, observed.get(k)) for k, v in desired.items())
    return desired == observed


def deployment(project, settings, app, cluster, observed_at):
    status = app.get('status', {})
    operation = status.get('operationState', {})
    source = operation.get('syncResult', {}).get('source', {})
    if (operation.get('phase') != 'Succeeded' or status.get('sync', {}).get('status') != 'Synced'
            or status.get('health', {}).get('status') != 'Healthy'
            or not matches(app['spec'].get('source'), source)):
        return None
    provenance = source.get('helm', {}).get('valuesObject', {}).get('delivery', {})
    if not provenance:
        raise ValueError(f'{project}: configured Application requires source.helm.valuesObject.delivery provenance')
    if not re.fullmatch(r'[a-f0-9]{40}', provenance.get('commit', '')):
        raise ValueError(f'{project}: delivery.commit must identify the actual application source commit')
    if not re.fullmatch(r'https://github.com/[^/]+/[^/]+(?:\.git)?', provenance.get('repository', '')):
        raise ValueError(f'{project}: delivery.repository must be a GitHub source repository URL')
    if not provenance.get('id') or len(provenance['id']) > 200:
        raise ValueError(f'{project}: delivery.id must be a stable release identifier (1–200 characters)')
    purpose = provenance.get('purpose', 'planned')
    if purpose not in {'planned', 'remediation'}:
        raise ValueError(f'{project}: delivery.purpose must be planned or remediation')
    if purpose == 'remediation' and not provenance.get('incidentId'):
        raise ValueError(f'{project}: remediation releases require delivery.incidentId')
    title = ('remediation:' + provenance['incidentId'] + ':' if purpose == 'remediation' else '') + provenance['id']
    identity = '/'.join([cluster, project, app['metadata']['uid'], provenance['id']])
    identifier = hashlib.sha256(identity.encode()).hexdigest()
    return {'id': identifier, 'displayTitle': title, 'name': project,
            'startedDate': operation['startedAt'], 'finishedDate': observed_at,
            'environment': settings['environment'], 'result': 'SUCCESS',
            'deploymentCommits': [{'repoUrl': provenance['repository'],
                                   'commitSha': provenance['commit'],
                                   'startedDate': operation['startedAt'], 'finishedDate': observed_at,
                                   'displayTitle': title}]}


def collect():
    account = Path('/var/run/secrets/kubernetes.io/serviceaccount')
    headers = {'Authorization': 'Bearer ' + (account / 'token').read_text().strip()}
    context = ssl.create_default_context(cafile=str(account / 'ca.crt'))
    kube = os.environ.get('KUBERNETES_URL', 'https://kubernetes.default.svc')
    checkpoint_url = kube + '/api/v1/namespaces/devlake/configmaps/devlake-delivery-checkpoints'
    def get(path):
        return request(kube + path, headers=headers, context=context)
    config = get('/api/v1/namespaces/devlake/configmaps/devlake-configure')
    projects = json.loads(config['data']['projects.json'])
    state = request(checkpoint_url, headers=headers, context=context)
    ledger = json.loads(state.get('data', {}).get('ledger', '{"pending":{},"sent":{}}'))
    def persist():
        encoded = json.dumps(ledger, separators=(',', ':'))
        if len(encoded.encode()) > 800000:
            raise RuntimeError('Delivery outbox exceeds 800 KiB; investigate backlog before collecting more')
        state.setdefault('data', {})['ledger'] = encoded
        updated = request(checkpoint_url, 'PUT', state, headers, context)
        state['metadata']['resourceVersion'] = updated['metadata']['resourceVersion']
    for project, settings in projects.items():
        if not settings.get('application'):
            continue
        app = get('/apis/argoproj.io/v1alpha1/namespaces/argocd/applications/' + settings['application'])
        payload = deployment(project, settings, app, os.environ['CLUSTER_NAME'],
                             datetime.now(timezone.utc).isoformat())
        if payload and payload['id'] != ledger['sent'].get(project) and payload['id'] not in ledger['pending']:
            ledger['pending'][payload['id']] = {'project': project, 'payload': payload}
            persist()  # Accept into durable storage before attempting delivery.
    token = Path('/credentials/token').read_text().strip()
    failures = 0
    for identifier, entry in list(ledger['pending'].items()):
        try:
            request('http://devlake:8080/rest/plugins/webhook/connections/by-name/'
                    + entry['project'] + '/deployments', 'POST', entry['payload'],
                    {'Authorization': 'Bearer ' + token})
        except (OSError, RuntimeError):
            failures += 1
            continue
        del ledger['pending'][identifier]
        ledger['sent'][entry['project']] = identifier
        persist()
    print(f'Delivery outbox: {len(ledger["pending"])} pending, {failures} delivery failures.')
    if failures:
        raise RuntimeError('Delivery failed; durable pending records will be retried on the next run')


if __name__ == '__main__':
    collect()
