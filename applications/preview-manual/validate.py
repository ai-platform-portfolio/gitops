#!/usr/bin/env python3
"""Validate the standalone registration using its actual pinned chart/app sources."""
import json
from pathlib import Path
import re
import subprocess
import tempfile

import yaml

PACKAGE = Path(__file__).resolve().parent
ROOT = PACKAGE.parents[1]


def command(*args):
    return subprocess.run(args, text=True, capture_output=True, check=True).stdout


def validate(tmp):
    app = yaml.safe_load((PACKAGE / 'application.yaml').read_text())
    assert 'automated' not in app['spec']['syncPolicy'] and not app['metadata'].get('finalizers')
    chart, application, credentials = app['spec']['sources']
    assert application['ref'] == 'application' and 'path' not in application
    checkouts = []
    for name, source in [('chart', chart), ('application', application)]:
        assert re.fullmatch('[0-9a-f]{40}', source['targetRevision']), 'Pin reviewed chart and application values commits'
        checkout = tmp / name
        command('git', 'init', '--quiet', str(checkout))
        command('git', '-C', str(checkout), 'fetch', '--depth=1', source['repoURL'], source['targetRevision'])
        command('git', '-C', str(checkout), 'checkout', '--quiet', '--detach', 'FETCH_HEAD')
        assert command('git', '-C', str(checkout), 'rev-parse', 'HEAD').strip() == source['targetRevision']
        checkouts.append(checkout)
    value_file, = chart['helm']['valueFiles']
    assert value_file.startswith('$application/')
    overrides = tmp / 'overrides.json'
    overrides.write_text(json.dumps(chart['helm']['valuesObject']))
    resources = [d for d in yaml.safe_load_all(command('helm', 'template', chart['helm']['releaseName'],
                 str(checkouts[0] / chart['path']), '--namespace', app['spec']['destination']['namespace'],
                 '-f', str(checkouts[1] / value_file.removeprefix('$application/')), '-f', str(overrides))) if d]
    assert credentials['directory'] == {'include': 'credentials.yaml'}
    resources += [d for d in yaml.safe_load_all((ROOT / credentials['path'] / 'credentials.yaml').read_text()) if d]
    keys = [(d['kind'], d['metadata'].get('namespace'), d['metadata']['name']) for d in resources]
    assert len(keys) == len(set(keys)), 'Only one source may own each resource'
    def one(kind, name, namespace=None):
        return next(d for d in resources if d['kind'] == kind and d['metadata']['name'] == name
                    and (namespace is None or d['metadata'].get('namespace') == namespace))
    def wave(d):
        return int(d['metadata']['annotations']['argocd.argoproj.io/sync-wave'])
    namespace = one('Namespace', 'preview-manual')
    assert wave(namespace) < wave(one('ExternalSecret', 'preview-manual-runtime', 'postgres'))
    for name in ['preview-manual-runtime', 'preview-manual-migrator']:
        source_secret = one('ExternalSecret', name, 'postgres')
        assert source_secret['spec']['refreshPolicy'] == 'CreatedOnce' and source_secret['spec']['target']['immutable']
        assert wave(source_secret) < wave(one('ExternalSecret', name, 'atlas-system'))
    reader = one('Role', 'preview-manual-runtime-reader')
    assert reader['rules'] == [{'apiGroups': [''], 'resources': ['secrets'],
            'resourceNames': ['preview-manual-runtime', 'platform-ca'], 'verbs': ['get']}]
    runtime_secret = one('ExternalSecret', 'preview-database', 'preview-manual')
    assert {d['remoteRef']['key'] for d in runtime_secret['spec']['data']} == {'preview-manual-runtime', 'platform-ca'}
    url = runtime_secret['spec']['target']['template']['data']['url']
    assert 'sslmode=verify-full&sslrootcert=/etc/postgres/ca.crt' in url and '{{ .password }}' in url
    assert not any(d['kind'] == 'Secret' for d in resources), 'No generated credentials in rendered GitOps'
    roles = next(d for d in resources if d['kind'] == 'AtlasSchema')
    database = next(d for d in resources if d['kind'] == 'Database')
    access = next(d for d in resources if d['kind'] == 'AtlasMigration' and 'local' in d['spec']['dir'])
    migration = next(d for d in resources if d['kind'] == 'AtlasMigration' and 'remote' in d['spec']['dir'])
    api = one('Deployment', 'api')
    assert wave(runtime_secret) < wave(roles) < wave(database) < wave(access) < wave(migration) < wave(api)
    assert database['spec']['databaseReclaimPolicy'] == 'delete'
    assert access['spec']['revisionsSchema'] != migration['spec']['revisionsSchema']
    assert '{{ .runtime }}' in access['spec']['dir']['local']['1_access.sql'], 'Atlas templates must survive Helm rendering'
    for d in [access, migration]:
        assert d['spec']['credentials']['parameters']['search_path'] == 'public'
        assert d['spec']['credentials']['parameters']['sslmode'] == 'verify-full'
    container = api['spec']['template']['spec']['containers'][0]
    assert container['env'] == [{'name': 'DATABASE_URL', 'valueFrom': {'secretKeyRef': {'name': 'preview-database', 'key': 'url'}}}]
    assert container['readinessProbe']['httpGet']['path'] == '/api/readyz'
    assert container['livenessProbe']['httpGet']['path'] == '/api/healthz'
    assert 'env' not in one('Deployment', 'web')['spec']['template']['spec']['containers'][0]
    for name in ['api', 'web']:
        volume = next(v for v in one('Deployment', name)['spec']['template']['spec']['volumes'] if v['name'] == 'postgres-ca')
        assert volume['secret']['items'] == [{'key': 'ca.crt', 'path': 'ca.crt'}]
    assert one('ListenerSet', 'web')['metadata']['annotations']['cert-manager.io/cluster-issuer']
    assert not any(d['kind'] == 'Certificate' for d in resources)
    print('Preview: external app values and merged chart pins rendered; ordered dependencies and runtime-only TLS credentials checked.')

if __name__ == '__main__':
    with tempfile.TemporaryDirectory(prefix='preview-render-') as directory:
        validate(Path(directory))
