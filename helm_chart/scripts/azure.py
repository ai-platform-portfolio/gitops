"""Read the authenticated Azure CLI context without printing account identifiers."""
import json
import subprocess


def current_account():
    result = subprocess.run(['az', 'account', 'show', '-o', 'json', '--only-show-errors'],
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Cannot read the Azure CLI account; authenticate and select a subscription')
    selected = json.loads(result.stdout)
    account = {'subscription_id': selected.get('id'), 'tenant_id': selected.get('tenantId')}
    if not all(account.values()):
        raise ValueError('Azure CLI account must include tenant and subscription identifiers')
    return account
