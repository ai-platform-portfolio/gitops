#!/bin/sh
# Server-owned apply step: no credentials or commands come from repository content.
set -eu

fail() {
  printf '%s\n' "$1" >&2
  exit 1
}

[ "${BASE_REPO_OWNER:-}/${BASE_REPO_NAME:-}" = "$SIGNAL_REPOSITORY" ] || fail 'Unexpected signal repository'
[ "${WORKSPACE:-}" = default ] || fail 'Unexpected signal workspace'

cluster_name=${REPO_REL_DIR#clusters/}
[ "$REPO_REL_DIR" = "clusters/$cluster_name" ] || fail 'Expected a cluster directory'
case "$cluster_name" in *[!a-z0-9-]*|'') fail 'Invalid cluster name' ;; esac
case "${HEAD_COMMIT:-}" in *[!a-f0-9]*|'') fail 'Invalid source revision' ;; esac
printf '%s' "$cluster_name" | LC_ALL=C grep -Eq '^aks-[a-z0-9]+(-[a-z0-9]+)*$' || fail 'Invalid cluster name'
printf '%s' "${HEAD_COMMIT:-}" | LC_ALL=C grep -Eq '^[a-f0-9]{40,64}$' || fail 'Invalid source revision'

# A fresh request identifies each successful apply, including a no-op reapply.
request_id=$(od -An -N16 -tx1 /dev/urandom | tr -d ' \n')
payload=$(mktemp)
trap 'rm -f "$payload"' EXIT HUP INT TERM
printf '{"data":{"phase":"applied","repository":"%s","directory":"%s","workspace":"default","revision":"%s","request":"%s"}}' \
  "$SIGNAL_REPOSITORY" "$REPO_REL_DIR" "$HEAD_COMMIT" "$request_id" > "$payload"

# Re-read the kubelet-rotated token for each invocation. Pass the header over stdin,
# never in command arguments, a repository file or the Atlantis PR output.
api_token=$(cat "$SIGNAL_TOKEN_DIR/token")
printf 'header = "Authorization: Bearer %s"\n' "$api_token" |
  curl --config - --silent --show-error --fail --output /dev/null \
    --cacert "$SIGNAL_TOKEN_DIR/ca.crt" \
    --connect-timeout 10 --max-time 20 --retry 3 --retry-max-time 60 \
    --request PATCH --header 'Content-Type: application/merge-patch+json' \
    --data-binary "@$payload" \
    "https://${KUBERNETES_SERVICE_HOST}:${KUBERNETES_SERVICE_PORT_HTTPS}/api/v1/namespaces/${SIGNAL_NAMESPACE}/configmaps/onboard-${cluster_name}"
printf 'Published onboarding signal for %s\n' "$cluster_name"
