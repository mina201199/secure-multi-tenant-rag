"""正式 PDF 上傳與有效版頁次閱讀契約。"""
import pytest

from app import worker
from tests.helpers import login
from tests.pdf_fixtures import make_pdf
from tests.test_version_routes import version_document, upload
from tests.test_worker import workerDsn, workerDb


def test_pdf_upload_and_escaped_physical_page_display(client, version_document, workerDb):
  login(client, 'a.owner')
  response = upload(client, version_document, source=make_pdf(('<script>alert(1)</script>', 'Amount 1500')),
    filename='new.PDF')
  assert response.status_code == 202
  assert worker.runOnce(workerDb)
  url = f"/documents/{version_document['id']}"
  page = client.get(url)
  assert page.status_code == 200
  assert '第 1 頁' in page.text and '第 2 頁' in page.text
  assert '&lt;script&gt;' in page.text and '<script>alert(1)</script>' not in page.text
  client.cookies.clear()
  assert login(client, 'b.owner').status_code == 303
  assert client.get(url).status_code == 404
  assert client.get('/api' + url).status_code == 404


@pytest.mark.parametrize('filename,source,status', [
  ('bad.pdf', b'plain text', 422), ('pdf.txt', make_pdf(), 422),
  ('bad.docx', make_pdf(), 422), ('big.txt', b'a' * (1048576 + 1), 413),
  ('big.pdf', b'%PDF-' + b'x' * 2097152, 413),
])
def test_pdf_envelope_rejected_before_queued_work(client, version_document, admin_db, filename, source, status):
  login(client, 'a.owner')
  assert upload(client, version_document, source=source, filename=filename).status_code == status
  assert admin_db.execute('SELECT count(*) FROM app_core.version_jobs WHERE document_id=%s',
    (version_document['id'],)).fetchone() == (0,)


def test_invalid_pdf_structure_is_failed_asynchronously(client, version_document, workerDb):
  login(client, 'a.owner')
  response = upload(client, version_document, source=b'%PDF-1.7 broken', filename='broken.pdf')
  assert response.status_code == 202
  assert worker.runOnce(workerDb)
  status = client.get('/api/jobs/' + response.json()['job_id']).json()
  assert (status['status'], status['error_code']) == ('failed', 'invalid_pdf')
  assert client.get(f"/documents/{version_document['id']}/versions?job_id={status['job_id']}").status_code == 200


@pytest.mark.parametrize('username', ['a.reader', 'a.admin', 'a.manager', 'b.owner'])
def test_pdf_upload_does_not_bypass_owner_or_tenant(client, version_document, username):
  login(client, username)
  assert upload(client, version_document, source=make_pdf(), filename='new.pdf').status_code == 404


def test_pdf_upload_requires_csrf_and_exposes_limits(client, version_document):
  login(client, 'a.owner')
  assert upload(client, version_document, source=make_pdf(), filename='new.pdf',
    data={'csrf_token': 'wrong'}).status_code == 403
  page = client.get(f"/documents/{version_document['id']}/versions")
  assert '.pdf' in page.text and '20' in page.text and '2 MiB' in page.text
