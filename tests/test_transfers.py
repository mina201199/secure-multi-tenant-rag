"""交接 HTTP 邊界、主管有限資訊與頁面驗收。"""
import pytest
from fastapi.testclient import TestClient

from tests.helpers import BASE_URL, login
from tests.test_version_writes import write_document


@pytest.fixture
def transferDoc(write_document, admin_db):
  doc = write_document
  try:
    yield doc
  finally:
    # 交接稽核與明細保留版本外鍵，先清除本測試自己的申請。
    if admin_db.execute("SELECT to_regclass('app_core.transfer_requests')").fetchone()[0]:
      admin_db.execute('DELETE FROM app_core.transfer_requests WHERE document_id=%s', (doc['id'],))


def submit(client, doc, **extra):
  payload = {'document_id': doc['id'], 'expected_version_id': doc['old'],
    'version_ids': [doc['old']], 'recipient_id': doc['users']['a.staff']['id'],
    'reason': '職務調整', 'csrf_token': client.get('/api/me').json().get('csrf_token', '')}
  payload.update(extra)
  return client.post('/api/transfers', json=payload, headers={'Origin': BASE_URL})


def decide(client, requestId, **extra):
  payload = {'approve': True, 'csrf_token': client.get('/api/me').json().get('csrf_token', '')}
  payload.update(extra)
  return client.post(f'/api/transfers/{requestId}/decision', json=payload, headers={'Origin': BASE_URL})


def test_transfer_submit_pending_does_not_change_owner(client, transferDoc, admin_db):
  doc = transferDoc
  login(client, 'a.owner')
  response = submit(client, doc)
  assert response.status_code == 200
  assert response.json()['status'] == 'pending'
  assert admin_db.execute('SELECT owner_id FROM app_core.documents WHERE id=%s',
    (doc['id'],)).fetchone() == (doc['owner']['id'],)


def test_manager_only_sees_minimum_pending_metadata(client, transferDoc, admin_db):
  doc = transferDoc
  admin_db.execute("UPDATE app_core.document_versions SET visibility='private' WHERE id=%s", (doc['old'],))
  login(client, 'a.owner')
  requestId = submit(client, doc).json()['request_id']
  own = next(row for row in client.get('/api/transfers').json() if row['request_id'] == requestId)
  assert own['version_ids'] == [doc['old']] and own['document_id'] == doc['id']
  client.cookies.clear()
  login(client, 'a.manager')
  response = client.get('/api/transfers')
  assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
  row = next(row for row in response.json() if row['request_id'] == requestId)
  assert row['can_decide'] and row['requester_username'] == 'a.owner'
  assert row['recipient_username'] == 'a.staff'
  assert not {'title', 'content', 'document_id', 'version_ids', 'source_pages'} & set(row)
  assert 'old acceptance content' not in response.text and 'TXT acceptance' not in response.text
  assert client.get(f"/api/documents/{doc['id']}").status_code == 404
  response = decide(client, requestId)
  assert response.status_code == 200 and response.json() == {'request_id': requestId, 'status': 'approved'}
  assert all(row['request_id'] != requestId for row in client.get('/api/transfers').json())
  assert decide(client, requestId).status_code in (404, 409)
  assert admin_db.execute('SELECT owner_id FROM app_core.documents WHERE id=%s',
    (doc['id'],)).fetchone() == (doc['users']['a.staff']['id'],)


@pytest.mark.parametrize('username', ['a.owner', 'a.reader', 'a.staff', 'a.admin', 'b.manager'])
def test_only_current_direct_manager_can_decide(client, transferDoc, username):
  login(client, 'a.owner')
  requestId = submit(client, transferDoc).json()['request_id']
  client.cookies.clear()
  login(client, username)
  assert decide(client, requestId).status_code == 404
  if username != 'a.owner':
    assert all(row['request_id'] != requestId for row in client.get('/api/transfers').json())


@pytest.mark.parametrize('extra', [
  {'document_id': True}, {'recipient_id': True}, {'version_ids': [True]},
  {'version_ids': []}, {'version_ids': [1] * 101}, {'reason': ' '}, {'reason': 'x' * 501},
  {'expected_version_id': -1}, {'tenant_id': 1}, {'actor_id': 1},
])
def test_create_rejects_ambiguous_or_unbounded_payload(client, transferDoc, extra):
  login(client, 'a.owner')
  assert submit(client, transferDoc, **extra).status_code == 422


def test_transfer_session_csrf_origin_and_strict_decision(client, transferDoc):
  assert submit(client, transferDoc).status_code == 401
  login(client, 'a.owner')
  assert submit(client, transferDoc, csrf_token='bad').status_code == 403
  requestId = submit(client, transferDoc).json()['request_id']
  client.cookies.clear()
  login(client, 'a.manager')
  assert decide(client, requestId, csrf_token='bad').status_code == 403
  assert decide(client, requestId, approve=1).status_code == 422
  assert decide(client, requestId, actor_id=transferDoc['owner']['id']).status_code == 422
  response = client.post(f'/api/transfers/{requestId}/decision', json={
    'approve': True, 'csrf_token': client.get('/api/me').json()['csrf_token']},
    headers={'Origin': 'https://outside.invalid'})
  assert response.status_code == 403
  assert decide(client, requestId, approve=False).json()['status'] == 'rejected'


def test_transfer_options_and_html_form_escaping(client, transferDoc, admin_db):
  doc = transferDoc
  path = f"/documents/{doc['id']}/transfer"
  assert client.get(path, follow_redirects=False).status_code == 303
  login(client, 'a.owner')
  options = client.get(f"/api/documents/{doc['id']}/transfer-options")
  assert options.status_code == 200 and options.json()['current_version_id'] == doc['old']
  recipients = {r['username'] for r in options.json()['recipients']}
  assert 'a.staff' in recipients and not {'a.owner', 'a.manager', 'a.inactive', 'b.owner'} & recipients
  page = client.get(path)
  assert page.status_code == 200 and f'action="{path}"' in page.text
  assert 'old acceptance content' not in page.text
  assert f'href="{path}"' in client.get(f"/documents/{doc['id']}/versions").text
  assert 'href="/transfers"' in client.get('/documents').text
  response = client.post(path, data={'expected_version_id': doc['old'], 'version_ids': [doc['old']],
    'recipient_id': doc['users']['a.staff']['id'], 'reason': '<script>secret</script>',
    'csrf_token': client.get('/api/me').json()['csrf_token']}, headers={'Origin': BASE_URL}, follow_redirects=False)
  assert response.status_code == 303 and response.headers['location'] == '/transfers'
  requestId = client.get('/api/transfers').json()[-1]['request_id']
  client.cookies.clear()
  login(client, 'a.manager')
  page = client.get('/transfers')
  assert page.status_code == 200 and '&lt;script&gt;secret' in page.text
  assert '<script>secret' not in page.text and 'TXT acceptance' not in page.text
  assert client.get(path).status_code == 404
  response = client.post(f'/transfers/{requestId}/decision', data={'action': 'reject',
    'csrf_token': client.get('/api/me').json()['csrf_token']}, headers={'Origin': BASE_URL}, follow_redirects=False)
  assert response.status_code == 303
  client.cookies.clear()
  login(client, 'a.owner')
  assert next(r for r in client.get('/api/transfers').json() if r['request_id'] == requestId)['status'] == 'rejected'


@pytest.mark.parametrize('surface', ['api', 'html'])
@pytest.mark.parametrize('operation', ['create', 'approve', 'reject'])
def test_commit_failure_never_returns_success_or_partial_transfer(running_app, transferDoc, admin_db, surface, operation):
  doc = transferDoc
  # session fixture 已啟動 app；第二次 lifespan 會覆寫並關閉共用連線池。
  browser = TestClient(running_app, base_url=BASE_URL, raise_server_exceptions=False)
  try:
    login(browser, 'a.owner')
    requestId = None
    if operation != 'create':
      requestId = submit(browser, doc).json()['request_id']
      browser.cookies.clear()
      login(browser, 'a.manager')
    admin_db.execute('''CREATE FUNCTION app_core.fail_transfer_audit_test() RETURNS trigger
      LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'audit commit failed'; END $$''')
    admin_db.execute('''CREATE CONSTRAINT TRIGGER fail_transfer_audit_test
      AFTER INSERT ON app_core.transfer_audit_events DEFERRABLE INITIALLY DEFERRED
      FOR EACH ROW EXECUTE FUNCTION app_core.fail_transfer_audit_test()''')
    try:
      if surface == 'api':
        response = submit(browser, doc) if operation == 'create' else decide(browser, requestId, approve=operation == 'approve')
      else:
        payload = {'csrf_token': browser.get('/api/me').json()['csrf_token']}
        if operation == 'create':
          path = f"/documents/{doc['id']}/transfer"
          payload.update(expected_version_id=doc['old'], version_ids=[doc['old']],
            recipient_id=doc['users']['a.staff']['id'], reason='職務調整')
        else:
          path = f'/transfers/{requestId}/decision'
          payload['action'] = operation
        response = browser.post(path, data=payload, headers={'Origin': BASE_URL}, follow_redirects=False)
      assert response.status_code == 500
      assert admin_db.execute('SELECT owner_id FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone() == (doc['owner']['id'],)
      assert admin_db.execute('SELECT owner_id FROM app_core.document_versions WHERE id=%s', (doc['old'],)).fetchone() == (doc['owner']['id'],)
      if operation == 'create':
        assert admin_db.execute('SELECT count(*) FROM app_core.transfer_requests WHERE document_id=%s', (doc['id'],)).fetchone() == (0,)
      else:
        assert admin_db.execute('SELECT status FROM app_core.transfer_requests WHERE id=%s', (requestId,)).fetchone() == ('pending',)
        assert admin_db.execute('SELECT count(*) FROM app_core.transfer_audit_events WHERE request_id=%s', (requestId,)).fetchone() == (1,)
    finally:
      admin_db.execute('DROP TRIGGER fail_transfer_audit_test ON app_core.transfer_audit_events')
      admin_db.execute('DROP FUNCTION app_core.fail_transfer_audit_test()')
  finally:
    browser.close()
