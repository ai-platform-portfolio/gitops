"""LiteLLM-only logical recovery through the native Blob CSI mount.

The lifecycle owns the single backup Job; Argo owns the ordered restore Job.
Neither runs while the other is active. No cloud login or transfer client here.
"""
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import traceback


store = Path(os.environ.get('ACCOUNTING_STORE', '/accounting'))
scripts = Path(os.environ.get('ACCOUNTING_SCRIPTS', '/scripts'))
work = Path(os.environ.get('ACCOUNTING_WORK', '/work'))


def sql(query):
    return subprocess.check_output(['psql', '-XAt', '-v', 'ON_ERROR_STOP=1', '-c', query], stderr=subprocess.PIPE).decode().strip()


def command(*args):
    subprocess.run(args, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def digest(path):
    with path.open('rb') as source:
        return hashlib.file_digest(source, 'sha256').hexdigest()


def save_head(head):
    # BlobFuse is configured for synchronous close, not background uploads.
    payload = json.dumps(head, sort_keys=True)
    with (store / 'head.json').open('w') as target:
        target.write(payload)
    if (store / 'head.json').read_text() != payload:
        raise ValueError('Checkpoint readback failed')


def main(operation):
    work.mkdir(exist_ok=True)
    head_path = store / 'head.json'
    head = json.loads(head_path.read_text()) if head_path.exists() else {
        'platform': os.environ['ACCOUNTING_PLATFORM'], 'state': 'initializing', 'snapshots': []}
    assert head['platform'] == os.environ['ACCOUNTING_PLATFORM']
    assert head['state'] in ('initializing', 'active', 'clean') and len(head['snapshots']) <= 2
    assert head['state'] != 'clean' or head['snapshots']
    for snapshot in head['snapshots']:
        assert re.fullmatch(r'[a-f0-9-]{36}', snapshot['id'])
        assert re.fullmatch(r'[a-f0-9]{64}', snapshot['sha256'])
    tables = sql("SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname NOT IN ('pg_catalog','information_schema') AND n.nspname !~ '^pg_toast' AND c.relkind IN ('r','p','S','v','m','f')")
    if operation == 'restore':
        if tables == '0':
            if head['state'] == 'clean':
                latest = head['snapshots'][0]
                dump = store / (latest['id'] + '.dump')
                assert digest(dump) == latest['sha256']
                command('pg_restore', '--exit-on-error', '--single-transaction', '--no-owner', '--no-acl', '--dbname', os.environ['PGDATABASE'], str(dump))
            else:
                assert head['state'] == 'initializing' and not list(store.glob('*.dump'))
        head['state'] = 'active'  # Invalidate old clean handoff before serving.
        save_head(head)
    else:
        assert operation == 'backup' and tables != '0'
        proof = json.loads((scripts / 'shutdown.json').read_text())
        assert proof['clean'] is True and proof['image'] == os.environ['PROXY_IMAGE']
        assert proof['pod_uid'] and proof['completed_at']
        assert sql('SELECT count(*) FROM pg_stat_activity WHERE datname=current_database() AND pid<>pg_backend_pid()') == '0'
        for owner in json.loads((scripts / 'expected-owners.json').read_text()):
            assert re.fullmatch(r'consumer-[a-f0-9]{64}', owner)
            assert sql(f'SELECT count(*) FROM "LiteLLM_UserTable" WHERE user_id=\'{owner}\'') == '1'
        if head['state'] == 'clean':
            assert head['snapshots'][0]['source_pod_uid'] == proof['pod_uid']
            latest = head['snapshots'][0]
            assert digest(store / (latest['id'] + '.dump')) == latest['sha256']
        else:
            assert head['state'] == 'active'
            local = work / 'database.dump'
            command('pg_dump', '--format=custom', '--no-owner', '--no-acl', '--file', str(local), os.environ['PGDATABASE'])
            command('pg_restore', '--list', str(local))
            generation = os.environ['POD_UID']
            assert re.fullmatch(r'[a-f0-9-]{36}', generation)
            destination = store / (generation + '.dump')
            with local.open('rb') as source, destination.open('wb') as target:
                shutil.copyfileobj(source, target)
            checksum = digest(local)
            assert local.stat().st_size > 0 and digest(destination) == checksum
            snapshot = dict(id=generation, sha256=checksum, source_pod_uid=proof['pod_uid'])
            head.update(state='clean', snapshots=[snapshot] + head['snapshots'][:1])
            save_head(head)  # Publish only after successful synchronous upload/readback.
        retained = {s['id'] + '.dump' for s in head['snapshots']}
        for path in store.glob('*.dump'):
            if path.name not in retained:
                path.unlink()  # Retryable pruning; never remove either retained dump.
    print('ACCOUNTING_RECOVERY_COMPLETE operation=' + operation)


if __name__ == '__main__':
    try:
        main(sys.argv[1])
    except Exception:
        # SQL, credentials and storage responses must not escape through Job logs.
        sys.exit('ACCOUNTING_RECOVERY_FAILED line=' + str(traceback.extract_tb(sys.exc_info()[2])[-1].lineno))
