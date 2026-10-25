"""Wait for the current gateway policy and generated hostname certificate."""
import json
from pathlib import Path
import ssl
import time
import urllib.request

account = Path('/var/run/secrets/kubernetes.io/serviceaccount')
context = ssl.create_default_context(cafile=str(account / 'ca.crt'))
headers = {'Authorization': 'Bearer ' + (account / 'token').read_text().strip()}


def ready(path, ancestor=False):
    req = urllib.request.Request('https://kubernetes.default.svc' + path, headers=headers)
    with urllib.request.urlopen(req, context=context, timeout=10) as response:
        obj = json.load(response)
    conditions = ((obj.get('status', {}).get('ancestors') or [{}])[0].get('conditions', []) if ancestor
                  else obj.get('status', {}).get('conditions', []))
    expected = 'Accepted' if ancestor else 'Ready'
    return any(c.get('type') == expected and c.get('status') == 'True'
               and c.get('observedGeneration') == obj['metadata']['generation'] for c in conditions)


for _ in range(60):
    try:
        if (ready('/apis/gateway.envoyproxy.io/v1alpha1/namespaces/devlake/securitypolicies/devlake-sso', True)
                and ready('/apis/cert-manager.io/v1/namespaces/devlake/certificates/devlake-tls')):
            print('DevLake OIDC policy accepted and hostname certificate ready.')
            break
    except OSError:
        pass
    time.sleep(5)
else:
    raise RuntimeError('DevLake access is not ready; inspect SecurityPolicy and Certificate conditions')
