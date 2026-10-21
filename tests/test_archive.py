"""封存的真實 DB、HTTP 與 worker 交易驗收。"""
import os
from queue import Queue
import time
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row

from app import version_service as service, worker
from tests.helpers import BASE_URL, login
from tests.pdf_fixtures import make_pdf
from tests.test_version_writes import write_document, processor_db, claim, pointer
from tests.test_worker import workerDsn, workerDb


def archive(connection, doc, identity=None, expected=None):
  who = identity or doc['owner']
  return connection.execute('SELECT app_core.archive_document(%s,%s,%s,%s) AS result',
    (who['tenant_id'], who['id'], doc['id'], doc['old'] if expected is None else expected)).fetchone()['result']


def postArchive(client, doc, **changes):
  payload = {'expected_current_version_id': doc['old'],
    'csrf_token': client.get('/api/me').json().get('csrf_token', '')}
  payload.update(changes)
  return client.post(f"/api/documents/{doc['id']}/archive", json=payload, headers={'Origin': BASE_URL})


def queue(connection, doc, kind):
  job = service.create_version(connection, doc['owner'], doc['id'], doc['old'],
    make_pdf(('archive evidence',)) if kind == 'pdf' else b'archive evidence',
    'archive_acceptance_0001', source_format=kind)
  connection.commit()
  return job


def test_archive_http_hides_all_existing_entry_points_and_retains_data(client, admin_db, write_document):
  doc = write_document
  assert login(client, 'a.owner').status_code == 303
  assert client.get(f"/documents/{doc['id']}/archive").status_code == 200
  response = postArchive(client, doc)
  assert response.status_code == 200
  assert response.json() == {'document_id': doc['id'], 'status': 'archived'}
  assert postArchive(client, doc).json() == response.json()
  for username in ('a.owner', 'a.reader', 'a.admin', 'b.owner'):
    client.cookies.clear()
    assert login(client, username).status_code == 303
    assert doc['id'] not in [d['id'] for d in client.get('/api/documents').json()]
    for path in (f"/api/documents/{doc['id']}", f"/documents/{doc['id']}",
        f"/documents/{doc['id']}/versions", f"/documents/{doc['id']}/sharing",
        f"/documents/{doc['id']}/archive", f"/documents/{doc['id']}/download",
        f"/api/documents/{doc['id']}/download"):
      assert client.get(path).status_code == 404
  assert admin_db.execute('SELECT status,current_version_id,archived_by,archived_at IS NOT NULL FROM app_core.documents WHERE id=%s',
    (doc['id'],)).fetchone() == ('archived', doc['old'], doc['owner']['id'], True)
  assert admin_db.execute('SELECT content,status FROM app_core.document_versions WHERE id=%s',
    (doc['old'],)).fetchone() == ('old acceptance content', 'ready')
  assert admin_db.execute('SELECT count(*) FROM app_core.version_readers WHERE version_id=%s',
    (doc['old'],)).fetchone() == (1,)


@pytest.mark.parametrize('kind', ['txt', 'pdf'])
@pytest.mark.parametrize('claimed', [False, True])
def test_archive_cancels_pending_jobs_and_invalidates_worker_tokens(
    runtime_db, workerDb, admin_db, write_document, kind, claimed):
  doc = write_document
  queued = queue(runtime_db, doc, kind)
  job = service.claim_job(workerDb) if claimed else None
  workerDb.commit()
  before = admin_db.execute('SELECT source_bytes,source_sha256 FROM app_core.version_sources WHERE version_id=%s',
    (queued['version_id'],)).fetchone()
  archive(runtime_db, doc)
  runtime_db.commit()
  assert not worker.runOnce(workerDb)
  assert admin_db.execute('SELECT status,error_code,lease_token,lease_expires_at FROM app_core.version_jobs WHERE id=%s',
    (queued['job_id'],)).fetchone() == ('failed', 'archived', None, None)
  assert admin_db.execute('SELECT status FROM app_core.document_versions WHERE id=%s',
    (queued['version_id'],)).fetchone() == ('failed',)
  if claimed:
    with pytest.raises(HTTPException) as caught:
      if kind == 'txt':
        service.complete_job(workerDb, job['job_id'], job['lease_token'])
      else:
        service.complete_pdf_job(workerDb, job['job_id'], job['lease_token'], job['source_sha256'],
          [{'page': 1, 'text': 'archive evidence'}])
    workerDb.rollback()
    assert caught.value.status_code == 409
  assert pointer(admin_db, doc) == doc['old']
  assert admin_db.execute('SELECT source_bytes,source_sha256 FROM app_core.version_sources WHERE version_id=%s',
    (queued['version_id'],)).fetchone() == before
  with pytest.raises(HTTPException) as caught:
    queue(runtime_db, doc, kind)
  runtime_db.rollback()
  assert caught.value.status_code == 404


def test_archive_preserves_published_pdf_pages(runtime_db, workerDb, admin_db, write_document):
  doc = write_document
  queued = queue(runtime_db, doc, 'pdf')
  assert worker.runOnce(workerDb)
  before = admin_db.execute('SELECT content,source_pages,parser_version FROM app_core.document_versions WHERE id=%s',
    (queued['version_id'],)).fetchone()
  archive(runtime_db, doc, expected=queued['version_id'])
  runtime_db.commit()
  assert admin_db.execute('SELECT content,source_pages,parser_version FROM app_core.document_versions WHERE id=%s',
    (queued['version_id'],)).fetchone() == before
  assert pointer(admin_db, doc) == queued['version_id']


@pytest.mark.parametrize('username', ['a.reader', 'a.staff', 'a.admin', 'a.manager', 'b.owner'])
def test_archive_requires_current_document_owner(client, write_document, username):
  login(client, username)
  assert postArchive(client, write_document).status_code == 404
  assert client.get(f"/documents/{write_document['id']}/archive").status_code == 404


def test_archive_csrf_auth_and_strict_payload(client, write_document):
  assert postArchive(client, write_document).status_code == 401
  login(client, 'a.owner')
  assert postArchive(client, write_document, csrf_token='bad').status_code == 403
  assert postArchive(client, write_document, tenant_id=99).status_code == 422
  assert postArchive(client, write_document, expected_current_version_id=True).status_code == 422
  assert postArchive(client, write_document, expected_current_version_id=9223372036854775808).status_code == 422
  assert postArchive(client, write_document, expected_current_version_id=999999999).status_code == 409


def test_archive_form_redirects_and_stale_sharing_fails(client, write_document):
  doc = write_document
  login(client, 'a.owner')
  token = client.get('/api/me').json()['csrf_token']
  page = client.get(f"/documents/{doc['id']}/archive")
  assert '封存' in page.text and '保留' in page.text
  assert f'action="/documents/{doc["id"]}/archive"' in page.text
  response = client.post(f"/documents/{doc['id']}/archive", data={
    'expected_current_version_id': doc['old'], 'csrf_token': token},
    headers={'Origin': BASE_URL}, follow_redirects=False)
  assert response.status_code == 303 and response.headers['location'] == '/documents'
  assert client.put(f"/api/documents/{doc['id']}/sharing", json={
    'version_id': doc['old'], 'mode': 'company', 'csrf_token': token},
    headers={'Origin': BASE_URL}).status_code == 404


def test_archive_rollback_and_db_capabilities(runtime_db, processor_db, admin_db, write_document):
  doc = write_document
  archive(runtime_db, doc)
  runtime_db.rollback()
  assert admin_db.execute('SELECT status,archived_at FROM app_core.documents WHERE id=%s',
    (doc['id'],)).fetchone() == ('ready', None)
  for role in ('rag_app', 'rag_worker', 'rag_version_processor'):
    assert admin_db.execute("SELECT has_function_privilege(%s,'app_core.archive_document(bigint,bigint,bigint,bigint)','EXECUTE')",
      (role,)).fetchone() == (role == 'rag_app',)
  for statement in ("UPDATE app_core.documents SET status='ready' WHERE id=%s",
      'SELECT source_bytes FROM app_core.version_sources WHERE document_id=%s'):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
      runtime_db.execute(statement, (doc['id'],))
    runtime_db.rollback()
  for username in ('a.admin', 'b.owner'):
    with pytest.raises(psycopg.errors.NoDataFound):
      archive(runtime_db, doc, doc['users'][username])
    runtime_db.rollback()
  admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (doc['owner']['id'],))
  with pytest.raises(psycopg.errors.NoDataFound):
    archive(runtime_db, doc)
  runtime_db.rollback()


def waitForLock(adminDb, pid):
  deadline = time.monotonic() + 5
  while time.monotonic() < deadline:
    row = adminDb.execute('SELECT wait_event_type FROM pg_stat_activity WHERE pid=%s', (pid,)).fetchone()
    if row and row[0] == 'Lock':
      return
    time.sleep(0.02)
  pytest.fail('concurrent request did not reach database lock')


@pytest.mark.parametrize('operation', ['publish', 'share', 'create'])
def test_archive_commit_blocks_waiting_mutations(runtime_db, workerDb, admin_db, write_document, operation):
  from app import repository
  doc = write_document
  queued = queue(runtime_db, doc, 'txt') if operation == 'publish' else None
  job = service.claim_job(workerDb) if queued else None
  workerDb.commit()
  archive(runtime_db, doc)
  pids = Queue()
  def attempt():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
        options='-c lock_timeout=8000') as connection:
      if operation == 'publish':
        # 真實 processor 權限另用已有 worker 連線；不能讓 web 冒充發布角色。
        connection = workerDb
      pids.put(connection.info.backend_pid)
      try:
        if operation == 'publish':
          service.complete_job(connection, job['job_id'], job['lease_token'])
        elif operation == 'share':
          repository.replace_sharing(connection, doc['owner'], doc['id'], doc['old'], 'company', [])
        else:
          service.create_version(connection, doc['owner'], doc['id'], doc['old'], b'late source', 'archive_race_0001')
        connection.commit()
      except HTTPException as error:
        connection.rollback()
        return error.status_code
  with ThreadPoolExecutor(max_workers=1) as pool:
    pending = pool.submit(attempt)
    try:
      waitForLock(admin_db, pids.get(timeout=5))
    finally:
      runtime_db.commit()
    assert pending.result(timeout=10) == (409 if operation == 'publish' else 404)
  assert pointer(admin_db, doc) == doc['old']
  assert admin_db.execute('SELECT status FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone() == ('archived',)


def test_archive_repeat_keeps_first_audit_record(runtime_db, admin_db, write_document):
  doc = write_document
  archive(runtime_db, doc)
  runtime_db.commit()
  first = admin_db.execute('SELECT archived_at,archived_by FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone()
  archive(runtime_db, doc)
  runtime_db.commit()
  assert admin_db.execute('SELECT archived_at,archived_by FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone() == first


def test_document_owner_can_archive_without_current_version_read_permission(client, admin_db, write_document):
  doc = write_document
  admin_db.execute("UPDATE app_core.document_versions SET owner_id=%s,visibility='private' WHERE id=%s",
    (doc['users']['a.staff']['id'], doc['old']))
  login(client, 'a.owner')
  assert client.get(f"/api/documents/{doc['id']}").status_code == 404
  assert client.get(f"/documents/{doc['id']}/archive").status_code == 200
  assert postArchive(client, doc).status_code == 200


def test_pdf_archive_during_extraction_cannot_publish(runtime_db, workerDb, admin_db, write_document, monkeypatch):
  from app import pdf_parser
  doc = write_document
  queue(runtime_db, doc, 'pdf')
  original = pdf_parser.parsePdf
  def parseAndArchive(source):
    result = original(source)
    archive(runtime_db, doc)
    runtime_db.commit()
    return result
  monkeypatch.setattr(pdf_parser, 'parsePdf', parseAndArchive)
  with pytest.raises(HTTPException) as caught:
    worker.runOnce(workerDb)
  workerDb.rollback()
  assert caught.value.status_code == 409
  assert pointer(admin_db, doc) == doc['old']
  assert not worker.runOnce(workerDb)


def test_archive_waiting_for_publication_rechecks_current_version(runtime_db, processor_db, admin_db, write_document):
  doc = write_document
  queued = queue(runtime_db, doc, 'txt')
  job = claim(processor_db, queued)
  service.complete_job(processor_db, job['job_id'], job['lease_token'])
  pids = Queue()
  def attempt():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
        options='-c lock_timeout=5000') as connection:
      pids.put(connection.info.backend_pid)
      try:
        archive(connection, doc)
      except psycopg.errors.SerializationFailure:
        connection.rollback()
        return 'conflict'
      return 'archived'
  # 先持有 outgoing lock；完成交易後封存必須重驗 expected base。
  with ThreadPoolExecutor(max_workers=1) as pool:
    pending = pool.submit(attempt)
    try:
      waitForLock(admin_db, pids.get(timeout=5))
    finally:
      processor_db.commit()
    assert pending.result(timeout=10) == 'conflict'
  assert admin_db.execute('SELECT status,current_version_id FROM app_core.documents WHERE id=%s',
    (doc['id'],)).fetchone() == ('ready', queued['version_id'])


def test_archive_does_not_wait_on_stale_outgoing_version(runtime_db, workerDb, admin_db, write_document):
  doc = write_document
  published = queue(runtime_db, doc, 'txt')
  assert worker.runOnce(workerDb)
  # 模擬發布後，舊請求已取得先前 outgoing 鎖、尚未完成文件重驗的狀態。
  with psycopg.connect(os.environ['DATABASE_ADMIN_URL']) as staleConnection:
    staleConnection.execute('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE',
      (doc['old'],))
    runtime_db.execute("SET LOCAL lock_timeout = '1000ms'")
    result = archive(runtime_db, doc, expected=published['version_id'])
    runtime_db.commit()
    assert result == {'document_id': doc['id'], 'status': 'archived'}
  assert admin_db.execute('SELECT status,current_version_id FROM app_core.documents WHERE id=%s',
    (doc['id'],)).fetchone() == ('archived', published['version_id'])
  assert admin_db.execute('SELECT content FROM app_core.document_versions WHERE id=%s',
    (doc['old'],)).fetchone() == ('old acceptance content',)
