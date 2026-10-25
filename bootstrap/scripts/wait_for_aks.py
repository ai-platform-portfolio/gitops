#!/usr/bin/env python3
"""Wait for the newly provisioned AKS API to accept authenticated requests."""

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

    with tempfile.TemporaryDirectory(prefix='aks-api-ready-') as directory:
        kubeconfig = str(Path(directory) / 'config')
        print('Waiting for authenticated AKS API readiness', flush=True)

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
                            consecutive += 1
                            if consecutive == REQUIRED_SUCCESSES:
                                print('AKS API accepted three consecutive readiness checks', flush=True)
                                return
                        else:
                            consecutive = 0
                    else:
                        consecutive = 0
                else:
                    consecutive = 0
            except (OSError, subprocess.TimeoutExpired):
                consecutive = 0

            time.sleep(POLL_SECONDS)

    raise SystemExit('AKS API did not accept authenticated requests within 10 minutes; '
                     'check API allowlist propagation and caller access')


if __name__ == '__main__':
    main()
