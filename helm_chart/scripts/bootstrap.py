#!/usr/bin/env python3
"""Apply bootstrap; recover one known Azure failed-create orphan, never tracked resources."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import tempfile

if __package__:
    from .azure import current_account
else:
    from azure import current_account

ENDPOINT = 'module.foundry.azurerm_private_endpoint.this'
FAILURE = 'InvalidResponseFromPrivateLinkService'


def read(*args):
    result = subprocess.run(args, text=True, capture_output=True, timeout=120)
    if result.returncode:
        raise RuntimeError('Bootstrap verification or recovery command failed')
    return result.stdout


def verify_endpoint(change, endpoint, subscription):
    """Match actual Azure scope/connection to Terraform's evaluated create, not name guesses."""
    if change.get('actions') != ['create']:
        raise ValueError('Recovery requires a new, unmanaged endpoint')
    desired = change['after']
    expected = (f"/subscriptions/{subscription}/resourceGroups/{desired['resource_group_name']}/"
                f"providers/Microsoft.Network/privateEndpoints/{desired['name']}")
    connections = endpoint.get('privateLinkServiceConnections', [])
    wanted = desired['private_service_connection']
    if (endpoint.get('id', '').lower() != expected.lower()
            or endpoint.get('provisioningState') != 'Failed'
            or endpoint.get('location') != desired['location']
            or endpoint.get('subnet', {}).get('id', '').lower() != desired['subnet_id'].lower()
            or endpoint.get('manualPrivateLinkServiceConnections')
            or len(connections) != 1 or len(wanted) != 1
            or wanted[0].get('is_manual_connection')
            or connections[0].get('privateLinkServiceId', '').lower() != wanted[0]['private_connection_resource_id'].lower()
            or sorted(connections[0].get('groupIds', [])) != sorted(wanted[0]['subresource_names'])):
        raise ValueError('Endpoint does not match the failed bootstrap create; no cleanup was attempted')
    return expected


def apply(tf, tfvars):
    # Stream ordinary Terraform output; retain only the specific Azure failure marker.
    process = subprocess.Popen([*tf, 'apply', '-input=false', '-auto-approve', f'-var-file={tfvars}'],
                               text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    failed_link = False
    for line in process.stdout:
        print(line, end='', flush=True)
        failed_link |= FAILURE in line or 'already exists' in line
    return process.wait(), failed_link


def up(infra, tfvars):
    account = current_account()
    os.environ.update(ARM_SUBSCRIPTION_ID=account['subscription_id'], ARM_TENANT_ID=account['tenant_id'])
    tf = [os.environ.get('TERRAFORM', 'terraform'), f'-chdir={infra}']
    status, failed_link = apply(tf, tfvars)
    if not status or not failed_link:
        return status
    if ENDPOINT in read(*tf, 'state', 'list').splitlines():
        return status

    # Native Terraform evaluates module defaults and the now-created dependencies.
    # Real plans contain sensitive values: keep them private; never print JSON.
    with tempfile.TemporaryDirectory(prefix='bootstrap-recovery-') as directory:
        plan = str(Path(directory) / 'recovery.tfplan')
        read(*tf, 'plan', '-input=false', f'-var-file={tfvars}', f'-out={plan}')
        changes = json.loads(read(*tf, 'show', '-json', plan)).get('resource_changes', [])
        candidates = [r['change'] for r in changes if r['address'] == ENDPOINT]
        if len(candidates) != 1 or candidates[0]['actions'] != ['create']:
            return status
        desired = candidates[0]['after']
        if not all(desired.get(k) for k in ['resource_group_name', 'name', 'subnet_id', 'location', 'private_service_connection']):
            return status
        endpoint_id = (f"/subscriptions/{account['subscription_id']}/resourceGroups/{desired['resource_group_name']}/"
                       f"providers/Microsoft.Network/privateEndpoints/{desired['name']}")
        endpoint = json.loads(read('az', 'network', 'private-endpoint', 'show', '--ids', endpoint_id,
                                   '-o', 'json', '--only-show-errors'))
        verify_endpoint(candidates[0], endpoint, account['subscription_id'])
        parent_id = desired['private_service_connection'][0]['private_connection_resource_id']
        parent = json.loads(read('az', 'resource', 'show', '--ids', parent_id,
                                 '-o', 'json', '--only-show-errors'))
        properties = parent.get('properties', {})
        if properties.get('provisioningState') != 'Succeeded' or properties.get('publicNetworkAccess') != 'Disabled':
            return status
        if ENDPOINT in read(*tf, 'state', 'list').splitlines():
            return status
        print('Recovering the verified Failed, untracked Foundry endpoint; one bootstrap retry.', flush=True)
        read('az', 'network', 'private-endpoint', 'delete', '--ids', endpoint_id, '--only-show-errors')
    # Replan normally: do not apply the pre-delete saved plan or target other resources.
    return apply(tf, tfvars)[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--infra-dir', type=Path, required=True)
    parser.add_argument('--tfvars', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = up(args.infra_dir.resolve(), args.tfvars.resolve())
        if result:
            print('Bootstrap did not complete; partially created infrastructure may remain running.', flush=True)
        return result
    except (OSError, RuntimeError, ValueError, KeyError, subprocess.TimeoutExpired):
        print('Bootstrap recovery could not complete: verification or an Azure operation failed. '
              'Infrastructure may remain running; inspect the failed operation before retrying.', flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
