#!/usr/bin/env bash
set -euo pipefail

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

[[ ${BASE_REPO_OWNER:-}/${BASE_REPO_NAME:-} == "$SIGNAL_REPOSITORY" ]] || fail 'Unexpected repository'
[[ ${WORKSPACE:-} == default ]] || fail 'Unexpected workspace'
[[ ${REPO_REL_DIR:-} =~ ^clusters/(aks-[a-z0-9]+(-[a-z0-9]+)*)$ ]] || fail 'Invalid cluster directory'
cluster_name=${BASH_REMATCH[1]}
[[ ${HEAD_COMMIT:-} =~ ^[a-f0-9]{40,64}$ ]] || fail 'Invalid source revision'

# A new marker also triggers onboarding when the same revision is applied again.
request_id=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
api_url="https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT_HTTPS}"
payload=$(printf '{"data":{"revision":"%s","request":"%s"}}' "$HEAD_COMMIT" "$request_id")

# Read the rotated token each time; keep it out of process arguments and output.
printf 'header = "Authorization: Bearer %s"\n' "$(cat "$SIGNAL_TOKEN_DIR/token")" |
  curl --config - --silent --show-error --fail --output /dev/null \
    --cacert "$SIGNAL_TOKEN_DIR/ca.crt" \
    --connect-timeout 10 --max-time 20 --retry 3 --retry-max-time 60 \
    --request PATCH --header 'Content-Type: application/merge-patch+json' \
    --data-binary "$payload" \
    "$api_url/api/v1/namespaces/${SIGNAL_NAMESPACE}/configmaps/onboard-${cluster_name}"
printf 'Published onboarding signal for %s\n' "$cluster_name"
