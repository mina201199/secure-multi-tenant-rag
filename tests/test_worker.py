"""TXT worker 的真實登入、恢復與發布驗收；不以 owner 冒充 worker。"""
import os
import secrets
import subprocess
import sys
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from threading import Event

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.conninfo import make_conninfo
from psycopg import sql

from app import bootstrap, version_service as service, worker
from tests.test_version_writes import write_document, create, pointer
from tests.helpers import login


@pytest.fixture
def workerDsn(admin_db):
  password = secrets.token_hex(24)
  bootstrap.ensure_worker_role(admin_db, password)
  return make_conninfo(os.environ['DATABASE_ADMIN_URL'], user='rag_worker', password=password)


@pytest.fixture
def workerDb(workerDsn):
  with worker.connectWorker(workerDsn) as connection:
    yield connection


def expire(adminDb, jobId):
  adminDb.execute("UPDATE app_core.version_jobs SET lease_expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (jobId,))


def available(adminDb, jobId):
  adminDb.execute("UPDATE app_core.version_jobs SET available_at=clock_timestamp()-interval '1 second' WHERE id=%s", (jobId,))


def claimJob(connection):
  job = service.claim_job(connection)
  connection.commit()
  return job


def recoverJob(connection):
  result = service.recover_job(connection)
  connection.commit()
  return result


def test_worker_publishes_saved_txt_private_and_revokes_old_read_access(workerDb, runtime_db, admin_db, write_document, client):
  result = create(runtime_db, write_document, b'\xef\xbb\xbf' + '新規定 NT$123\r\n第二段'.encode())
  login(client, 'a.reader')
  documentId = write_document['id']
  assert client.get(f'/api/documents/{documentId}').status_code == 200
  assert worker.runOnce(workerDb) is True
  assert pointer(admin_db, write_document) == result['version_id']
  assert admin_db.execute('SELECT content,status,visibility FROM app_core.document_versions WHERE id=%s',
                         (result['version_id'],)).fetchone() == ('新規定 NT$123\r\n第二段', 'ready', 'private')
  assert admin_db.execute('SELECT count(*) FROM app_core.version_readers WHERE version_id=%s',
                         (result['version_id'],)).fetchone() == (0,)
  assert service.get_job(runtime_db, write_document['owner'], result['job_id'])['status'] == 'ready'
  assert client.get(f'/api/documents/{documentId}').status_code == 404
  assert client.get(f'/documents/{documentId}').status_code == 404
  client.cookies.clear()
  login(client, 'a.owner')
  assert client.get(f'/api/documents/{documentId}').json()['content'] == '新規定 NT$123\r\n第二段'
  assert worker.runOnce(workerDb) is False


def test_live_lease_cannot_be_recovered_or_claimed_twice(workerDb, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document)
  first = claimJob(workerDb)
  assert first['job_id'] == result['job_id']
  assert recoverJob(workerDb) is None
  assert claimJob(workerDb) is None
  assert pointer(admin_db, write_document) == write_document['old']


def test_expired_job_retries_after_backoff_with_new_token(workerDb, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document)
  first = claimJob(workerDb)
  expire(admin_db, result['job_id'])
  recovered = recoverJob(workerDb)
  assert recovered['status'] == 'queued'
  assert pointer(admin_db, write_document) == write_document['old']
  assert claimJob(workerDb) is None
  assert admin_db.execute('SELECT attempts,available_at>clock_timestamp(),lease_token FROM app_core.version_jobs WHERE id=%s',
                         (result['job_id'],)).fetchone() == (1, True, None)
  available(admin_db, result['job_id'])
  second = claimJob(workerDb)
  assert second['job_id'] == first['job_id']
  assert second['lease_token'] != first['lease_token']
  with pytest.raises(HTTPException) as caught:
    service.complete_job(workerDb, first['job_id'], first['lease_token'])
  workerDb.rollback()
  assert caught.value.status_code == 409
  service.complete_job(workerDb, second['job_id'], second['lease_token'])
  workerDb.commit()
  assert pointer(admin_db, write_document) == result['version_id']
  assert admin_db.execute('SELECT attempts FROM app_core.version_jobs WHERE id=%s', (result['job_id'],)).fetchone() == (2,)


def test_three_lost_leases_stop_without_replacing_old_version(workerDb, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document)
  for attempt in range(1, 4):
    available(admin_db, result['job_id'])
    assert claimJob(workerDb) is not None
    expire(admin_db, result['job_id'])
    recovered = recoverJob(workerDb)
    assert recovered['status'] == ('failed' if attempt == 3 else 'queued')
  assert recoverJob(workerDb) is None
  assert claimJob(workerDb) is None
  assert pointer(admin_db, write_document) == write_document['old']
  assert admin_db.execute('SELECT status,error_code,attempts FROM app_core.version_jobs WHERE id=%s',
                         (result['job_id'],)).fetchone() == ('failed', 'lease_expired', 3)
  assert admin_db.execute('SELECT status,content FROM app_core.document_versions WHERE id=%s',
                         (result['version_id'],)).fetchone() == ('failed', None)


@pytest.mark.parametrize('change,reason', [
  ('inactive', 'actor_inactive'), ('archived', 'archived'), ('owner', 'ownership_changed'),
  ('target_owner', 'ownership_changed'), ('target_state', 'version_unavailable'),
  ('base', 'base_conflict'), ('outgoing_state', 'version_unavailable'),
])
def test_recovery_never_revives_invalid_work(workerDb, runtime_db, admin_db, write_document, change, reason):
  doc = write_document
  result = create(runtime_db, doc)
  claimJob(workerDb)
  expire(admin_db, result['job_id'])
  if change == 'inactive':
    admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (doc['owner']['id'],))
  elif change == 'archived':
    admin_db.execute("UPDATE app_core.documents SET status='archived' WHERE id=%s", (doc['id'],))
  elif change == 'owner':
    admin_db.execute('UPDATE app_core.documents SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'], doc['id']))
  elif change == 'target_owner':
    admin_db.execute('UPDATE app_core.document_versions SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'], result['version_id']))
  elif change == 'target_state':
    admin_db.execute("UPDATE app_core.document_versions SET status='archived' WHERE id=%s", (result['version_id'],))
  elif change == 'base':
    admin_db.execute('UPDATE app_core.documents SET current_version_id=NULL WHERE id=%s', (doc['id'],))
  else:
    admin_db.execute("UPDATE app_core.document_versions SET status='failed' WHERE id=%s", (doc['old'],))
  before = pointer(admin_db, doc)
  recovered = recoverJob(workerDb)
  assert recovered['status'] == 'failed'
  assert recovered['error_code'] == reason
  assert claimJob(workerDb) is None
  assert pointer(admin_db, doc) == before


def test_publication_rechecks_after_recovery(workerDb, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document)
  claimJob(workerDb)
  expire(admin_db, result['job_id'])
  recoverJob(workerDb)
  available(admin_db, result['job_id'])
  admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (write_document['owner']['id'],))
  assert worker.runOnce(workerDb)
  assert pointer(admin_db, write_document) == write_document['old']
  assert admin_db.execute('SELECT status,error_code FROM app_core.version_jobs WHERE id=%s',
                         (result['job_id'],)).fetchone() == ('failed', 'actor_inactive')


@pytest.mark.parametrize('statement', [
  'SELECT * FROM app_core.version_sources', 'SELECT * FROM app_core.users',
  'SELECT * FROM app_core.version_jobs', 'SELECT * FROM app_core.document_versions',
  'UPDATE app_core.documents SET current_version_id=NULL',
  "UPDATE app_core.version_jobs SET status='queued'", 'SET ROLE rag_version_writer',
  'SET ROLE rag_app', 'CREATE TABLE app_core.worker_owned(id int)',
  "SELECT app_core.create_version(1,1,1,1,'new'::bytea,'worker_key_000001')",
])
def test_real_worker_login_has_no_direct_data_or_impersonation_rights(workerDb, statement):
  with pytest.raises(psycopg.errors.InsufficientPrivilege):
    workerDb.execute(statement)
  workerDb.rollback()


def test_web_cannot_recover_jobs(runtime_db):
  with pytest.raises(psycopg.errors.InsufficientPrivilege):
    runtime_db.execute('SELECT app_core.recover_version_job()')


@pytest.mark.parametrize('dsnName', ['DATABASE_URL', 'DATABASE_ADMIN_URL'])
def test_worker_refuses_web_and_owner_connections(dsnName):
  with pytest.raises(ValueError):
    worker.connectWorker(os.environ[dsnName])


def test_concurrent_workers_only_claim_once(workerDsn, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document)
  def run():
    with worker.connectWorker(workerDsn) as connection:
      return worker.runOnce(connection)
  with ThreadPoolExecutor(max_workers=2) as pool:
    list(pool.map(lambda _: run(), range(2)))
  assert pointer(admin_db, write_document) == result['version_id']
  assert admin_db.execute('SELECT attempts FROM app_core.version_jobs WHERE id=%s', (result['job_id'],)).fetchone() == (1,)


def test_two_recoverers_only_requeue_once(workerDsn, workerDb, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document)
  claimJob(workerDb)
  expire(admin_db, result['job_id'])
  def recover():
    with worker.connectWorker(workerDsn) as connection:
      return recoverJob(connection)
  with ThreadPoolExecutor(max_workers=2) as pool:
    results = list(pool.map(lambda _: recover(), range(2)))
  assert sum(value is not None for value in results) == 1
  assert admin_db.execute('SELECT attempts,status FROM app_core.version_jobs WHERE id=%s',
                         (result['job_id'],)).fetchone() == (1, 'queued')


def test_cli_once_publishes_without_logging_source_or_credentials(workerDsn, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document, b'private-sentinel-content-0921')
  environment = {**os.environ, 'WORKER_DATABASE_URL': workerDsn}
  process = subprocess.run([sys.executable, '-m', 'app.worker', '--once'], env=environment,
                           capture_output=True, text=True, timeout=15)
  assert process.returncode == 0, process.stdout + process.stderr
  assert pointer(admin_db, write_document) == result['version_id']
  assert 'private-sentinel-content-0921' not in process.stdout + process.stderr
  assert workerDsn not in process.stdout + process.stderr


def test_cli_invalid_config_exits_without_leaking_dsn():
  sentinel = 'do-not-log-this-secret'
  process = subprocess.run([sys.executable, '-m', 'app.worker', '--once'],
      env={**os.environ, 'WORKER_DATABASE_URL': sentinel}, capture_output=True, text=True, timeout=15)
  assert process.returncode != 0
  assert sentinel not in process.stdout + process.stderr
  assert 'Traceback' not in process.stdout + process.stderr


def test_worker_sigterm_stops_idle_loop(workerDsn):
  process = subprocess.Popen([sys.executable, '-u', '-m', 'app.worker'],
      env={**os.environ, 'WORKER_DATABASE_URL': workerDsn}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
  try:
    # 固定 ready 事件表示已驗證身分並安裝訊號 handler。
    import select
    assert select.select([process.stdout], [], [], 10)[0]
    assert process.stdout.readline().strip() == 'worker_started'
    process.terminate()
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0
    assert 'Traceback' not in stdout + stderr
  finally:
    if process.poll() is None:
      process.kill()
      process.communicate(timeout=5)


def test_killed_process_leaves_durable_lease_then_new_worker_recovers(workerDsn, workerDb, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document)
  script = '''import os,time
from app.worker import connectWorker
from app.version_service import claim_job
with connectWorker(os.environ['WORKER_DATABASE_URL']) as conn:
  assert claim_job(conn) is not None
  conn.commit()
  print('claimed', flush=True)
  time.sleep(60)
'''
  process = subprocess.Popen([sys.executable, '-u', '-c', script],
      env={**os.environ, 'WORKER_DATABASE_URL': workerDsn}, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
  try:
    import select
    assert select.select([process.stdout], [], [], 10)[0]
    assert process.stdout.readline().strip() == 'claimed'
    process.kill()
    process.communicate(timeout=10)
    assert admin_db.execute('SELECT status,attempts FROM app_core.version_jobs WHERE id=%s',
                           (result['job_id'],)).fetchone() == ('processing', 1)
    expire(admin_db, result['job_id'])
    assert worker.runOnce(workerDb)
    assert pointer(admin_db, write_document) == write_document['old']
    available(admin_db, result['job_id'])
    assert worker.runOnce(workerDb)
    assert pointer(admin_db, write_document) == result['version_id']
  finally:
    if process.poll() is None:
      process.kill()
      process.communicate(timeout=5)


def test_recovery_rollback_preserves_lease_and_attempt_count(workerDb, runtime_db, admin_db, write_document):
  result = create(runtime_db, write_document)
  first = claimJob(workerDb)
  expire(admin_db, result['job_id'])
  service.recover_job(workerDb)
  workerDb.rollback()
  assert admin_db.execute('SELECT status,attempts,lease_token::text FROM app_core.version_jobs WHERE id=%s',
                         (result['job_id'],)).fetchone() == ('processing', 1, first['lease_token'])
  assert recoverJob(workerDb)['status'] == 'queued'


@pytest.mark.parametrize('terminal', ['ready', 'failed'])
def test_terminal_jobs_are_not_recovered(workerDb, runtime_db, admin_db, write_document, terminal):
  result = create(runtime_db, write_document)
  job = claimJob(workerDb)
  operation = service.complete_job if terminal == 'ready' else service.fail_job
  operation(workerDb, job['job_id'], job['lease_token'])
  workerDb.commit()
  assert recoverJob(workerDb) is None
  assert claimJob(workerDb) is None
  assert admin_db.execute('SELECT status FROM app_core.version_jobs WHERE id=%s', (result['job_id'],)).fetchone() == (terminal,)


def test_upgrade_from_004_preserves_claimed_source_and_resumes_without_new_version(admin_db):
  databaseName = 'worker_upgrade_' + uuid.uuid4().hex
  admin_db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(databaseName)))
  try:
    with psycopg.connect(os.environ['DATABASE_ADMIN_URL'], dbname=databaseName) as connection:
      connection.execute('CREATE SCHEMA app_core')
      connection.execute('CREATE TABLE app_core.schema_migrations(version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())')
      for path in sorted(bootstrap.MIGRATIONS_DIR.glob('*.sql')):
        if path.name[:3] > '004':
          continue
        connection.execute(path.read_text(encoding='utf-8'))
        connection.execute('INSERT INTO app_core.schema_migrations(version) VALUES(%s)', (path.stem,))
      bootstrap.seed_demo_data(connection, secrets.token_hex(16))
      documentId, tenantId, actorId, baseId = connection.execute(
          "SELECT id,tenant_id,owner_id,current_version_id FROM app_core.documents WHERE status='ready' ORDER BY id LIMIT 1").fetchone()
      result = service.create_version(connection, {'id': actorId, 'tenant_id': tenantId},
                                     documentId, baseId, b'upgrade immutable text', 'upgrade_job_key_001')
      connection.execute('SET ROLE rag_version_processor')
      oldJob = service.claim_job(connection)
      connection.execute('RESET ROLE')
      connection.commit()
      before = connection.execute('SELECT source_bytes,source_sha256 FROM app_core.version_sources').fetchall()
      bootstrap.apply_migrations(connection)
      bootstrap.apply_migrations(connection)
      assert connection.execute('SELECT source_bytes,source_sha256 FROM app_core.version_sources').fetchall() == before
      assert connection.execute('SELECT attempts,lease_token::text FROM app_core.version_jobs').fetchone() == (1, oldJob['lease_token'])
      password = secrets.token_hex(24)
      bootstrap.ensure_worker_role(connection, password)
      expire(connection, result['job_id'])
      connection.commit()
      with worker.connectWorker(make_conninfo(os.environ['DATABASE_ADMIN_URL'], dbname=databaseName,
                                             user='rag_worker', password=password)) as restricted:
        assert worker.runOnce(restricted)
        available(connection, result['job_id'])
        connection.commit()
        assert worker.runOnce(restricted)
      assert connection.execute('SELECT current_version_id FROM app_core.documents WHERE id=%s', (documentId,)).fetchone() == (result['version_id'],)
      assert connection.execute('SELECT count(*) FROM app_core.version_jobs').fetchone() == (1,)
      assert connection.execute('SELECT attempts FROM app_core.version_jobs').fetchone() == (2,)
  finally:
    admin_db.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(databaseName)))


def test_bootstrap_without_worker_password_disables_new_login(admin_db, workerDsn):
  bootstrap.ensure_worker_role(admin_db, None)
  with pytest.raises(psycopg.OperationalError):
    worker.connectWorker(workerDsn)


def test_worker_rejects_missing_password_without_connecting():
  with pytest.raises(ValueError):
    worker.connectWorker('postgresql://rag_worker@127.0.0.1:1/unused')


@pytest.mark.parametrize('lockedRow', ['outgoing', 'document', 'target', 'job'])
def test_locked_expired_job_does_not_block_other_recovery_or_queued_work(
    workerDb, runtime_db, admin_db, write_document, lockedRow):
  first = create(runtime_db, write_document)
  claimJob(workerDb)
  expire(admin_db, first['job_id'])
  fixture = contextmanager(write_document_fixture)
  with fixture(admin_db) as secondDoc, fixture(admin_db) as thirdDoc:
    second = create(runtime_db, secondDoc)
    claimJob(workerDb)
    expire(admin_db, second['job_id'])
    third = create(runtime_db, thirdDoc)
    statements = {
      'outgoing': ('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE', write_document['old']),
      'document': ('SELECT id FROM app_core.documents WHERE id=%s FOR UPDATE', write_document['id']),
      'target': ('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE', first['version_id']),
      'job': ('SELECT id FROM app_core.version_jobs WHERE id=%s FOR UPDATE', first['job_id']),
    }
    with psycopg.connect(os.environ['DATABASE_ADMIN_URL']) as holder:
      statement, identifier = statements[lockedRow]
      holder.execute(statement, (identifier,))
      workerDb.execute("SET lock_timeout='200ms'")
      workerDb.commit()
      assert worker.runOnce(workerDb)
      assert pointer(admin_db, thirdDoc) == third['version_id']
      assert admin_db.execute('SELECT status FROM app_core.version_jobs WHERE id=%s', (second['job_id'],)).fetchone() == ('queued',)
      assert admin_db.execute('SELECT status FROM app_core.version_jobs WHERE id=%s', (first['job_id'],)).fetchone() == ('processing',)


# 使用原 fixture 的資料建立／清理契約，讓多文件競爭不修改 seed 文件。
write_document_fixture = write_document.__wrapped__


def test_stop_during_recovery_does_not_claim_new_work(workerDb, runtime_db, admin_db, write_document, monkeypatch):
  result = create(runtime_db, write_document)
  stop = Event()
  realRecover = service.recover_job
  def stopAfterRecovery(connection):
    recovered = realRecover(connection)
    stop.set()
    return recovered
  monkeypatch.setattr(service, 'recover_job', stopAfterRecovery)
  worker.runOnce(workerDb, stop)
  assert admin_db.execute('SELECT status,attempts FROM app_core.version_jobs WHERE id=%s',
                         (result['job_id'],)).fetchone() == ('queued', 0)
  assert pointer(admin_db, write_document) == write_document['old']
