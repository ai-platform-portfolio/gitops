#!/usr/bin/env bash
# Package-local native Job stages. Never print SQL, tokens, dumps or CLI errors.
set -Eeuo pipefail
umask 077
exec 3>&2
stage=${1:-unknown}
fail() { echo "ACCOUNTING_RECOVERY_FAILED stage=$stage" >&3; exit 1; }
trap fail ERR

work=${ACCOUNTING_WORK:-/work}
scripts=${ACCOUNTING_SCRIPTS:-/scripts}
mkdir -p "$work"
cd "$work"
export AZURE_CONFIG_DIR="$work/azure"

blob() {
  az storage blob "$@" --account-name "$STORAGE_ACCOUNT" --container-name "$STORAGE_CONTAINER" \
    --auth-mode login --only-show-errors
}

# Both official images contain Python; keep validation beside its shell stages.
check() {
  python3 - "$@" <<'PY'
import hashlib, json, os, pathlib, re, sys
from datetime import datetime, timezone
p = pathlib.Path('.')
mode = sys.argv[1]
def read(name):
    return json.loads((p / name).read_text())
def write(name, value):
    (p / name).write_text(json.dumps(value, sort_keys=True))
def digest(name):
    h = hashlib.sha256()
    with open(name, 'rb') as f:
        for chunk in iter(lambda: f.read(1048576), b''):
            h.update(chunk)
    return h.hexdigest()
if mode == 'head':
    h = read('head.json')
    assert h['schema'] == 1 and h['platform'] == os.environ['ACCOUNTING_PLATFORM']
    assert h['state'] in ('initializing', 'active', 'clean')
    assert len(h.get('retired', [])) <= 2 and all(re.fullmatch(r'[a-f0-9-]{36}', i) for i in h.get('retired', []))
    assert isinstance(h['snapshots'], list) and len(h['snapshots']) <= 2
    for s in h['snapshots']:
        assert re.fullmatch(r'[a-f0-9-]{36}', s['id'])
        assert re.fullmatch(r'[a-f0-9]{64}', s['sha256'])
        assert type(s['bytes']) is int and 0 < s['bytes'] <= 1073741824
        assert s['database'] == 'litellm' and s['postgres_major'] == 18 and s['clean'] is True
        assert '@sha256:' in s['proxy_image'] and s['source_pod_uid']
    ids = [s['id'] for s in h['snapshots']]
    assert len(ids) == len(set(ids)) and not (set(ids) & set(h.get('retired', [])))
    assert h['state'] != 'clean' or h['snapshots']
elif mode == 'verify':
    s = read('head.json')['snapshots'][0]
    assert p.joinpath('database.dump').stat().st_size == s['bytes']
    assert digest('database.dump') == s['sha256']
elif mode == 'shutdown':
    shutdown = json.loads(pathlib.Path(os.environ.get('ACCOUNTING_SCRIPTS', '/scripts'), 'shutdown.json').read_text())
    assert shutdown['clean'] is True and shutdown['image'] == os.environ['PROXY_IMAGE']
    assert shutdown['pod_uid'] and shutdown['completed_at']
    write('shutdown.json', shutdown)
    owners = json.loads(pathlib.Path(os.environ.get('ACCOUNTING_SCRIPTS', '/scripts'), 'expected-owners.json').read_text())
    assert isinstance(owners, list) and len(owners) == len(set(owners))
    assert all(re.fullmatch(r'consumer-[a-f0-9]{64}', owner) for owner in owners)
    (p / 'owners').write_text(''.join(owner + '\n' for owner in owners))
elif mode == 'publish':
    h = read('head.json')
    if os.environ['OPERATION'] == 'restore':
        h['state'] = 'active'
    else:
        size = p.joinpath('database.dump').stat().st_size
        assert 0 < size <= 1073741824
        shutdown = read('shutdown.json')
        s = dict(id=os.environ['POD_UID'], sha256=digest('database.dump'), bytes=size,
                 database='litellm', postgres_major=18, clean=True,
                 created_at=datetime.now(timezone.utc).isoformat(), proxy_image=shutdown['image'],
                 source_pod_uid=shutdown['pod_uid'], owners=p.joinpath('owners').read_text().splitlines())
        h.update(retired=h.get('retired', []) + [s['id'] for s in h['snapshots'][1:]],
                 state='clean', snapshots=[s] + h['snapshots'][:1])
    write('next.json', h)
elif mode == 'uploaded':
    assert digest('verified.dump') == read('next.json')['snapshots'][0]['sha256']
else:
    raise ValueError('Unknown accounting validation stage')
PY
}

# Detailed stderr stays in the private, disposable Job volume.
exec 2>"$work/errors.log"
case "$1" in
  prepare)
    az login --service-principal --username "$AZURE_CLIENT_ID" --tenant "$AZURE_TENANT_ID" \
      --federated-token "$(cat "$AZURE_FEDERATED_TOKEN_FILE")" --allow-no-subscriptions \
      --only-show-errors --output none
    exists=$(blob exists --name head.json --query exists -o tsv)
    if [[ "$exists" == false ]]; then
      python3 -c 'import json,os; json.dump(dict(schema=1,platform=os.environ["ACCOUNTING_PLATFORM"],state="initializing",snapshots=[]),open("initial.json","w"))'
      blob upload --name head.json --file initial.json --if-none-match '*' --overwrite false -o none
    else
      [[ "$exists" == true ]] || fail
    fi
    blob show --name head.json --query properties.etag -o tsv > etag
    [[ -s etag ]] || fail
    blob download --name head.json --file head.json --if-match "$(cat etag)" -o none
    check head
    if [[ "$OPERATION" == restore && $(python3 -c 'import json; print(json.load(open("head.json"))["state"])') == clean ]]; then
      snapshot=$(python3 -c 'import json; print(json.load(open("head.json"))["snapshots"][0]["id"])')
      blob download --name "snapshots/$snapshot.dump" --file database.dump -o none
      check verify
    fi
    ;;

  database)
    tables=$(psql -XAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_toast' AND c.relkind IN ('r','p','S','v','m','f')")
    state=$(python3 -c 'import json; print(json.load(open("head.json"))["state"])')
    if [[ "$OPERATION" == restore ]]; then
      if [[ "$tables" != 0 ]]; then
        # Reapply/upgrade never restores over an existing database.
        touch populated
      elif [[ "$state" == clean ]]; then
        check verify
        pg_restore --exit-on-error --single-transaction --no-owner --no-acl --dbname "$PGDATABASE" database.dump
      else
        # Only a never-activated store may initialize without a clean snapshot.
        [[ "$state" == initializing ]] || fail
      fi
    else
      [[ "$OPERATION" == backup && "$tables" != 0 ]] || fail
      check shutdown
      [[ $(psql -XAt -v ON_ERROR_STOP=1 -c 'SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()') == 0 ]] || fail
      while IFS= read -r owner; do
        [[ $(psql -XAt -v ON_ERROR_STOP=1 -c "SELECT count(*) FROM \"LiteLLM_UserTable\" WHERE user_id='$owner'") == 1 ]] || fail
      done < owners
      if [[ "$state" == clean ]]; then
        # Resume after a successful publication, without generating a duplicate.
        [[ $(python3 -c 'import json; h=json.load(open("head.json")); s=json.load(open("shutdown.json")); print(h["snapshots"][0]["source_pod_uid"]==s["pod_uid"])') == True ]] || fail
        touch already-published
      else
        [[ "$state" == active ]] || fail
        pg_dump --format=custom --no-owner --no-acl --file database.dump "$PGDATABASE"
        pg_restore --list database.dump > archive.list
      fi
    fi
    ;;

  publish)
    if [[ -f already-published ]]; then
      cp head.json next.json
    else
      check publish
    fi
    if [[ "$OPERATION" == backup && ! -f already-published ]]; then
      blob upload --name "snapshots/$POD_UID.dump" --file database.dump --overwrite false -o none
      blob download --name "snapshots/$POD_UID.dump" --file verified.dump -o none
      check uploaded
    fi
    # A concurrent/stale Job may not replace a newer recovery decision.
    blob upload --name head.json --file next.json --if-match "$(cat etag)" --overwrite true -o none
    if [[ "$OPERATION" == backup ]]; then
      # Delete only a previously verified snapshot rotated out of this head.
      python3 -c 'import json; print("\n".join(json.load(open("next.json")).get("retired", [])))' > obsolete
      while IFS= read -r snapshot; do
        [[ -n "$snapshot" ]] || continue
        blob delete --name "snapshots/$snapshot.dump" -o none
      done < obsolete
      blob show --name head.json --query properties.etag -o tsv > etag
      blob download --name head.json --file committed.json --if-match "$(cat etag)" -o none
      python3 -c 'import json; actual=json.load(open("committed.json")); expected=json.load(open("next.json")); assert actual==expected; actual["retired"]=[]; json.dump(actual,open("pruned.json","w"))'
      blob upload --name head.json --file pruned.json --if-match "$(cat etag)" --overwrite true -o none
    fi
    echo "ACCOUNTING_RECOVERY_COMPLETE operation=$OPERATION"
    ;;

  *) exit 1 ;;
esac
