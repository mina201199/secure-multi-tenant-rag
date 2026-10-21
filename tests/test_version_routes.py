"""Real HTTP/DB boundaries for owner-only immutable TXT version creation."""
import re
from uuid import uuid4

import pytest

from tests.helpers import BASE_URL, login


@pytest.fixture
def version_document(admin_db):
    tenant, owner = admin_db.execute("SELECT tenant_id, id FROM app_core.users WHERE username = 'a.owner'").fetchone()
    doc = admin_db.execute("""INSERT INTO app_core.documents (tenant_id, owner_id, title, category, status)
        VALUES (%s, %s, '新版表單測試', '測試', 'ready') RETURNING id""", (tenant, owner)).fetchone()[0]
    version = admin_db.execute("""INSERT INTO app_core.document_versions
        (tenant_id, document_id, version_number, owner_id, status, content)
        VALUES (%s, %s, 1, %s, 'ready', '原始版本') RETURNING id""", (tenant, doc, owner)).fetchone()[0]
    admin_db.execute('UPDATE app_core.documents SET current_version_id = %s WHERE id = %s', (version, doc))
    yield {'id': doc, 'version_id': version, 'owner_id': owner}
    if admin_db.execute("SELECT to_regclass('app_core.version_jobs')").fetchone()[0]:
        admin_db.execute('DELETE FROM app_core.version_jobs WHERE document_id = %s', (doc,))
        admin_db.execute('DELETE FROM app_core.version_sources WHERE document_id = %s', (doc,))
    admin_db.execute('UPDATE app_core.documents SET current_version_id = NULL WHERE id = %s', (doc,))
    admin_db.execute('DELETE FROM app_core.document_versions WHERE document_id = %s', (doc,))
    admin_db.execute('DELETE FROM app_core.documents WHERE id = %s', (doc,))


def upload(client, doc, *, source=b'new content', filename='new.txt', key=None, data=None, headers=None, form=False):
    fields = {'expected_current_version_id': str(doc['version_id']),
              'csrf_token': client.get('/api/me').json().get('csrf_token', '')}
    if data:
        fields.update(data)
    key = key or uuid4().hex
    request_headers = {'Origin': BASE_URL, 'Idempotency-Key': key}
    if form:
        fields['idempotency_key'] = key
    if headers:
        request_headers.update(headers)
    url = f"/documents/{doc['id']}/versions" if form else f"/api/documents/{doc['id']}/versions"
    return client.post(url, data=fields, files={'file': (filename, source, 'text/plain')},
                       headers=request_headers, follow_redirects=False)


def test_create_commits_queued_private_version_and_replays_same_response(client, version_document, admin_db):
    doc = version_document
    login(client, 'a.owner')
    key = uuid4().hex
    created = upload(client, doc, key=key)
    assert created.status_code == 202
    result = created.json()
    assert set(result) == {'document_id', 'version_id', 'version_number', 'processing_status', 'is_current', 'job_id'}
    assert result['document_id'] == doc['id'] and result['version_number'] == 2
    assert result['processing_status'] == 'queued' and result['is_current'] is False
    assert upload(client, doc, key=key).json() == result
    assert upload(client, doc, key=key).status_code == 202
    assert admin_db.execute('SELECT current_version_id FROM app_core.documents WHERE id = %s', (doc['id'],)).fetchone()[0] == doc['version_id']
    assert admin_db.execute('SELECT status, visibility, content FROM app_core.document_versions WHERE id = %s', (result['version_id'],)).fetchone() == ('queued', 'private', None)
    assert client.get(f"/api/documents/{doc['id']}").json()['content'] == '原始版本'
    status = client.get('/api/jobs/' + result['job_id'])
    assert status.status_code == 200
    assert status.json()['status'] == 'queued'
    assert set(status.json()) <= {'job_id', 'document_id', 'version_id', 'status', 'error_code', 'retryable'}
    assert 'new content' not in status.text


@pytest.mark.parametrize('username', ['a.reader', 'a.admin', 'a.manager', 'b.owner'])
def test_non_document_owner_cannot_create_or_manage(client, version_document, username):
    login(client, username)
    response = upload(client, version_document)
    missing = upload(client, {**version_document, 'id': 999999999})
    assert response.status_code == missing.status_code == 404
    assert response.json() == missing.json()
    assert client.get(f"/documents/{version_document['id']}/versions").status_code == 404


def test_document_owner_can_manage_without_reading_effective_version(client, version_document, admin_db):
    doc = version_document
    staff = admin_db.execute("SELECT id FROM app_core.users WHERE username = 'a.staff'").fetchone()[0]
    admin_db.execute('UPDATE app_core.document_versions SET owner_id = %s WHERE id = %s', (staff, doc['version_id']))
    login(client, 'a.owner')
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
    assert client.get(f"/documents/{doc['id']}/versions").status_code == 200
    assert upload(client, doc).status_code == 202
    client.cookies.clear()
    login(client, 'a.staff')
    assert client.get(f"/documents/{doc['id']}/versions").status_code == 404
    assert f"/documents/{doc['id']}/versions" not in client.get(f"/documents/{doc['id']}").text


@pytest.mark.parametrize('field', ['tenant_id', 'owner_id', 'status', 'visibility', 'version_number', 'reader_ids', 'path', 'idempotency_key'])
def test_api_rejects_client_controlled_or_unknown_fields(client, version_document, field):
    login(client, 'a.owner')
    assert upload(client, version_document, data={field: '1'}).status_code == 422


@pytest.mark.parametrize('source,filename,status', [
    (b'%PDF-1.7 bad', 'file.pdf', 422), (b'\xff\xfe', 'file.txt', 422),
    (b'a\x00b', 'file.txt', 422), (b'\t \n', 'file.txt', 422),
    (b'x' * (1024 * 1024 + 1), 'file.txt', 413),
    ('字' * 200001, 'file.txt', 413),
], ids=['pdf', 'invalid-utf8', 'nul', 'whitespace', 'byte-cap', 'character-cap'])
def test_invalid_txt_rejected_without_new_version(client, version_document, admin_db, source, filename, status):
    login(client, 'a.owner')
    if isinstance(source, str):
        source = source.encode('utf-8')
    assert upload(client, version_document, source=source, filename=filename).status_code == status
    assert admin_db.execute('SELECT count(*) FROM app_core.document_versions WHERE document_id = %s', (version_document['id'],)).fetchone()[0] == 1


def test_utf8_bom_and_unicode_limit_are_accepted(client, version_document):
    login(client, 'a.owner')
    assert upload(client, version_document, source=b'\xef\xbb\xbf' + ('字' * 200000).encode()).status_code == 202


@pytest.mark.parametrize('key', ['', 'short', 'x' * 129, 'has spaces in key', 'contains.punctuation'])
def test_invalid_idempotency_key_rejected(client, version_document, key):
    login(client, 'a.owner')
    assert upload(client, version_document, headers={'Idempotency-Key': key}).status_code == 422


def test_stale_base_pending_and_changed_fingerprint_conflict(client, version_document):
    login(client, 'a.owner')
    assert upload(client, version_document, data={'expected_current_version_id': '999999999'}).status_code == 409
    key = uuid4().hex
    assert upload(client, version_document, key=key).status_code == 202
    assert upload(client, version_document, key=key, source=b'different').status_code == 409
    assert upload(client, version_document).status_code == 409


def test_upload_requires_csrf_origin_and_session(client, version_document):
    login(client, 'a.owner')
    assert upload(client, version_document, data={'csrf_token': ''}).status_code == 403
    assert upload(client, version_document, headers={'Origin': 'https://evil.example'}).status_code == 403
    client.cookies.clear()
    assert client.post(f"/api/documents/{version_document['id']}/versions").status_code == 401
    assert client.get('/api/jobs/' + str(uuid4())).status_code == 401
    assert client.get(f"/documents/{version_document['id']}/versions", follow_redirects=False).status_code == 303


def test_job_lookup_hides_missing_malformed_and_lost_management(client, version_document, admin_db):
    login(client, 'a.owner')
    result = upload(client, version_document).json()
    missing = client.get('/api/jobs/' + str(uuid4()))
    assert missing.status_code == 404
    malformed = client.get('/api/jobs/not-a-job')
    assert malformed.status_code == 404 and malformed.json() == missing.json()
    staff = admin_db.execute("SELECT id FROM app_core.users WHERE username = 'a.staff'").fetchone()[0]
    admin_db.execute('UPDATE app_core.documents SET owner_id = %s WHERE id = %s', (staff, version_document['id']))
    assert client.get('/api/jobs/' + result['job_id']).json() == missing.json()


def test_management_form_has_server_tokens_and_commits_upload(client, version_document):
    doc = version_document
    login(client, 'a.owner')
    url = f"/documents/{doc['id']}/versions"
    page = client.get(url)
    assert page.status_code == 200
    assert 'enctype="multipart/form-data"' in page.text
    assert f'name="expected_current_version_id" value="{doc["version_id"]}"' in page.text
    key = re.search(r'name="idempotency_key" value="([A-Za-z0-9_-]{16,128})"', page.text).group(1)
    assert 'name="csrf_token"' in page.text
    assert url in client.get(f"/documents/{doc['id']}").text
    result = upload(client, doc, form=True, key=key)
    assert result.status_code == 303
    page = client.get(result.headers['location'])
    assert page.status_code == 200 and 'queued' in page.text
    assert upload(client, doc, form=True, key=key).status_code == 303


def test_form_duplicate_fields_and_safe_html_errors(client, version_document):
    login(client, 'a.owner')
    token = client.get('/api/me').json()['csrf_token']
    fields = [('file', ('new.txt', b'new', 'text/plain')),
              ('expected_current_version_id', (None, str(version_document['version_id']))),
              ('expected_current_version_id', (None, str(version_document['version_id']))),
              ('csrf_token', (None, token)), ('idempotency_key', (None, uuid4().hex))]
    response = client.post(f"/documents/{version_document['id']}/versions", files=fields,
                           headers={'Origin': BASE_URL, 'Accept': 'text/html'})
    assert response.status_code == 422 and response.headers['content-type'].startswith('text/html')
    assert 'new.txt' not in response.text


def test_streaming_multipart_envelope_is_bounded_without_content_length(client, version_document, admin_db):
    login(client, 'a.owner')
    token = client.get('/api/me').json()['csrf_token']
    boundary = 'version-upload-limit'
    body = (f'--{boundary}\r\nContent-Disposition: form-data; name="csrf_token"\r\n\r\n{token}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="expected_current_version_id"\r\n\r\n{version_document["version_id"]}\r\n'
            f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="new.txt"\r\n'
            f'Content-Type: text/plain\r\n\r\nnew content\r\n--{boundary}--\r\n').encode()
    body = body.replace(b'new content', b'x' * (2 * 1024 * 1024))
    response = client.post(f"/api/documents/{version_document['id']}/versions",
                           content=(body[offset:offset + 8192] for offset in range(0, len(body), 8192)),
                           headers={'Origin': BASE_URL, 'Idempotency-Key': uuid4().hex,
                                    'Content-Type': f'multipart/form-data; boundary={boundary}'})
    assert response.status_code == 413
    assert admin_db.execute('SELECT count(*) FROM app_core.document_versions WHERE document_id = %s',
                            (version_document['id'],)).fetchone()[0] == 1


@pytest.mark.parametrize('content_length', [None, '1'])
def test_parser_stops_receiving_at_aggregate_limit_even_without_trusted_length(content_length):
    import asyncio

    from fastapi import HTTPException
    from starlette.requests import Request

    from app.version_routes import read_upload

    # An ASGI receive probe proves the app stops consuming the request, unlike
    # TestClient which can combine its complete content iterator into one chunk.
    prefix = b'--bounded\r\nContent-Disposition: form-data; name="file"; filename="test.txt"\r\n\r\n'
    body = prefix + b'x' * (2 * 1024 * 1024) + b'\r\n--bounded--\r\n'
    consumed = 0
    headers = [(b'content-type', b'multipart/form-data; boundary=bounded')]
    if content_length is not None:
        headers.append((b'content-length', content_length.encode()))

    async def receive():
        nonlocal consumed
        chunk = body[consumed:consumed + 8192]
        consumed += len(chunk)
        return {'type': 'http.request', 'body': chunk, 'more_body': consumed < len(body)}

    request = Request({'type': 'http', 'method': 'POST', 'path': '/', 'headers': headers}, receive)
    with pytest.raises(HTTPException) as error:
        asyncio.run(read_upload(request))
    assert error.value.status_code == 413
    assert consumed <= 1024 * 1024 + 64 * 1024 + 8192
    assert consumed < len(body)

def test_capacity_error_uses_safe_html_for_browser_and_json_for_api(client, version_document):
    login(client, 'a.owner')
    response = upload(client, version_document, source=b'x' * (1024 * 1024 + 1), form=True,
                      headers={'Accept': 'text/html'})
    assert response.status_code == 413
    assert response.headers['content-type'].startswith('text/html')
    response = upload(client, version_document, source=b'x' * (1024 * 1024 + 1),
                      headers={'Accept': 'text/html'})
    assert response.status_code == 413
    assert response.headers['content-type'].startswith('application/json')

def test_database_wait_does_not_block_async_upload_event_loop(client, version_document, monkeypatch):
    import asyncio
    import threading

    import httpx

    from app import version_service
    from app.main import app

    login(client, 'a.owner')
    token = client.get('/api/me').json()['csrf_token']
    session = client.cookies.get('rag_session')
    started, release = threading.Event(), threading.Event()

    def database_wait(*args):
        started.set()
        release.wait(3)
        return {'document_id': version_document['id'], 'version_id': 123, 'version_number': 2,
                'processing_status': 'queued', 'is_current': False, 'job_id': str(uuid4())}

    # The blocked dependency represents the real synchronous database call;
    # exercise the actual route and event loop with a concurrent health request.
    monkeypatch.setattr(version_service, 'create_version', database_wait)

    async def scenario():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url=BASE_URL,
                                    cookies={'rag_session': session}) as browser:
            task = asyncio.create_task(browser.post(f"/api/documents/{version_document['id']}/versions",
                data={'csrf_token': token, 'expected_current_version_id': str(version_document['version_id'])},
                files={'file': ('new.txt', b'new content', 'text/plain')},
                headers={'Origin': BASE_URL, 'Idempotency-Key': uuid4().hex}))
            try:
                assert await asyncio.to_thread(started.wait, 5)
                health = await asyncio.wait_for(browser.get('/health'), timeout=1)
                assert health.status_code == 200
                assert not task.done(), 'database wait blocked the upload event loop'
            finally:
                release.set()
                await task
    asyncio.run(scenario())

@pytest.mark.parametrize('body', [b'not-a-multipart-boundary',
    b'--broken\r\nInvalid Header\r\n\r\ndata\r\n--broken--\r\n'])
def test_malformed_multipart_is_safe_validation_error(client, version_document, body):
    login(client, 'a.owner')
    response = client.post(f"/api/documents/{version_document['id']}/versions", content=body,
        headers={'Origin': BASE_URL, 'Idempotency-Key': uuid4().hex,
                 'Content-Type': 'multipart/form-data; boundary=broken'})
    assert response.status_code == 422
    assert response.headers['content-type'].startswith('application/json')


@pytest.mark.parametrize('path', ['/api/documents/{id}/versions', '/documents/{id}/versions'])
def test_document_identifier_outside_bigint_is_validation_error(client, version_document, path):
    login(client, 'a.owner')
    url = path.format(id=9223372036854775808)
    if path.startswith('/api'):
        response = client.post(url,
            data={'csrf_token': client.get('/api/me').json()['csrf_token'],
                  'expected_current_version_id': str(version_document['version_id'])},
            files={'file': ('new.txt', b'new content', 'text/plain')},
            headers={'Origin': BASE_URL, 'Idempotency-Key': uuid4().hex})
    else:
        response = client.get(url)
    assert response.status_code == 422
