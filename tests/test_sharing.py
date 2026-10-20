"""Sharing tests catch owner bypass, stale-version writes and ACL revocation bugs."""
import pytest

from tests.helpers import BASE_URL, login


@pytest.fixture
def sharing_document(admin_db):
    tenant_id, owner_id = admin_db.execute(
        "SELECT tenant_id, id FROM app_core.users WHERE username = 'a.owner'"
    ).fetchone()
    document_id = admin_db.execute(
        """INSERT INTO app_core.documents (tenant_id, title, category, owner_id, status)
           VALUES (%s, '分享測試', '測試', %s, 'ready') RETURNING id""", (tenant_id, owner_id)
    ).fetchone()[0]
    version_id = admin_db.execute(
        """INSERT INTO app_core.document_versions
           (tenant_id, document_id, version_number, owner_id, status, content)
           VALUES (%s, %s, 1, %s, 'ready', '分享測試內容') RETURNING id""",
        (tenant_id, document_id, owner_id),
    ).fetchone()[0]
    admin_db.execute("UPDATE app_core.documents SET current_version_id = %s WHERE id = %s", (version_id, document_id))
    users = dict(admin_db.execute("SELECT username, id FROM app_core.users"))
    yield {"id": document_id, "version_id": version_id, "users": users}
    admin_db.execute("DELETE FROM app_core.version_readers WHERE version_id = %s", (version_id,))
    admin_db.execute("UPDATE app_core.documents SET current_version_id = NULL WHERE id = %s", (document_id,))
    admin_db.execute("DELETE FROM app_core.document_versions WHERE document_id = %s", (document_id,))
    admin_db.execute("DELETE FROM app_core.documents WHERE id = %s", (document_id,))


def change(client, document, mode, readers=(), **overrides):
    payload = {"version_id": document["version_id"], "mode": mode, "reader_ids": list(readers),
               "csrf_token": client.get('/api/me').json()['csrf_token']}
    payload.update(overrides)
    return client.put(f"/api/documents/{document['id']}/sharing", json=payload, headers={"Origin": BASE_URL})


def test_owner_can_share_then_revoke_for_existing_reader_session(client, sharing_document):
    doc = sharing_document
    login(client, 'a.owner')
    assert change(client, doc, 'specified', [doc['users']['a.reader']]).status_code == 200
    client.cookies.clear()
    login(client, 'a.reader')
    reader_cookie = client.cookies.get('rag_session')
    assert client.get(f"/api/documents/{doc['id']}").status_code == 200
    client.cookies.clear()
    login(client, 'a.owner')
    assert change(client, doc, 'private').status_code == 200
    client.cookies.clear()
    client.cookies.set('rag_session', reader_cookie)
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
    assert doc['id'] not in {d['id'] for d in client.get('/api/documents').json()}


def test_company_share_and_private_reset(client, sharing_document):
    login(client, 'a.owner')
    assert change(client, sharing_document, 'company').status_code == 200
    client.cookies.clear()
    login(client, 'a.staff')
    assert client.get(f"/api/documents/{sharing_document['id']}").status_code == 200
    client.cookies.clear()
    login(client, 'a.owner')
    assert change(client, sharing_document, 'private').status_code == 200


@pytest.mark.parametrize('username', ['a.reader', 'a.admin', 'a.manager', 'b.owner'])
def test_only_current_owner_manages_sharing_without_existence_leak(client, sharing_document, username):
    login(client, username)
    response = change(client, sharing_document, 'company')
    missing = change(client, {**sharing_document, 'id': 999999999}, 'company')
    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json()
    assert client.get(f"/documents/{sharing_document['id']}/sharing").status_code == 404


@pytest.mark.parametrize('reader', ['b.reader', 'missing', 'a.owner'])
def test_invalid_readers_rejected_atomically(client, sharing_document, reader):
    login(client, 'a.owner')
    user_id = sharing_document['users'].get(reader, 999999999)
    assert change(client, sharing_document, 'specified', [user_id]).status_code == 422
    assert client.get(f"/api/documents/{sharing_document['id']}").json()['visibility'] == 'private'


def test_inactive_reader_rejected(client, sharing_document, admin_db):
    user_id = sharing_document['users']['a.reader']
    admin_db.execute('UPDATE app_core.users SET is_active = false WHERE id = %s', (user_id,))
    try:
        login(client, 'a.owner')
        assert change(client, sharing_document, 'specified', [user_id]).status_code == 422
    finally:
        admin_db.execute('UPDATE app_core.users SET is_active = true WHERE id = %s', (user_id,))


def test_stale_version_and_ambiguous_modes_rejected(client, sharing_document):
    login(client, 'a.owner')
    assert change(client, sharing_document, 'company', version_id=999999999).status_code == 409
    assert change(client, sharing_document, 'private', [sharing_document['users']['a.reader']]).status_code == 422
    assert change(client, sharing_document, 'specified').status_code == 422
    assert change(client, sharing_document, 'company', tenant_id=123).status_code == 422


def test_sharing_requires_csrf_and_same_origin(client, sharing_document):
    login(client, 'a.owner')
    assert change(client, sharing_document, 'company', csrf_token='').status_code == 403
    payload = {'version_id': sharing_document['version_id'], 'mode': 'company',
               'csrf_token': client.get('/api/me').json()['csrf_token']}
    assert client.put(f"/api/documents/{sharing_document['id']}/sharing", json=payload,
                      headers={'Origin': 'https://evil.example'}).status_code == 403


def test_owner_sharing_page_has_only_active_same_tenant_options(client, sharing_document):
    login(client, 'a.owner')
    page = client.get(f"/documents/{sharing_document['id']}/sharing")
    assert page.status_code == 200
    assert 'a.reader' in page.text and 'b.reader' not in page.text
    assert 'name="csrf_token"' in page.text
    assert f'/documents/{sharing_document["id"]}/sharing' in client.get(f'/documents/{sharing_document["id"]}').text

@pytest.mark.parametrize('reader_id', [True, '4', -1, 0])
def test_reader_ids_are_positive_strict_integers(client, sharing_document, reader_id):
    login(client, 'a.owner')
    assert change(client, sharing_document, 'specified', [reader_id]).status_code == 422


def test_sharing_requires_login(client, sharing_document):
    response = client.put(f"/api/documents/{sharing_document['id']}/sharing", json={
        'version_id': sharing_document['version_id'], 'mode': 'company'})
    assert response.status_code == 401


def test_form_saves_with_csrf_and_rejects_missing_token(client, sharing_document):
    login(client, 'a.owner')
    url = f"/documents/{sharing_document['id']}/sharing"
    data = {'version_id': sharing_document['version_id'], 'mode': 'company'}
    assert client.post(url, data=data, headers={'Origin': BASE_URL}).status_code == 403
    data['csrf_token'] = client.get('/api/me').json()['csrf_token']
    response = client.post(url, data=data, headers={'Origin': BASE_URL}, follow_redirects=False)
    assert response.status_code == 303
    assert client.get(f"/api/documents/{sharing_document['id']}").json()['visibility'] == 'company'
    client.cookies.clear()
    login(client, 'b.reader')
    assert client.get(f"/api/documents/{sharing_document['id']}").status_code == 404


def test_invalid_replacement_preserves_existing_acl(client, sharing_document):
    doc = sharing_document
    login(client, 'a.owner')
    assert change(client, doc, 'specified', [doc['users']['a.reader']]).status_code == 200
    assert change(client, doc, 'specified', [doc['users']['a.staff'], doc['users']['b.reader']]).status_code == 422
    client.cookies.clear()
    login(client, 'a.reader')
    assert client.get(f"/api/documents/{doc['id']}").status_code == 200
    client.cookies.clear()
    login(client, 'a.staff')
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404


@pytest.mark.parametrize('kind', ['archived', 'not-ready'])
def test_sharing_hides_unavailable_document(client, sharing_document, admin_db, kind):
    if kind == 'archived':
        admin_db.execute("UPDATE app_core.documents SET status = 'archived' WHERE id = %s", (sharing_document['id'],))
    else:
        admin_db.execute("UPDATE app_core.document_versions SET status = 'failed' WHERE id = %s", (sharing_document['version_id'],))
    login(client, 'a.owner')
    assert change(client, sharing_document, 'company').status_code == 404
    assert client.get(f"/documents/{sharing_document['id']}/sharing").status_code == 404


def test_effective_version_owner_controls_sharing(client, sharing_document, admin_db):
    # documents.owner_id is deliberately stale: only version ownership authorizes this operation.
    admin_db.execute('UPDATE app_core.document_versions SET owner_id = %s WHERE id = %s',
                     (sharing_document['users']['a.staff'], sharing_document['version_id']))
    login(client, 'a.owner')
    assert change(client, sharing_document, 'company').status_code == 404
    client.cookies.clear()
    login(client, 'a.staff')
    assert change(client, sharing_document, 'company').status_code == 200


def test_waiting_share_rechecks_pointer_after_version_lock(sharing_document, admin_db):
    """An old query snapshot must not grant ACL to the version replaced while it waited."""
    import os
    import threading
    import time

    import psycopg
    from fastapi import HTTPException
    from psycopg.rows import dict_row

    from app import repository

    doc = sharing_document
    tenant_id = admin_db.execute('SELECT tenant_id FROM app_core.documents WHERE id = %s', (doc['id'],)).fetchone()[0]
    outcome = []
    started = threading.Event()

    def share():
        with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row,
                             application_name='sharing-lock-regression') as connection:
            started.set()
            try:
                repository.replace_sharing(connection, {'id': doc['users']['a.owner'], 'tenant_id': tenant_id},
                                           doc['id'], doc['version_id'], 'specified', [doc['users']['a.reader']])
                outcome.append(200)
            except HTTPException as error:
                connection.rollback()
                outcome.append(error.status_code)

    admin_db.execute('BEGIN')
    worker = threading.Thread(target=share)
    try:
        admin_db.execute('SELECT id FROM app_core.document_versions WHERE id = %s FOR UPDATE', (doc['version_id'],))
        worker.start()
        assert started.wait(5), 'share worker did not start'
        deadline = time.monotonic() + 5
        waiting = False
        while time.monotonic() < deadline:
            admin_db.execute('SELECT pg_stat_clear_snapshot()')
            waiting = admin_db.execute(
                """SELECT EXISTS (SELECT 1 FROM pg_stat_activity
                   WHERE application_name = 'sharing-lock-regression' AND wait_event_type = 'Lock')"""
            ).fetchone()[0]
            if waiting:
                break
            time.sleep(0.02)
        assert waiting, 'share query did not wait on outgoing version lock'
        new_version = admin_db.execute(
            """INSERT INTO app_core.document_versions
               (tenant_id, document_id, version_number, owner_id, status, content)
               VALUES (%s, %s, 2, %s, 'ready', 'new effective content') RETURNING id""",
            (tenant_id, doc['id'], doc['users']['a.owner']),
        ).fetchone()[0]
        admin_db.execute('UPDATE app_core.documents SET current_version_id = %s WHERE id = %s',
                         (new_version, doc['id']))
        admin_db.execute('COMMIT')
    finally:
        admin_db.execute('ROLLBACK')
        worker.join(timeout=5)
    assert not worker.is_alive()
    assert outcome == [409]
    assert admin_db.execute('SELECT count(*) FROM app_core.version_readers WHERE version_id = %s',
                            (doc['version_id'],)).fetchone()[0] == 0
