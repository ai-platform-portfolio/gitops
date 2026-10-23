#!/usr/bin/env python3
"""Prepare retained platform containers using the authenticated Azure CLI account."""
import argparse
import json
import os
from pathlib import Path
import subprocess

if __package__:
    from .azure import current_account, terraform_environment
else:
    from azure import current_account, terraform_environment


def configuration(infra, tfvars, account):
    result = subprocess.run([os.environ.get('TERRAFORM', 'terraform'), f'-chdir={infra}', 'console', '-no-color',
                             '-var-file=' + str(tfvars)], input='jsonencode(var.platform)\n',
                            env=terraform_environment(account),
                            text=True, capture_output=True)
    if result.returncode:
        raise RuntimeError('Cannot evaluate platform tfvars; run make init and check configuration')
    # Console may emit provider/input warnings before the final quoted result.
    for line in reversed(result.stdout.splitlines()):
        if line.startswith('"'):
            return json.loads(json.loads(line))
    raise RuntimeError('Terraform console did not return the platform input object')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--infra-dir', type=Path, required=True)
    parser.add_argument('--tfvars', type=Path, required=True)
    args = parser.parse_args()
    account = current_account()
    settings = configuration(args.infra_dir.resolve(), args.tfvars.resolve(), account)
    # Containers are retained platform services, outside cluster Terraform
    # ownership. Only create missing containers; never delete them on down.
    for container in settings['storage']['containers'].values():
        subprocess.run(['az', 'storage', 'container', 'create', '--name', container['name'],
                        '--account-name', settings['storage']['account_name'], '--auth-mode', 'login',
                        '--subscription', account['subscription_id'], '--only-show-errors', '-o', 'none'], check=True)
    print('Retained platform containers are ready.', flush=True)


if __name__ == '__main__':
    main()
