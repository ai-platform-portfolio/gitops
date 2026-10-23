#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
mkdocs build --strict --config-file catalog/mkdocs.yml --site-dir "$PWD/techdocs/default/component/backstage"
