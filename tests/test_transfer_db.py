"""交接資料庫能力的真實 PostgreSQL 驗證。"""
import os
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row

from app import transfer_service as service


@pytest.fixture
def transferDocument(admin_db):
  users = {r[0]: {'id': r[1], 'tenant_id': r[2]} for r in admin_db.execute('SELECT username,id,tenant_id FROM app_core.users')}
  owner = users['a.owner']
  document = admin_db.execute("INSERT INTO app_core.documents(tenant_id,owner_id,title,category) VALUES(%s,%s,%s,'transfer') RETURNING id",
    (owner['tenant_id'], owner['id'], 'transfer ' + uuid.uuid4().hex)).fetchone()[0]
  versions = [admin_db.execute("INSERT INTO app_core.document_versions(tenant_id,document_id,version_number,owner_id,status,content) VALUES(%s,%s,%s,%s,'ready','secret') RETURNING id",
    (owner['tenant_id'], document, number, owner['id'])).fetchone()[0] for number in range(1, 4)]
  admin_db.execute('UPDATE app_core.documents SET current_version_id=%s WHERE id=%s', (versions[-1], document))
  doc = {'id': document, 'versions': versions, 'current': versions[-1], 'owner': owner, 'users': users}
  try:
    yield doc
  finally:
    admin_db.execute('DELETE FROM app_core.transfer_requests WHERE document_id=%s', (document,))
    admin_db.execute('UPDATE app_core.documents SET current_version_id=NULL WHERE id=%s', (document,))
    admin_db.execute('DELETE FROM app_core.history_readers WHERE version_id=ANY(%s)', (versions,))
    admin_db.execute('DELETE FROM app_core.version_readers WHERE version_id=ANY(%s)', (versions,))
    admin_db.execute('DELETE FROM app_core.document_versions WHERE document_id=%s', (document,))
    admin_db.execute('DELETE FROM app_core.documents WHERE id=%s', (document,))


def create(connection, doc, **changes):
  fields = {'expectedVersionId': doc['current'], 'versionIds': [doc['current']],
    'recipientId': doc['users']['a.reader']['id'], 'reason': ' handover '}
  fields.update(changes)
  result = service.createTransfer(connection, doc['owner'], doc['id'], **fields)
  connection.commit()
  return result


def decide(connection, doc, request, approve=True):
  result = service.decideTransfer(connection, doc['users']['a.manager'], request['request_id'], approve)
  connection.commit()
  return result


def fails(connection, status, call):
  with pytest.raises(HTTPException) as error:
    call()
  connection.rollback()
  assert error.value.status_code == status


def test_transfer_capability_installed(admin_db):
  assert admin_db.execute("SELECT to_regprocedure('app_core.create_transfer(bigint,bigint,bigint,bigint,bigint[],bigint,text)')").fetchone()[0] is not None


def test_atomic_explicit_scope_acl_and_minimal_manager(runtime_db, admin_db, transferDocument):
  doc = transferDocument
  options = service.getOptions(runtime_db, doc['owner'], doc['id'])
  assert set(options) == {'document_id', 'title', 'current_version_id', 'versions', 'recipients'}
  assert len(options['versions']) == 3
  admin_db.execute('INSERT INTO app_core.version_readers VALUES(%s,%s,%s)', (doc['owner']['tenant_id'], doc['current'], doc['users']['a.staff']['id']))
  admin_db.execute('INSERT INTO app_core.version_readers VALUES(%s,%s,%s)', (doc['owner']['tenant_id'], doc['current'], doc['owner']['id']))
  admin_db.execute('INSERT INTO app_core.history_readers(tenant_id,version_id,user_id,granted_by) VALUES(%s,%s,%s,%s)', (doc['owner']['tenant_id'], doc['versions'][0], doc['users']['a.staff']['id'], doc['owner']['id']))
  acl_before = admin_db.execute('SELECT * FROM app_core.history_readers WHERE version_id=ANY(%s) ORDER BY version_id,user_id', (doc['versions'],)).fetchall()
  selected = [doc['versions'][0], doc['current']]
  request = create(runtime_db, doc, versionIds=selected)
  listing = service.listTransfers(runtime_db, doc['users']['a.manager'])
  row = next(r for r in listing if r['request_id'] == request['request_id'])
  assert set(row) == {'request_id','status','requester_id','requester_username','recipient_id','recipient_username','reason','created_at','expires_at','can_decide'}
  assert row['reason'] == 'handover' and row['can_decide']
  assert service.listTransfers(runtime_db, doc['users']['a.reader']) == []
  assert decide(runtime_db, doc, request) == {'request_id': request['request_id'], 'status': 'approved'}
  assert admin_db.execute('SELECT owner_id FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone()[0] == doc['users']['a.reader']['id']
  assert admin_db.execute('SELECT id,owner_id FROM app_core.document_versions WHERE document_id=%s ORDER BY id', (doc['id'],)).fetchall() == [(v, doc['users']['a.reader']['id'] if v in selected else doc['owner']['id']) for v in doc['versions']]
  assert admin_db.execute('SELECT user_id FROM app_core.version_readers WHERE version_id=%s ORDER BY user_id', (doc['current'],)).fetchall() == sorted([(doc['users']['a.staff']['id'],), (doc['owner']['id'],)])
  assert admin_db.execute('SELECT * FROM app_core.history_readers WHERE version_id=ANY(%s) ORDER BY version_id,user_id', (doc['versions'],)).fetchall() == acl_before
  fails(runtime_db, 404, lambda: service.getOptions(runtime_db, doc['owner'], doc['id']))
  assert all(r['request_id'] != request['request_id'] for r in service.listTransfers(runtime_db, doc['users']['a.manager']))


@pytest.mark.parametrize('changes', [{'versionIds': []}, {'versionIds': [0]}, {'versionIds': [None]}, {'reason': ' '}, {'reason': 'x'*501}, {'versionIds': [1]*101}])
def test_invalid_db_inputs(runtime_db, transferDocument, changes):
  fails(runtime_db, 422, lambda: create(runtime_db, transferDocument, **changes))


@pytest.mark.parametrize('username', ['a.owner','a.manager','b.owner'])
def test_invalid_recipient(runtime_db, transferDocument, username):
  doc = transferDocument
  fails(runtime_db, 422, lambda: create(runtime_db, doc, recipientId=doc['users'][username]['id']))


@pytest.mark.parametrize('username', ['a.reader','a.staff','a.admin','a.manager','b.owner'])
def test_nonowner_unavailable(runtime_db, transferDocument, username):
  doc = transferDocument
  fails(runtime_db, 404, lambda: service.getOptions(runtime_db, doc['users'][username], doc['id']))


@pytest.mark.parametrize('username', ['a.owner','a.manager','a.reader'])
def test_deactivation_reactivation_stales_request(runtime_db, admin_db, transferDocument, username):
  doc = transferDocument
  request = create(runtime_db, doc)
  user = doc['users'][username]['id']
  admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (user,))
  admin_db.execute('UPDATE app_core.users SET is_active=true WHERE id=%s', (user,))
  fails(runtime_db, 409, lambda: decide(runtime_db, doc, request))
  assert admin_db.execute('SELECT owner_id FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone()[0] == doc['owner']['id']


@pytest.mark.parametrize('target', ['document','version','manager'])
def test_aba_stales_request(runtime_db, admin_db, transferDocument, target):
  doc = transferDocument
  request = create(runtime_db, doc)
  if target == 'manager':
    admin_db.execute('UPDATE app_core.users SET manager_id=%s WHERE id=%s', (doc['users']['a.admin']['id'],doc['owner']['id']))
    admin_db.execute('UPDATE app_core.users SET manager_id=%s WHERE id=%s', (doc['users']['a.manager']['id'],doc['owner']['id']))
  else:
    table, identity = ('documents',doc['id']) if target == 'document' else ('document_versions',doc['current'])
    for owner in (doc['users']['a.staff']['id'], doc['owner']['id']):
      admin_db.execute(f'UPDATE app_core.{table} SET owner_id=%s WHERE id=%s', (owner, identity))
  fails(runtime_db, 409, lambda: decide(runtime_db, doc, request))


@pytest.mark.parametrize('change', ['expired','archived','pending','pointer','historical_owner'])
def test_revalidate_scope(runtime_db, admin_db, transferDocument, change):
  doc = transferDocument
  request = create(runtime_db, doc, versionIds=doc['versions'])
  if change == 'expired':
    admin_db.execute("UPDATE app_core.transfer_requests SET expires_at=clock_timestamp()-interval '1 second' WHERE id=%s", (request['request_id'],))
  elif change == 'archived':
    admin_db.execute("UPDATE app_core.documents SET status='archived' WHERE id=%s", (doc['id'],))
  elif change == 'pending':
    admin_db.execute("UPDATE app_core.document_versions SET status='queued' WHERE id=%s", (doc['versions'][0],))
  elif change == 'pointer':
    admin_db.execute('UPDATE app_core.documents SET current_version_id=%s WHERE id=%s', (doc['versions'][0],doc['id']))
  else:
    admin_db.execute('UPDATE app_core.document_versions SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'],doc['versions'][0]))
  fails(runtime_db, 409, lambda: decide(runtime_db, doc, request))


def test_history_lock_conflict(runtime_db, admin_db, transferDocument):
  doc = transferDocument
  request = create(runtime_db, doc, versionIds=doc['versions'])
  with psycopg.connect(os.environ['DATABASE_ADMIN_URL']) as locked:
    locked.execute('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE', (doc['versions'][0],))
    fails(runtime_db, 409, lambda: decide(runtime_db, doc, request))


def test_duplicate_and_parallel_decisions(runtime_db, admin_db, transferDocument):
  doc = transferDocument
  request = create(runtime_db, doc)
  def attempt():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as connection:
      try:
        return decide(connection, doc, request)['status']
      except HTTPException as error:
        return error.status_code
  with ThreadPoolExecutor(max_workers=2) as pool:
    assert sorted(pool.map(lambda _: attempt(), range(2)), key=str) == [409, 'approved']
  assert admin_db.execute("SELECT count(*) FROM app_core.transfer_audit_events WHERE request_id=%s AND action='approved'", (request['request_id'],)).fetchone()[0] == 1


def test_runtime_and_worker_permissions(runtime_db, admin_db):
  for table in ('transfer_requests','transfer_request_versions','transfer_audit_events'):
    assert not admin_db.execute("SELECT has_table_privilege('rag_app',%s,'SELECT')", ('app_core.'+table,)).fetchone()[0]
  assert not admin_db.execute("SELECT has_function_privilege('rag_worker','app_core.list_transfers(bigint,bigint,bigint)','EXECUTE')").fetchone()[0]

@pytest.mark.parametrize('username', ['a.owner','a.manager','a.reader'])
def test_approval_rechecks_accounts_after_wait(admin_db, runtime_db, transferDocument, username):
  import time
  from queue import Queue
  doc = transferDocument
  request = create(runtime_db, doc)
  pids = Queue()
  def approval():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as connection:
      pids.put(connection.info.backend_pid)
      try:
        return decide(connection, doc, request)
      except HTTPException as error:
        return error.status_code
  with psycopg.connect(os.environ['DATABASE_ADMIN_URL']) as lock:
    lock.execute('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE', (doc['current'],))
    with ThreadPoolExecutor(max_workers=1) as pool:
      future = pool.submit(approval)
      pid = pids.get(timeout=5)
      deadline = time.monotonic() + 5
      while time.monotonic() < deadline:
        if admin_db.execute("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone() == (True,):
          break
        time.sleep(0.02)
      else:
        lock.rollback()
        pytest.fail('交接未等待 outgoing 鎖')
      user = doc['users'][username]['id']
      admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (user,))
      admin_db.execute('UPDATE app_core.users SET is_active=true WHERE id=%s', (user,))
      lock.commit()
      assert future.result(timeout=5) == 409


def test_mid_scope_update_failure_rolls_back_every_owner(runtime_db, admin_db, transferDocument):
  doc = transferDocument
  request = create(runtime_db, doc, versionIds=doc['versions'])
  admin_db.execute("""CREATE FUNCTION app_core.test_transfer_fail() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN IF NEW.owner_id<>OLD.owner_id AND NEW.id=%s THEN RAISE EXCEPTION 'injected owner failure'; END IF; RETURN NEW; END $$""" % doc['current'])
  admin_db.execute('CREATE TRIGGER test_transfer_fail BEFORE UPDATE ON app_core.document_versions FOR EACH ROW EXECUTE FUNCTION app_core.test_transfer_fail()')
  try:
    with pytest.raises(psycopg.Error):
      decide(runtime_db, doc, request)
    runtime_db.rollback()
    assert admin_db.execute('SELECT owner_id FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone() == (doc['owner']['id'],)
    assert all(r[0] == doc['owner']['id'] for r in admin_db.execute('SELECT owner_id FROM app_core.document_versions WHERE document_id=%s', (doc['id'],)))
    assert admin_db.execute('SELECT status FROM app_core.transfer_requests WHERE id=%s', (request['request_id'],)).fetchone() == ('pending',)
    assert admin_db.execute("SELECT count(*) FROM app_core.transfer_audit_events WHERE request_id=%s AND action='approved'", (request['request_id'],)).fetchone() == (0,)
  finally:
    admin_db.execute('DROP TRIGGER test_transfer_fail ON app_core.document_versions')
    admin_db.execute('DROP FUNCTION app_core.test_transfer_fail()')


def test_selected_versions_must_include_current_and_belong_to_document(runtime_db, admin_db, transferDocument):
  doc = transferDocument
  fails(runtime_db, 422, lambda: create(runtime_db, doc, versionIds=[doc['versions'][0]]))
  fails(runtime_db, 422, lambda: create(runtime_db, doc, versionIds=[doc['current'],doc['current']]))
  foreign = admin_db.execute('SELECT id FROM app_core.document_versions WHERE document_id<>%s LIMIT 1', (doc['id'],)).fetchone()[0]
  fails(runtime_db, 422, lambda: create(runtime_db, doc, versionIds=[doc['current'],foreign]))


def test_queued_version_creation_serializes_with_transfer(runtime_db, admin_db, transferDocument):
  from app import version_service
  doc = transferDocument
  request = create(runtime_db, doc)
  queued = version_service.create_version(runtime_db, doc['owner'], doc['id'], doc['current'], b'new body', 'transfer_race_000001')
  runtime_db.commit()
  try:
    fails(runtime_db, 409, lambda: decide(runtime_db, doc, request))
    fails(runtime_db, 409, lambda: create(runtime_db, doc))
  finally:
    admin_db.execute('DELETE FROM app_core.version_jobs WHERE document_id=%s', (doc['id'],))
    admin_db.execute('DELETE FROM app_core.version_sources WHERE document_id=%s', (doc['id'],))
    admin_db.execute('DELETE FROM app_core.document_versions WHERE id=%s', (queued['version_id'],))

@pytest.mark.parametrize('change', ['archive','publish','manager','document_owner','history_owner'])
def test_approval_rechecks_scope_after_outgoing_wait(admin_db, runtime_db, transferDocument, change):
  import time
  from queue import Queue
  doc = transferDocument
  request = create(runtime_db, doc, versionIds=doc['versions'])
  pids = Queue()
  def approval():
    with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as connection:
      pids.put(connection.info.backend_pid)
      try:
        return decide(connection, doc, request)
      except HTTPException as error:
        return error.status_code
  try:
    with psycopg.connect(os.environ['DATABASE_ADMIN_URL']) as lock:
      lock.execute('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE', (doc['current'],))
      with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(approval)
        pid = pids.get(timeout=5)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
          if admin_db.execute("SELECT wait_event_type='Lock' FROM pg_stat_activity WHERE pid=%s", (pid,)).fetchone() == (True,):
            break
          time.sleep(0.02)
        else:
          lock.rollback()
          pytest.fail('交接未等待 outgoing 鎖')
        if change == 'archive':
          admin_db.execute("UPDATE app_core.documents SET status='archived' WHERE id=%s", (doc['id'],))
        elif change == 'publish':
          admin_db.execute('UPDATE app_core.documents SET current_version_id=%s WHERE id=%s', (doc['versions'][0],doc['id']))
        elif change == 'manager':
          admin_db.execute('UPDATE app_core.users SET manager_id=%s WHERE id=%s', (doc['users']['a.admin']['id'],doc['owner']['id']))
        elif change == 'document_owner':
          admin_db.execute('UPDATE app_core.documents SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'],doc['id']))
        else:
          admin_db.execute('UPDATE app_core.document_versions SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'],doc['versions'][0]))
        lock.commit()
        assert future.result(timeout=5) == 409
  finally:
    if change == 'manager':
      admin_db.execute('UPDATE app_core.users SET manager_id=%s WHERE id=%s', (doc['users']['a.manager']['id'],doc['owner']['id']))
