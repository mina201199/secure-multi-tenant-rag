"""PDF worker 的真實受限登入、失敗隔離與租約恢復驗收。"""
import os
import subprocess
import sys
from threading import Event

import pytest

from app import version_service as service, worker
from tests.helpers import login
from tests.pdf_fixtures import make_pdf
from tests.test_version_writes import write_document, pointer
from tests.test_worker import workerDsn, workerDb, expire, available, claimJob


def queuePdf(connection, doc, source=None):
  result = service.create_version(connection, doc['owner'], doc['id'], doc['old'],
    make_pdf(('Amount NT$1500', 'Part A-123 unit mm')) if source is None else source,
    'pdf_worker_key_0001', source_format='pdf')
  connection.commit()
  return result


def test_pdf_worker_publishes_pages_private_and_revokes_old_reader(
    workerDb, runtime_db, admin_db, write_document, client):
  queued = queuePdf(runtime_db, write_document)
  login(client, 'a.reader')
  url = f"/api/documents/{write_document['id']}"
  assert client.get(url).status_code == 200
  assert worker.runOnce(workerDb)
  assert pointer(admin_db, write_document) == queued['version_id']
  client.cookies.clear()
  assert login(client, 'a.owner').status_code == 303
  page = client.get(url).json()
  assert [p['page'] for p in page['source_pages']] == [1, 2]
  assert 'NT$1500' in page['source_pages'][0]['text']
  assert 'A-123' in page['source_pages'][1]['text']
  assert page['visibility'] == 'private'
  assert admin_db.execute('SELECT count(*) FROM app_core.version_readers WHERE version_id=%s',
    (queued['version_id'],)).fetchone() == (0,)
  client.cookies.clear()
  assert login(client, 'a.reader').status_code == 303
  assert client.get(url).status_code == 404
  assert service.get_job(runtime_db, write_document['owner'], queued['job_id'])['status'] == 'ready'
  assert not worker.runOnce(workerDb)


@pytest.mark.parametrize('source,errorCode', [
  (b'%PDF-1.7 broken', 'invalid_pdf'),
  (make_pdf(('secret',), encrypted=True), 'encrypted_pdf'),
  (make_pdf(('readable first', '')), 'unreadable_page'),
], ids=['corrupt', 'encrypted', 'mixed-page'])
def test_bad_pdf_fails_without_replacing_old_or_stopping_worker(
    workerDb, runtime_db, admin_db, write_document, source, errorCode):
  queued = queuePdf(runtime_db, write_document, source)
  assert worker.runOnce(workerDb)
  status = service.get_job(runtime_db, write_document['owner'], queued['job_id'])
  runtime_db.commit()
  assert (status['status'], status['error_code']) == ('failed', errorCode)
  assert pointer(admin_db, write_document) == write_document['old']
  assert admin_db.execute('SELECT content,source_pages FROM app_core.document_versions WHERE id=%s',
    (queued['version_id'],)).fetchone() == (None, None)
  assert service.recover_job(workerDb) is None
  workerDb.commit()
  nextJob = service.create_version(runtime_db, write_document['owner'], write_document['id'],
    write_document['old'], b'next TXT after bad PDF', 'after_bad_pdf_0001')
  runtime_db.commit()
  assert worker.runOnce(workerDb)
  assert pointer(admin_db, write_document) == nextJob['version_id']


def test_stop_after_pdf_extraction_leaves_lease_for_recovery(
    workerDb, runtime_db, admin_db, write_document, monkeypatch):
  from app import pdf_parser
  queued = queuePdf(runtime_db, write_document)
  stop = Event()
  original = pdf_parser.parsePdf

  def parseAndStop(source):
    pages = original(source)
    stop.set()
    return pages

  monkeypatch.setattr(pdf_parser, 'parsePdf', parseAndStop)
  assert worker.runOnce(workerDb, stop)
  assert pointer(admin_db, write_document) == write_document['old']
  assert admin_db.execute('SELECT status,attempts FROM app_core.version_jobs WHERE id=%s',
    (queued['job_id'],)).fetchone() == ('processing', 1)
  monkeypatch.setattr(pdf_parser, 'parsePdf', original)
  expire(admin_db, queued['job_id'])
  assert worker.runOnce(workerDb)
  available(admin_db, queued['job_id'])
  assert worker.runOnce(workerDb)
  assert pointer(admin_db, write_document) == queued['version_id']


def test_pdf_lease_expiring_during_parse_cannot_publish(
    workerDb, runtime_db, admin_db, write_document, monkeypatch):
  from app import pdf_parser
  queued = queuePdf(runtime_db, write_document)
  original = pdf_parser.parsePdf

  def parseAndExpire(source):
    pages = original(source)
    expire(admin_db, queued['job_id'])
    return pages

  monkeypatch.setattr(pdf_parser, 'parsePdf', parseAndExpire)
  assert worker.runOnce(workerDb)
  assert pointer(admin_db, write_document) == write_document['old']
  assert service.get_job(runtime_db, write_document['owner'], queued['job_id'])['error_code'] == 'lease_expired'


def test_killed_pdf_worker_claim_recovers_without_duplicate_version(
    workerDsn, workerDb, runtime_db, admin_db, write_document):
  import select
  queued = queuePdf(runtime_db, write_document)
  script = '''import os,time
from app.worker import connectWorker
from app.version_service import claim_job
with connectWorker(os.environ['WORKER_DATABASE_URL']) as conn:
  assert claim_job(conn)['source_format'] == 'pdf'
  conn.commit()
  print('claimed', flush=True)
  time.sleep(60)
'''
  process = subprocess.Popen([sys.executable, '-u', '-c', script],
    env={**os.environ, 'WORKER_DATABASE_URL': workerDsn}, stdout=subprocess.PIPE,
    stderr=subprocess.PIPE, text=True)
  try:
    assert select.select([process.stdout], [], [], 10)[0]
    assert process.stdout.readline().strip() == 'claimed'
    process.kill()
    process.communicate(timeout=10)
    expire(admin_db, queued['job_id'])
    assert worker.runOnce(workerDb)
    available(admin_db, queued['job_id'])
    assert worker.runOnce(workerDb)
    assert pointer(admin_db, write_document) == queued['version_id']
    assert admin_db.execute('SELECT attempts FROM app_core.version_jobs WHERE id=%s',
      (queued['job_id'],)).fetchone() == (2,)
    assert admin_db.execute('SELECT count(*) FROM app_core.version_sources WHERE document_id=%s',
      (write_document['id'],)).fetchone() == (1,)
  finally:
    if process.poll() is None:
      process.kill()
      process.communicate(timeout=5)


def test_pdf_cli_once_does_not_log_content_or_credentials(
    workerDsn, runtime_db, admin_db, write_document):
  queued = queuePdf(runtime_db, write_document, make_pdf(('private-pdf-sentinel-8315',)))
  process = subprocess.run([sys.executable, '-m', 'app.worker', '--once'],
    env={**os.environ, 'WORKER_DATABASE_URL': workerDsn}, capture_output=True, text=True, timeout=30)
  assert process.returncode == 0
  assert pointer(admin_db, write_document) == queued['version_id']
  assert 'private-pdf-sentinel-8315' not in process.stdout + process.stderr
  assert workerDsn not in process.stdout + process.stderr
