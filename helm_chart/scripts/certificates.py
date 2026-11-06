"""Preserve public ACME TLS keys in the existing Key Vault for disposable rebuilds."""
import base64
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
from urllib.parse import urlparse

ISSUER = {'name': 'letsencrypt-azuredns', 'kind': 'ClusterIssuer'}


def command(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=60)
    if result.returncode:
        # CLI/openssl diagnostics must never echo secret payloads.
        raise RuntimeError('Public TLS preservation command failed; check Key Vault access and OpenSSL')
    return result.stdout


def scope(cluster):
    runtime = cluster.settings['runtime']
    identity = cluster.settings['cluster']['name'] + '/' + cluster.connection['resource_group']
    prefix = 'public-tls-' + hashlib.sha256((identity + '/' + runtime['DNS_ZONE']).encode()).hexdigest()[:16] + '-'
    uri = urlparse(runtime['AZURE_KEY_VAULT_URI'])
    if uri.scheme != 'https' or not uri.hostname:
        raise ValueError('Public TLS preservation requires the existing HTTPS Key Vault URI')
    vault = uri.hostname.split('.')[0]
    return identity, prefix, vault, runtime['DNS_ZONE']


def key_name(prefix, metadata):
    identity = metadata['namespace'] + '/' + metadata['name']
    return prefix + hashlib.sha256(identity.encode()).hexdigest()[:32]


def validate(entry, identity, zone):
    """Validate the public leaf and private key together, before any restore writes."""
    metadata = entry['metadata']
    if (entry.get('version') != 1 or entry.get('cluster') != identity or entry.get('zone') != zone
            or set(metadata) != {'namespace', 'name', 'annotations'}
            or not re.fullmatch(r'[a-z0-9]([-a-z0-9]*[a-z0-9])?', metadata['namespace'])
            or len(metadata['namespace']) > 63 or len(metadata['name']) > 253
            or not re.fullmatch(r'[a-z0-9]([a-z0-9.-]*[a-z0-9])?', metadata['name'])
            or any(not k.startswith('cert-manager.io/') or not isinstance(v, str)
                   for k, v in metadata['annotations'].items())
            or set(entry['data']) != {'tls.crt', 'tls.key'}):
        raise ValueError('Invalid public TLS snapshot identity')
    names = sorted(entry['dnsNames'])
    if not names or any(not n.endswith('.' + zone) for n in names):
        raise ValueError('Public TLS snapshot is outside the configured DNS zone')
    with tempfile.TemporaryDirectory(prefix='public-tls-') as directory:
        files = {}
        for key, value in entry['data'].items():
            path = Path(directory) / key
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'wb') as stream:
                stream.write(base64.b64decode(value, validate=True))
            files[key] = str(path)
        details = command('openssl', 'x509', '-in', files['tls.crt'], '-noout', '-dates', '-ext', 'subjectAltName')
        actual = sorted(re.findall(r'DNS:([^,\s]+)', details))
        if actual != names:
            raise ValueError('Public TLS certificate DNS identities do not match the snapshot')
        public = command('openssl', 'x509', '-in', files['tls.crt'], '-pubkey', '-noout')
        if public != command('openssl', 'pkey', '-in', files['tls.key'], '-pubout'):
            raise ValueError('Public TLS certificate and private key do not match')
        dates = dict(re.findall(r'(notBefore|notAfter)=(.*)', details))
        start, end = [datetime.strptime(dates[k], '%b %d %H:%M:%S %Y %Z').replace(tzinfo=timezone.utc)
                      for k in ['notBefore', 'notAfter']]
        if start > datetime.now(timezone.utc):
            raise ValueError('Public TLS certificate is not yet valid')
        return end


def save(cluster):
    identity, prefix, vault, zone = scope(cluster)
    apis = command(*cluster.kube, 'api-resources', '-o', 'name').splitlines()
    if 'certificates.cert-manager.io' not in apis:
        return
    count = 0
    for certificate in cluster.objects('certificates.cert-manager.io'):
        spec = certificate['spec']
        issuer = spec.get('issuerRef', {})
        if (any(issuer.get(k) != v for k, v in ISSUER.items())
                or issuer.get('group', 'cert-manager.io') != 'cert-manager.io'
                or spec.get('isCA')
                or set(spec.get('usages', [])) - {'digital signature', 'key encipherment', 'server auth'}):
            continue
        if not any(c['type'] == 'Ready' and c['status'] == 'True'
                   and c.get('observedGeneration') == certificate['metadata']['generation']
                   for c in certificate.get('status', {}).get('conditions', [])):
            print('Skipping a public certificate that is not currently Ready.', flush=True)
            continue
        namespace = certificate['metadata']['namespace']
        secret = cluster.object('secret', spec['secretName'], namespace)
        if not secret or secret.get('type') != 'kubernetes.io/tls':
            raise ValueError('Ready public certificate has no TLS Secret')
        entry = {'version': 1, 'cluster': identity, 'zone': zone, 'dnsNames': spec['dnsNames'],
                 'metadata': {'namespace': namespace, 'name': spec['secretName'],
                              'annotations': {k: v for k, v in secret['metadata'].get('annotations', {}).items()
                                              if k.startswith('cert-manager.io/')}},
                 'data': {k: secret['data'][k] for k in ['tls.crt', 'tls.key']}}
        expiry = validate(entry, identity, zone)
        if expiry <= datetime.now(timezone.utc):
            continue
        with tempfile.TemporaryDirectory(prefix='public-tls-save-') as directory:
            path = Path(directory) / 'secret.json'
            with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), 'w') as stream:
                json.dump(entry, stream)
            command('az', 'keyvault', 'secret', 'set', '--vault-name', vault, '--name', key_name(prefix, entry['metadata']),
                    '--file', str(path), '--encoding', 'utf-8', '--expires', expiry.strftime('%Y-%m-%dT%H:%M:%SZ'),
                    '--output', 'none', '--only-show-errors')
        count += 1
    print(f'Preserved {count} public TLS certificates in Key Vault; application credentials excluded.', flush=True)


def restore(cluster):
    identity, prefix, vault, zone = scope(cluster)
    secrets = json.loads(command('az', 'keyvault', 'secret', 'list', '--vault-name', vault, '-o', 'json', '--only-show-errors'))
    entries = []
    for secret in secrets:
        if not secret['name'].startswith(prefix):
            continue
        entry = json.loads(command('az', 'keyvault', 'secret', 'show', '--vault-name', vault,
                                   '--name', secret['name'], '--query', 'value', '-o', 'tsv', '--only-show-errors'))
        if key_name(prefix, entry['metadata']) != secret['name']:
            raise ValueError('Public TLS snapshot name does not match its identity')
        expiry = validate(entry, identity, zone)
        if expiry > datetime.now(timezone.utc):
            entries.append(entry)
    count = 0
    for entry in entries:
        metadata = entry['metadata']
        if cluster.object('secret', metadata['name'], metadata['namespace']):
            continue  # A running cluster owns current credentials, including renewals.
        if not cluster.object('namespace', metadata['namespace'], metadata['namespace']):
            cluster.manifest({'apiVersion': 'v1', 'kind': 'Namespace', 'metadata': {'name': metadata['namespace']}})
        # Strip old owner UIDs/resource versions; cert-manager adopts restored material.
        result = subprocess.run([*cluster.kube, 'create', '-f', '-'],
                                input=json.dumps({'apiVersion': 'v1', 'kind': 'Secret', 'metadata': metadata,
                                                  'type': 'kubernetes.io/tls', 'data': entry['data']}),
                                text=True, capture_output=True, timeout=30)
        if result.returncode and 'AlreadyExists' not in result.stderr:
            raise RuntimeError('Restoring public TLS Secret failed; existing credentials were not overwritten')
        count += not result.returncode
    print(f'Restored {count} public TLS certificates before platform reconciliation.', flush=True)
