from tests.helpers import login


def test_demo_is_available_without_unlocking_real_documents(client):
    response = client.get('/demo')
    assert response.status_code == 200
    assert '互動展示' in response.text
    assert client.get('/api/documents').status_code == 401
    assert client.get('/documents', follow_redirects=False).status_code == 303


def test_workspace_assets_are_served(client):
    assert client.get('/static/workspace.css').status_code == 200
    assert client.get('/static/workspace.js').status_code == 200


def test_document_workspace_keeps_real_session_identity(client):
    login(client, 'b.reader')
    page = client.get('/documents')
    assert page.status_code == 200
    assert '乙公司' in page.text
    assert '甲公司' not in page.text
