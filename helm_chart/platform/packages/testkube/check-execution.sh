#!/bin/sh
set -eu
export HOME=/tmp
api=http://testkube-api-server.testkube.svc.cluster.local:8088
# RBAC propagation can lag ARM Ready. Retry within the Job's finite deadline.
passed=false
for attempt in $(seq 1 20); do
  rm -rf /tmp/artifacts
  if testkube --client direct --api-uri "$api" run testworkflow platform-storage-smoke \
      --config candidateUrl="$api" --tag platform-check="$HOSTNAME" \
      --watch --download-artifacts --download-dir /tmp/artifacts; then
    if find /tmp/artifacts -name proof.txt -exec grep -l '^testkube-azure-artifact-roundtrip$' {} \; | grep -q .; then
      passed=true
      break
    fi
  fi
  sleep 15
done
[ "$passed" = true ] || { printf '%s\n' 'Artifact execution/download check failed' >&2; exit 1; }
if testkube --client direct --api-uri "$api" run testworkflow platform-failure-smoke \
    --tag platform-check="$HOSTNAME" --watch; then
  printf '%s\n' 'Failed test did not propagate a nonzero exit status' >&2
  exit 1
fi
# A connection error alone must not count as the deliberately failed execution.
testkube --client direct --api-uri "$api" get testworkflowexecution \
  --testworkflow platform-failure-smoke --tag platform-check="$HOSTNAME" \
  --status failed --limit 1 --output json > /tmp/failure.json
grep -Eq '"status"[[:space:]]*:[[:space:]]*"failed"' /tmp/failure.json
printf '%s\n' 'Testkube artifact roundtrip and failure propagation verified'
