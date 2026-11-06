#!/usr/bin/env python3
"""Wait for AKS API health and the caller permissions required to bootstrap Helm."""

import os
from pathlib import Path
import subprocess
import tempfile
import time


TIMEOUT_SECONDS = 600
POLL_SECONDS = 10
REQUIRED_SUCCESSES = 3


def run(*args):
    return subprocess.run(args, text=True, capture_output=True, timeout=30)


def main():
    cluster = os.environ['AKS_NAME']
    resource_group = os.environ['AKS_RESOURCE_GROUP']
    deadline = time.monotonic() + TIMEOUT_SECONDS
    consecutive = 0
    last_problem = 'credentials or API unavailable'

    with tempfile.TemporaryDirectory(prefix='aks-api-ready-') as directory:
        kubeconfig = str(Path(directory) / 'config')
        print('Waiting for AKS API health and bootstrap authorisation', flush=True)

        while time.monotonic() < deadline:
            try:
                credentials = run('az', 'aks', 'get-credentials', '--resource-group',
                                  resource_group, '--name', cluster, '--file', kubeconfig,
                                  '--overwrite-existing', '--only-show-errors')
                if credentials.returncode == 0:
                    login = run('kubelogin', 'convert-kubeconfig', '--kubeconfig',
                                kubeconfig, '-l', 'azurecli')
                    if login.returncode == 0:
                        ready = run('kubectl', '--kubeconfig', kubeconfig,
                                    '--request-timeout=10s', 'get', '--raw=/readyz')
                        if ready.returncode == 0 and ready.stdout.strip() == 'ok':
                            allowed = run('kubectl', '--kubeconfig', kubeconfig,
                                          '--request-timeout=10s', 'auth', 'can-i', '*', '*',
                                          '--all-namespaces')
                            if allowed.returncode == 0 and allowed.stdout.strip() == 'yes':
                                consecutive += 1
                                if consecutive == REQUIRED_SUCCESSES:
                                    print('AKS API health and bootstrap authorisation passed three consecutive checks', flush=True)
                                    return
                            else:
                                consecutive = 0
                                last_problem = 'API reachable but bootstrap permissions denied'
                        else:
                            consecutive = 0
                            last_problem = 'API not ready'
                    else:
                        consecutive = 0
                else:
                    consecutive = 0
            except (OSError, subprocess.TimeoutExpired):
                consecutive = 0

            time.sleep(POLL_SECONDS)

    raise SystemExit(f'AKS bootstrap readiness timed out: {last_problem}. '
                     'Check API access and operator-group membership. If membership changed, '
                     'renew the Azure CLI sign-in so its token includes the current group.')


if __name__ == '__main__':
    main()
