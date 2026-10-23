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
    postgres_password = password()
    s3_password = password()
    definitions = {
        'lgtm/platform-grafana-admin': {'user': 'admin', 'password': password()},
        'langfuse/platform-langfuse-app': {'salt': password(), 'encryption-key': secrets.token_hex(32),
                                           'nextauth-secret': password()},
        'langfuse/platform-langfuse-postgresql': {
            'POSTGRES_USER': 'postgres', 'POSTGRES_PASSWORD': postgres_password,
            'POSTGRES_DB': 'langfuse', 'USERDB_USER': 'langfuse', 'USERDB_PASSWORD': postgres_password},
        'langfuse/platform-langfuse-redis': {'default': password()},
        'langfuse/platform-langfuse-clickhouse': {'password': password()},
        'langfuse/platform-langfuse-s3': {'accessKey': 'langfuse', 'secretKey': s3_password,
            'seaweedfs_s3_config': json.dumps({'identities': [{'name': 'langfuse',
                'credentials': [{'accessKey': 'langfuse', 'secretKey': s3_password}],
                'actions': ['Admin', 'Read', 'Write', 'List', 'Tagging']}]})},
    }
    for namespace in ['lgtm', 'langfuse', 'postgres', 'sonarqube']:
        labels = {'gitops.platform/gateway': 'public'} if namespace != 'postgres' else {}
        cluster.manifest({'apiVersion': 'v1', 'kind': 'Namespace',
                          'metadata': {'name': namespace, 'labels': labels}})
    for key, data in definitions.items():
        namespace, name = key.split('/')
        ensure_secret(cluster, namespace, name, data)
    ensure_secret(cluster, 'postgres', 'sonarqube-role',
                  {'username': 'sonarqube', 'password': password()},
                  'kubernetes.io/basic-auth', {'cnpg.io/reload': 'true'})
    ensure_secret(cluster, 'sonarqube', 'sonarqube-monitoring', {'passcode': password()})
    ensure_secret(cluster, 'sonarqube', 'sonarqube-admin',
                  {'password': 'Aa1_' + secrets.token_urlsafe(29), 'currentPassword': 'admin'})


def prepare_sonarqube(cluster, run, wait):
    def ready():
        api = run(*cluster.kube, 'api-resources', '-o', 'name', capture=True).splitlines()
        if 'clusters.postgresql.cnpg.io' not in api:
            return False
        database = cluster.object('clusters.postgresql.cnpg.io', 'platform', 'postgres')
        return bool(database and any(c['type'] == 'Ready' and c['status'] == 'True'
                    for c in database.get('status', {}).get('conditions', [])))

    wait(ready, 'Waiting for PostgreSQL before configuring SonarQube TLS', timeout=3600)
    role = cluster.object('secret', 'sonarqube-role', 'postgres')['data']
    ca = cluster.object('secret', 'platform-ca', 'postgres')['data']['ca.crt']
    secret(cluster, 'sonarqube', 'sonarqube-jdbc',
           {'jdbc-password': base64.b64decode(role['password']).decode(),
            'ca.crt': base64.b64decode(ca).decode()})
