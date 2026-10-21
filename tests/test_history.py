"""獨立歷史授權、封存邊界與交易稽核。"""
import os
from concurrent.futures import ThreadPoolExecutor
from queue import Queue

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row
from fastapi.testclient import TestClient

from app import history_service as history, worker
from tests.helpers import BASE_URL, login
from tests.test_archive import archive, queue, waitForLock
from tests.test_version_writes import write_document, processor_db, claim
from tests.test_worker import workerDb, workerDsn


@pytest.fixture
def historyDoc(write_document, runtime_db, workerDb, admin_db):
  doc = write_document
  queued = queue(runtime_db, doc, 'txt')
  assert worker.runOnce(workerDb)
  doc['current'] = queued['version_id']
  try:
    yield doc
  finally:
    for table in ('history_audit_events', 'history_readers'):
      if admin_db.execute('SELECT to_regclass(%s)', ('app_core.' + table,)).fetchone()[0]:
        admin_db.execute(f'DELETE FROM app_core.{table} WHERE version_id=%s', (doc['old'],))


def changeAccess(client, doc, reader='a.reader', granted=True, **extra):
  payload = {'reader_id': doc['users'][reader]['id'], 'granted': granted,
    'csrf_token': client.get('/api/me').json().get('csrf_token', '')}
  payload.update(extra)
  return client.put(f"/api/history/versions/{doc['old']}/access", json=payload, headers={'Origin': BASE_URL})


def readHistory(client, doc, **extra):
  payload = {'csrf_token': client.get('/api/me').json().get('csrf_token', '')}
  payload.update(extra)
  return client.post(f"/api/history/versions/{doc['old']}/read", json=payload, headers={'Origin': BASE_URL})


def test_explicit_history_acl_audit_and_revoke(client, admin_db, historyDoc):
  doc = historyDoc
  login(client, 'a.owner')
  listing = client.get('/api/history/versions')
  assert listing.status_code == 200
  row = next(v for v in listing.json() if v['version_id'] == doc['old'])
  assert row['can_manage'] and not row['can_read'] and 'content' not in row
  assert readHistory(client, doc).status_code == 404
  assert changeAccess(client, doc).status_code == 200
  client.cookies.clear()
  login(client, 'a.reader')
  assert client.get(f"/api/documents/{doc['id']}").status_code == 404
  response = readHistory(client, doc)
  assert response.status_code == 200
  assert response.json()['content'] == 'old acceptance content'
  assert response.headers['cache-control'] == 'no-store'
  assert admin_db.execute('SELECT actor_id,version_id,action FROM app_core.history_audit_events WHERE id=%s',
    (response.json()['audit_id'],)).fetchone() == (doc['users']['a.reader']['id'], doc['old'], 'read')
  client.cookies.clear()
  login(client, 'a.owner')
  assert changeAccess(client, doc, granted=False).status_code == 200
  client.cookies.clear()
  login(client, 'a.reader')
  assert readHistory(client, doc).status_code == 404


@pytest.mark.parametrize('username', ['a.reader', 'a.staff', 'a.admin', 'a.manager', 'b.owner'])
def test_history_has_no_implicit_role_or_general_acl(client, historyDoc, username):
  login(client, username)
  assert readHistory(client, historyDoc).status_code == 404
  assert changeAccess(client, historyDoc).status_code == 404
  assert client.get(f"/api/history/versions/{historyDoc['old']}/access").status_code == 404


def test_archive_blocks_every_history_entry_even_with_acl(client, runtime_db, admin_db, historyDoc):
  doc = historyDoc
  login(client, 'a.owner')
  assert changeAccess(client, doc, reader='a.owner').status_code == 200
  assert readHistory(client, doc).status_code == 200
  archive(runtime_db, doc, expected=doc['current'])
  runtime_db.commit()
  assert readHistory(client, doc).status_code == 404
  assert changeAccess(client, doc).status_code == 404
  for prefix in ('/api/history/versions', '/history/versions'):
    assert client.get(f"{prefix}/{doc['old']}/access").status_code == 404
  assert all(v['version_id'] != doc['old'] for v in client.get('/api/history/versions').json())
  assert admin_db.execute('SELECT count(*) FROM app_core.history_readers WHERE version_id=%s',
    (doc['old'],)).fetchone() == (1,)
  assert admin_db.execute("SELECT count(*) FROM app_core.history_audit_events WHERE version_id=%s AND action='read'",
    (doc['old'],)).fetchone() == (1,)


def test_history_input_and_grant_boundaries(client, admin_db, historyDoc):
  doc = historyDoc
  assert readHistory(client, doc).status_code == 401
  login(client, 'a.owner')
  assert changeAccess(client, doc, reader='b.owner').status_code == 422
  assert changeAccess(client, doc, reader_id=True).status_code == 422
  assert changeAccess(client, doc, tenant_id=doc['owner']['tenant_id']).status_code == 422
  assert readHistory(client, doc, csrf_token='bad').status_code == 403
  assert changeAccess(client, doc, csrf_token='bad').status_code == 403
  assert readHistory(client, doc, actor_id=doc['owner']['id']).status_code == 422
  current = dict(doc, old=doc['current'])
  assert changeAccess(client, current).status_code == 404
  assert readHistory(client, current).status_code == 404
  admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (doc['users']['a.reader']['id'],))
  try:
    assert changeAccess(client, doc).status_code == 422
  finally:
    admin_db.execute('UPDATE app_core.users SET is_active=true WHERE id=%s', (doc['users']['a.reader']['id'],))


def test_history_html_form_navigation_and_escaping(client, admin_db, historyDoc):
  doc = historyDoc
  login(client, 'a.owner')
  listing = client.get('/history')
  assert listing.status_code == 200 and '獨立' in listing.text
  assert 'href="/history"' in client.get('/documents').text
  accessPath = f"/history/versions/{doc['old']}/access"
  page = client.get(accessPath)
  assert page.status_code == 200 and f'action="{accessPath}"' in page.text
  response = client.post(accessPath, data={'reader_id': doc['owner']['id'], 'action': 'grant',
    'csrf_token': client.get('/api/me').json()['csrf_token']}, headers={'Origin': BASE_URL}, follow_redirects=False)
  assert response.status_code == 303 and response.headers['location'] == accessPath
  admin_db.execute('UPDATE app_core.document_versions SET content=%s WHERE id=%s', ('<script>alert(1)</script>', doc['old']))
  readPath = f"/history/versions/{doc['old']}/read"
  assert client.get(readPath).status_code == 405
  before = admin_db.execute("SELECT count(*) FROM app_core.history_audit_events WHERE version_id=%s AND action='read'",
    (doc['old'],)).fetchone()[0]
  response = client.post(readPath, data={'csrf_token': client.get('/api/me').json()['csrf_token']}, headers={'Origin': BASE_URL})
  assert response.status_code == 200
  assert '<script>alert(1)</script>' not in response.text and '&lt;script&gt;' in response.text
  assert response.headers['cache-control'] == 'no-store'
  assert admin_db.execute("SELECT count(*) FROM app_core.history_audit_events WHERE version_id=%s AND action='read'",
    (doc['old'],)).fetchone()[0] == before + 1
  auditPage = client.get(accessPath)
  assert 'a.owner' in auditPage.text and '調閱' in auditPage.text


@pytest.mark.parametrize('operation', ['revoke', 'archive', 'deactivate'])
def test_waiting_history_read_rechecks_access(runtime_db, admin_db, historyDoc, operation):
  doc = historyDoc
  reader = doc['users']['a.reader']
  history.setAccess(runtime_db, doc['owner'], doc['old'], reader['id'], True)
  runtime_db.commit()
  with psycopg.connect(os.environ['DATABASE_ADMIN_URL']) as blocker:
    if operation == 'revoke':
      history.setAccess(runtime_db, doc['owner'], doc['old'], reader['id'], False)
    elif operation == 'archive':
      archive(runtime_db, doc, expected=doc['current'])
    else:
      blocker.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (reader['id'],))
    pids = Queue()
    def attempt():
      with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
          options='-c lock_timeout=8000') as connection:
        pids.put(connection.info.backend_pid)
        try:
          history.readVersion(connection, reader, doc['old'])
        except HTTPException as error:
          connection.rollback()
          return error.status_code
        return 200
    try:
      with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(attempt)
        try:
          waitForLock(admin_db, pids.get(timeout=5))
        finally:
          runtime_db.commit()
          blocker.commit()
        assert pending.result(timeout=10) == 404
    finally:
      admin_db.execute('UPDATE app_core.users SET is_active=true WHERE id=%s', (reader['id'],))
  assert admin_db.execute("SELECT count(*) FROM app_core.history_audit_events WHERE version_id=%s AND action='read'",
    (doc['old'],)).fetchone() == (0,)


def test_history_db_capabilities_and_transaction_rollback(runtime_db, admin_db, historyDoc):
  doc = historyDoc
  history.setAccess(runtime_db, doc['owner'], doc['old'], doc['owner']['id'], True)
  runtime_db.rollback()
  assert admin_db.execute('SELECT count(*) FROM app_core.history_readers WHERE version_id=%s', (doc['old'],)).fetchone() == (0,)
  history.setAccess(runtime_db, doc['owner'], doc['old'], doc['owner']['id'], True)
  runtime_db.commit()
  history.readVersion(runtime_db, doc['owner'], doc['old'])
  runtime_db.rollback()
  assert admin_db.execute("SELECT count(*) FROM app_core.history_audit_events WHERE version_id=%s AND action='read'",
    (doc['old'],)).fetchone() == (0,)
  for role in ('rag_app', 'rag_worker', 'rag_version_processor'):
    assert admin_db.execute("SELECT has_function_privilege(%s,'app_core.read_history_version(bigint,bigint,bigint)','EXECUTE')",
      (role,)).fetchone() == (role == 'rag_app',)
    assert admin_db.execute("SELECT has_function_privilege(%s,'app_core.lock_history_version(bigint,bigint,bigint,boolean)','EXECUTE')",
      (role,)).fetchone() == (False,)
  for statement in ('SELECT * FROM app_core.history_audit_events',
      'DELETE FROM app_core.history_readers', 'UPDATE app_core.history_audit_events SET action=action',
      'DELETE FROM app_core.history_audit_events'):
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
      runtime_db.execute(statement)
    runtime_db.rollback()
  forged = {'id': doc['owner']['id'], 'tenant_id': doc['users']['b.owner']['tenant_id']}
  with pytest.raises(HTTPException) as caught:
    history.listVersions(runtime_db, forged)
  runtime_db.rollback()
  assert caught.value.status_code == 404


def test_history_owner_is_version_owner_not_current_document_owner(client, admin_db, historyDoc):
  doc = historyDoc
  admin_db.execute('UPDATE app_core.document_versions SET owner_id=%s WHERE id=%s',
    (doc['users']['a.staff']['id'], doc['old']))
  login(client, 'a.owner')
  assert changeAccess(client, doc).status_code == 404
  client.cookies.clear()
  login(client, 'a.staff')
  assert changeAccess(client, doc).status_code == 200
  assert readHistory(client, doc).status_code == 404


def test_history_preserves_current_acl_and_does_not_grant_current_access(client, admin_db, historyDoc):
  doc = historyDoc
  before = admin_db.execute('SELECT version_id,user_id FROM app_core.version_readers WHERE version_id IN (%s,%s) ORDER BY 1,2',
    (doc['old'], doc['current'])).fetchall()
  login(client, 'a.owner')
  changeAccess(client, doc)
  assert admin_db.execute('SELECT version_id,user_id FROM app_core.version_readers WHERE version_id IN (%s,%s) ORDER BY 1,2',
    (doc['old'], doc['current'])).fetchall() == before
  client.cookies.clear()
  login(client, 'a.reader')
  assert readHistory(client, doc).status_code == 200
  assert client.get(f"/api/documents/{doc['id']}").status_code == 404
  assert client.get(f"/api/documents/{doc['id']}?version_id={doc['old']}").status_code == 404
  assert client.get(f"/api/history/versions/{doc['old']}/access").status_code == 404


def test_inactive_reader_is_denied_and_can_be_revoked(client, admin_db, historyDoc):
  doc = historyDoc
  login(client, 'a.owner')
  changeAccess(client, doc)
  client.cookies.clear()
  login(client, 'a.reader')
  admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (doc['users']['a.reader']['id'],))
  try:
    assert readHistory(client, doc).status_code == 401
    client.cookies.clear()
    login(client, 'a.owner')
    assert changeAccess(client, doc, granted=False).status_code == 200
  finally:
    admin_db.execute('UPDATE app_core.users SET is_active=true WHERE id=%s', (doc['users']['a.reader']['id'],))


def test_audit_commit_failure_never_delivers_content_or_grants(client, admin_db, historyDoc):
  doc = historyDoc
  login(client, 'a.owner')
  assert changeAccess(client, doc, reader='a.owner').status_code == 200
  before = admin_db.execute('SELECT count(*) FROM app_core.history_audit_events WHERE version_id=%s', (doc['old'],)).fetchone()
  admin_db.execute("""CREATE FUNCTION app_core.reject_test_history_commit() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN RAISE EXCEPTION 'test audit unavailable'; END $$""")
  admin_db.execute('''CREATE CONSTRAINT TRIGGER test_history_commit_failure
    AFTER INSERT ON app_core.history_audit_events DEFERRABLE INITIALLY DEFERRED
    FOR EACH ROW EXECUTE FUNCTION app_core.reject_test_history_commit()''')
  safeClient = TestClient(client.app, base_url=BASE_URL, raise_server_exceptions=False)
  safeClient.cookies.update(client.cookies)
  try:
    response = readHistory(safeClient, doc)
    assert response.status_code == 500 and 'old acceptance content' not in response.text
    response = safeClient.post(f"/history/versions/{doc['old']}/read", data={
      'csrf_token': safeClient.get('/api/me').json()['csrf_token']}, headers={'Origin': BASE_URL})
    assert response.status_code == 500 and 'old acceptance content' not in response.text
    assert changeAccess(safeClient, doc).status_code == 500
    assert admin_db.execute('SELECT count(*) FROM app_core.history_audit_events WHERE version_id=%s', (doc['old'],)).fetchone() == before
    assert admin_db.execute('SELECT user_id FROM app_core.history_readers WHERE version_id=%s',
      (doc['old'],)).fetchall() == [(doc['owner']['id'],)]
  finally:
    safeClient.close()
    admin_db.execute('DROP TRIGGER test_history_commit_failure ON app_core.history_audit_events')
    admin_db.execute('DROP FUNCTION app_core.reject_test_history_commit()')


@pytest.mark.parametrize('operation', ['read', 'grant'])
def test_history_stale_outgoing_lock_returns_conflict(runtime_db, admin_db, historyDoc, operation):
  doc = historyDoc
  history.setAccess(runtime_db, doc['owner'], doc['old'], doc['owner']['id'], True)
  runtime_db.commit()
  with psycopg.connect(os.environ['DATABASE_ADMIN_URL']) as stale:
    stale.execute('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE', (doc['old'],))
    runtime_db.execute("SET LOCAL lock_timeout='1000ms'")
    with pytest.raises(HTTPException) as caught:
      if operation == 'read':
        history.readVersion(runtime_db, doc['owner'], doc['old'])
      else:
        history.setAccess(runtime_db, doc['owner'], doc['old'], doc['users']['a.reader']['id'], True)
    runtime_db.rollback()
    assert caught.value.status_code == 409
  assert admin_db.execute("SELECT count(*) FROM app_core.history_audit_events WHERE version_id=%s AND action='read'",
    (doc['old'],)).fetchone() == (0,)


def test_history_requires_completed_old_version_and_bounds(client, admin_db, historyDoc):
  doc = historyDoc
  login(client, 'a.owner')
  for status in ('queued', 'processing', 'failed', 'archived'):
    admin_db.execute('UPDATE app_core.document_versions SET status=%s WHERE id=%s', (status, doc['old']))
    assert changeAccess(client, doc).status_code == 404
    assert readHistory(client, doc).status_code == 404
  assert client.get('/api/history/versions?after_id=-1').status_code == 422
  assert client.get('/api/history/versions/9223372036854775808/access').status_code == 422
  assert client.get('/api/history/versions/0/access').status_code == 422
  assert client.get('/history/versions/99999999/access', headers={'Accept': 'text/html'}).status_code == 404


def test_history_pdf_page_positions_are_preserved(client, runtime_db, workerDb, admin_db, write_document):
  from app import version_service
  from tests.pdf_fixtures import make_pdf
  doc = write_document
  pdf = version_service.create_version(runtime_db, doc['owner'], doc['id'], doc['old'],
    make_pdf(('first historical page', 'second historical page')), 'history_pdf_source_0001', source_format='pdf')
  runtime_db.commit()
  assert worker.runOnce(workerDb)
  version_service.create_version(runtime_db, doc['owner'], doc['id'], pdf['version_id'],
    b'new current private version', 'history_pdf_replacement_0001')
  runtime_db.commit()
  assert worker.runOnce(workerDb)
  doc = dict(doc, old=pdf['version_id'])
  try:
    login(client, 'a.owner')
    assert changeAccess(client, doc, reader='a.owner').status_code == 200
    result = readHistory(client, doc).json()
    assert [page['page'] for page in result['source_pages']] == [1, 2]
    assert result['source_pages'][1]['text'].strip() == 'second historical page'
    response = client.post(f"/history/versions/{doc['old']}/read", data={
      'csrf_token': client.get('/api/me').json()['csrf_token']}, headers={'Origin': BASE_URL})
    assert response.status_code == 200 and '第 2 頁' in response.text
  finally:
    admin_db.execute('DELETE FROM app_core.history_audit_events WHERE version_id=%s', (doc['old'],))
    admin_db.execute('DELETE FROM app_core.history_readers WHERE version_id=%s', (doc['old'],))


def test_history_listing_pagination_and_metadata_only(client, admin_db, historyDoc):
  doc = historyDoc
  login(client, 'a.owner')
  admin_db.execute('''INSERT INTO app_core.document_versions
    (tenant_id,document_id,version_number,owner_id,status,content)
    SELECT %s,%s,n,%s,'ready','history pagination secret' FROM generate_series(3,104) n''',
    (doc['owner']['tenant_id'], doc['id'], doc['owner']['id']))
  first = client.get('/api/history/versions').json()
  assert len(first) == 100
  second = client.get('/api/history/versions', params={'after_id': first[-1]['version_id']}).json()
  assert second and all(row['version_id'] > first[-1]['version_id'] for row in second)
  assert len({row['version_id'] for row in first + second if row['document_id'] == doc['id']}) == 103
  assert 'history pagination secret' not in str(first + second)


def test_history_waiting_for_publication_rechecks_pointer(runtime_db, processor_db, admin_db, historyDoc):
  from app import version_service
  doc = historyDoc
  history.setAccess(runtime_db, doc['owner'], doc['old'], doc['owner']['id'], True)
  runtime_db.commit()
  queued = version_service.create_version(runtime_db, doc['owner'], doc['id'], doc['current'],
    b'latest version', 'history_racing_publish_0001')
  runtime_db.commit()
  job = claim(processor_db, queued)
  version_service.complete_job(processor_db, job['job_id'], job['lease_token'])
  pids = Queue()
  def attempt():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
        options='-c lock_timeout=8000') as connection:
      pids.put(connection.info.backend_pid)
      try:
        history.readVersion(connection, doc['owner'], doc['old'])
      except HTTPException as error:
        connection.rollback()
        return error.status_code
      return 200
  with ThreadPoolExecutor(max_workers=1) as pool:
    pending = pool.submit(attempt)
    try:
      waitForLock(admin_db, pids.get(timeout=5))
    finally:
      processor_db.commit()
    assert pending.result(timeout=10) == 409
  assert history.readVersion(runtime_db, doc['owner'], doc['old'])['content'] == 'old acceptance content'
  runtime_db.commit()


def test_history_waiting_manager_rechecks_version_owner(runtime_db, admin_db, historyDoc):
  doc = historyDoc
  pids = Queue()
  def attempt():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
        options='-c lock_timeout=8000') as connection:
      pids.put(connection.info.backend_pid)
      try:
        history.setAccess(connection, doc['owner'], doc['old'], doc['users']['a.reader']['id'], True)
      except HTTPException as error:
        connection.rollback()
        return error.status_code
      return 200
  with psycopg.connect(os.environ['DATABASE_ADMIN_URL']) as blocker:
    blocker.execute('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE', (doc['current'],))
    blocker.execute('UPDATE app_core.document_versions SET owner_id=%s WHERE id=%s',
      (doc['users']['a.staff']['id'], doc['old']))
    with ThreadPoolExecutor(max_workers=1) as pool:
      pending = pool.submit(attempt)
      try:
        waitForLock(admin_db, pids.get(timeout=5))
      finally:
        blocker.commit()
      assert pending.result(timeout=10) == 404
  assert admin_db.execute('SELECT count(*) FROM app_core.history_readers WHERE version_id=%s', (doc['old'],)).fetchone() == (0,)
