#!/bin/sh
set -eu
# Application startup only warns on migration errors; require the pinned schema.
expected=20260917090000
for attempt in $(seq 1 60); do
  actual=$(psql -X -A -t -v ON_ERROR_STOP=1 -c 'SELECT max(version_id) FROM goose_db_version WHERE is_applied' 2>/dev/null || true)
  if [ "$actual" = "$expected" ]; then
    printf '%s\n' 'Testkube PostgreSQL migrations verified'
    exit 0
  fi
  sleep 5
done
printf '%s\n' 'Testkube schema did not reach the pinned migration version' >&2
exit 1
