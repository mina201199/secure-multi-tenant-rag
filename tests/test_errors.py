"""HTML errors keep API contracts and do not reveal protected document metadata."""
import pytest
from fastapi import FastAPI, Form, HTTPException
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.main import app
from tests.helpers import BASE_URL, login


@pytest.fixture
def error_client():
    # Exercise the registered application handlers without session/database dependencies.
    isolated = FastAPI(exception_handlers=app.exception_handlers, openapi_url=None)

    @isolated.get('/documents/error/{status}')
    @isolated.get('/api/error/{status}')
    @isolated.get('/health/error/{status}')
    def failure(status: int):
        raise HTTPException(status, 'secret-file.pdf /private/path secret-token',
                            headers={'WWW-Authenticate': 'Session', 'Retry-After': '60'})

    @isolated.get('/documents/starlette')
    def starlette_failure():
        raise StarletteHTTPException(404, 'secret-file.pdf')

    @isolated.post('/documents/form')
    def form(version_id: int = Form(...)):
        return {'version_id': version_id}

    return TestClient(isolated, base_url=BASE_URL)


@pytest.mark.parametrize('status', [401, 403, 404, 409])
def test_document_errors_offer_safe_html_and_recovery(error_client, status):
    response = error_client.get(f'/documents/error/{status}?token=secret-query')
    assert response.status_code == status
    assert response.headers['content-type'].startswith('text/html')
    assert f'<h1' in response.text and str(status) in response.text
    assert 'href="/documents"' in response.text and 'href="/login"' in response.text
    assert '<html lang="zh-Hant">' in response.text
    for secret in ['secret-file.pdf', '/private/path', 'secret-token', 'secret-query']:
        assert secret not in response.text
    assert response.headers['www-authenticate'] == 'Session'
    assert response.headers['retry-after'] == '60'


@pytest.mark.parametrize('status', [401, 403, 404, 409, 422])
def test_api_errors_remain_json_even_for_browser_accept(error_client, status):
    response = error_client.get(f'/api/error/{status}', headers={'Accept': 'text/html'})
    assert response.status_code == status
    assert response.headers['content-type'].startswith('application/json')
    assert response.json() == {'detail': 'secret-file.pdf /private/path secret-token'}


@pytest.mark.parametrize('accept,html', [
    ('text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8', True),
    ('application/json', False),
    ('text/html;q=0,application/json', False),
    ('text/html;q=0.1,application/json;q=0.9', False),
])
def test_explicit_accept_preference_is_respected(error_client, accept, html):
    response = error_client.get('/documents/error/404', headers={'Accept': accept})
    assert response.headers['content-type'].startswith('text/html' if html else 'application/json')


def test_routing_and_starlette_404_use_html_only_for_page_requests(error_client):
    for path in ['/unknown-page', '/documents/starlette']:
        response = error_client.get(path, headers={'Accept': 'text/html'})
        assert response.status_code == 404
        assert response.headers['content-type'].startswith('text/html')
        assert 'secret-file.pdf' not in response.text
    for path in ['/unknown-page', '/api/missing', '/api', '/static/missing', '/docs/missing',
                 '/redoc/missing', '/openapi.json', '/health/error/404']:
        headers = {} if path == '/unknown-page' else {'Accept': 'text/html'}
        response = error_client.get(path, headers=headers)
        assert response.status_code == 404
        assert response.headers['content-type'].startswith('application/json')


def test_form_validation_html_does_not_echo_submitted_values(error_client):
    response = error_client.post('/documents/form', data={'version_id': 'secret-submitted-value'})
    assert response.status_code == 422
    assert response.headers['content-type'].startswith('text/html')
    assert 'secret-submitted-value' not in response.text
    response = error_client.post('/documents/form', data={}, headers={'Accept': 'application/json'})
    assert response.status_code == 422 and 'detail' in response.json()


def test_hidden_and_missing_html_documents_are_identical(client, admin_db):
    other_id = admin_db.execute(
        "SELECT d.id FROM app_core.documents d JOIN app_core.tenants t ON t.id = d.tenant_id "
        "WHERE t.name = '乙公司' ORDER BY d.id LIMIT 1"
    ).fetchone()[0]
    login(client, 'a.owner')
    for suffix in ['', '/sharing']:
        missing = client.get('/documents/999999999' + suffix)
        hidden = client.get(f'/documents/{other_id}' + suffix)
        assert missing.status_code == hidden.status_code == 404
        assert missing.headers['content-type'].startswith('text/html')
        assert missing.text == hidden.text


def test_real_form_auth_errors_have_pages_and_api_stays_json(client):
    page = client.post('/documents/1/sharing', data={'version_id': 1, 'mode': 'company'})
    assert page.status_code == 401 and page.headers['content-type'].startswith('text/html')
    api = client.get('/api/me', headers={'Accept': 'text/html'})
    assert api.status_code == 401 and api.json() == {'detail': '請先登入'}
    login(client, 'a.owner')
    csrf = client.post('/logout', headers={'Origin': BASE_URL})
    assert csrf.status_code == 403 and csrf.headers['content-type'].startswith('text/html')
