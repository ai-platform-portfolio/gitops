"""Keep application credentials stable within this disposable cluster."""
import base64
import json
import secrets


def secret(cluster, namespace, name, data, secret_type='Opaque', labels=None):
    cluster.manifest({'apiVersion': 'v1', 'kind': 'Secret', 'type': secret_type,
                      'metadata': {'name': name, 'namespace': namespace, 'labels': labels or {}},
                      'data': {key: base64.b64encode(value.encode()).decode() for key, value in data.items()}})


def ensure_secret(cluster, namespace, name, data, secret_type='Opaque', labels=None):
    existing = cluster.object('secret', name, namespace)
    if existing:
        saved = {key: base64.b64decode(value).decode() for key, value in existing.get('data', {}).items()}
        if existing.get('type', 'Opaque') != secret_type or not all(saved.get(key) for key in data):
            raise ValueError(f'Incomplete application Secret: {namespace}/{name}')
        return saved
    if cluster.objects('persistentvolumeclaims', namespace):
        raise RuntimeError(f'Credentials missing beside existing data: {namespace}/{name}; redeploy cleanly')
    secret(cluster, namespace, name, data, secret_type, labels)
    return data


def prepare(cluster):
    def password():
        return secrets.token_urlsafe(32)
    s3_password = password()
    definitions = {
        'lgtm/platform-grafana-admin': {'user': 'admin', 'password': password()},
        'langfuse/platform-langfuse-app': {'salt': password(), 'encryption-key': secrets.token_hex(32),
                                           'nextauth-secret': password()},
        'langfuse/platform-langfuse-redis': {'default': password()},
        'langfuse/platform-langfuse-clickhouse': {'password': password()},
        'langfuse/platform-langfuse-s3': {'accessKey': 'langfuse', 'secretKey': s3_password,
            'seaweedfs_s3_config': json.dumps({'identities': [{'name': 'langfuse',
                'credentials': [{'accessKey': 'langfuse', 'secretKey': s3_password}],
                'actions': ['Admin', 'Read', 'Write', 'List', 'Tagging']}]})},
    }
    for namespace in ['lgtm', 'langfuse', 'postgres']:
        labels = {'gitops.platform/gateway': 'public'} if namespace != 'postgres' else {}
        cluster.manifest({'apiVersion': 'v1', 'kind': 'Namespace',
                          'metadata': {'name': namespace, 'labels': labels}})
    for key, data in definitions.items():
        namespace, name = key.split('/')
        ensure_secret(cluster, namespace, name, data)
