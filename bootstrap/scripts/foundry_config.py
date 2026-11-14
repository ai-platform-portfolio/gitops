#!/usr/bin/env python3
"""Publish the current bootstrap-owned Foundry project configuration for CI."""
import argparse
import json
import os
import re
import subprocess


def publish(infra_dir):
    settings = json.loads(subprocess.run(
        [os.environ.get('TERRAFORM', 'terraform'), f'-chdir={infra_dir}',
         'output', '-json', 'foundry_ci'],
        check=True, text=True, capture_output=True, timeout=30).stdout)
    organization = settings['organization']
    endpoint = settings['endpoint']
    location = settings['location']

    if not (re.fullmatch(r'[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*', organization)
            and re.fullmatch(r'https://[a-z0-9.-]+/api/projects/[A-Za-z0-9._-]+/?', endpoint)
            and re.fullmatch(r'[a-z0-9]+', location)):
        raise ValueError('Terraform must supply a GitHub organization, HTTPS project endpoint and Azure region')

    # Update the existing variables on every startup, including after a full rebuild.
    # Native gh creates missing variables; no per-repository registration is needed.
    for name, value in [('FOUNDRY_LOCATION', location), ('FOUNDRY_PROJECT_ENDPOINT', endpoint)]:
        subprocess.run(['gh', 'variable', 'set', name, '--org', organization,
                        '--visibility', 'all', '--body', value], check=True, timeout=30)
    print('Published current Foundry project endpoint and region to GitHub organization variables')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--infra-dir', required=True)
    publish(parser.parse_args().infra_dir)
