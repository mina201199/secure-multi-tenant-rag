"""PDF publication capabilities exercised with real restricted PostgreSQL roles."""
import hashlib
import os
import uuid

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

from app import bootstrap, version_service as service
from tests.helpers import login
from tests.test_version_writes import write_document, processor_db, claim, pointer


SOURCE = b'%PDF-1.7\nsynthetic source; parser is tested separately\n%%EOF'
PAGES = [{'page': 1, 'text': 'Part A-1001: 1,500\nM8x30'},
         {'page': 2, 'text': '第二頁\nPreserve 00.10'}]
PDF_ERRORS = ['invalid_pdf', 'encrypted_pdf', 'unreadable_page', 'unsupported_pdf',
              'pdf_limit_exceeded', 'pdf_timeout', 'pdf_resource_limit', 'pdf_parser_unavailable']


def test_pdf_visible_header_example_is_not_rejected_as_txt_binary(runtime_db, processor_db, write_document):
    result = create_pdf(runtime_db, write_document)
    job = claim(processor_db, result)
    pages = [{'page': 1, 'text': '%PDF-1.7 header example'}]
    completed = complete_pdf(processor_db, job, pages)
    processor_db.commit()
    assert completed['status'] == 'ready'


def create_pdf(connection, doc, source=SOURCE, source_format='pdf', key='pdf_acceptance_0001'):
    row = connection.execute(
        'SELECT app_core.create_version(%s,%s,%s,%s,%s,%s,%s) AS result',
        (doc['owner']['tenant_id'], doc['owner']['id'], doc['id'], doc['old'], source, key, source_format)
    ).fetchone()['result']
    connection.commit()
    return row


def complete_pdf(connection, job, pages=PAGES, digest=None, lease=None):
    return connection.execute(
        'SELECT app_core.complete_pdf_version_job(%s,%s,%s,%s) AS result',
        (job['job_id'], lease or job['lease_token'], digest or job['source_sha256'], Jsonb(pages))
    ).fetchone()['result']


def test_pdf_publication_is_private_and_atomic(runtime_db, processor_db, admin_db, client, write_document):
    doc = write_document
    result = create_pdf(runtime_db, doc)
    assert create_pdf(runtime_db, doc) == result
    assert admin_db.execute('SELECT source_format FROM app_core.version_sources WHERE version_id=%s',
                            (result['version_id'],)).fetchone() == ('pdf',)
    assert admin_db.execute('SELECT visibility,content,source_pages,parser_version FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == ('private', None, None, None)
    assert admin_db.execute('SELECT count(*) FROM app_core.version_readers WHERE version_id=%s',
                            (result['version_id'],)).fetchone() == (0,)
    login(client, 'a.reader')
    assert client.get(f"/api/documents/{doc['id']}").status_code == 200
    job = claim(processor_db, result)
    assert job['source_format'] == 'pdf'
    assert complete_pdf(processor_db, job)['status'] == 'ready'
    assert pointer(admin_db, doc) == doc['old']
    assert admin_db.execute('SELECT content,source_pages FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == (None, None)
    processor_db.commit()
    assert pointer(admin_db, doc) == result['version_id']
    assert admin_db.execute('SELECT content,source_pages,parser_version FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == (
        'Part A-1001: 1,500\nM8x30\n\n第二頁\nPreserve 00.10', PAGES, 'pypdf-6.21.1-v1')
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
    assert client.get(f"/documents/{doc['id']}").status_code == 404


def test_pdf_rollback_and_stale_lease_cannot_publish(runtime_db, processor_db, admin_db, write_document):
    doc = write_document
    result = create_pdf(runtime_db, doc)
    job = claim(processor_db, result)
    for statement, params in (
        ('SELECT app_core.complete_pdf_version_job(%s,%s,%s,%s)',
         (job['job_id'], uuid.uuid4(), job['source_sha256'], Jsonb(PAGES))),
        ('SELECT app_core.fail_pdf_version_job(%s,%s,%s)',
         (job['job_id'], uuid.uuid4(), 'invalid_pdf')),
    ):
        with pytest.raises(psycopg.errors.SerializationFailure):
            processor_db.execute(statement, params)
        processor_db.rollback()
    complete_pdf(processor_db, job)
    processor_db.rollback()
    assert pointer(admin_db, doc) == doc['old']
    assert admin_db.execute('SELECT status,content,source_pages,parser_version FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == ('queued', None, None, None)
    assert complete_pdf(processor_db, job)['status'] == 'ready'
    processor_db.commit()


@pytest.mark.parametrize('pages', [None, {}, [], [None], ['text'],
    [{'page': 0, 'text': 'text'}], [{'page': 2, 'text': 'text'}],
    [{'page': '1', 'text': 'text'}], [{'page': True, 'text': 'text'}],
    [{'page': 1, 'text': None}], [{'page': 1, 'text': 42}],
    [{'page': 1, 'text': ''}], [{'page': 1, 'text': '\u00a0\u3000'}],
    [{'page': 1, 'text': 'bad\x01text'}], [{'page': 1, 'text': 'bad\u0085text'}],
    [{'page': 1, 'text': 'text', 'extra': 'untrusted'}],
    [{'page': 1}], [{'text': 'text'}],
    [{'page': 1, 'text': 'a' * 200001}],
    [{'page': i, 'text': 'text'} for i in range(1, 22)],
    [{'page': 1, 'text': 'text'}, {'page': 1, 'text': 'repeated'}],
], ids=lambda value: type(value).__name__)
def test_malformed_pdf_result_fails_closed(runtime_db, processor_db, admin_db, write_document, pages):
    doc = write_document
    result = create_pdf(runtime_db, doc)
    job = claim(processor_db, result)
    outcome = complete_pdf(processor_db, job, pages)
    processor_db.commit()
    assert (outcome['status'], outcome['error_code']) == ('failed', 'invalid_source')
    assert pointer(admin_db, doc) == doc['old']
    assert admin_db.execute('SELECT content,source_pages,parser_version FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == (None, None, None)


@pytest.mark.parametrize('case', ['digest', 'txt_completion', 'pdf_completion_on_txt'])
def test_source_digest_and_format_bind_publication(runtime_db, processor_db, admin_db, write_document, case):
    doc = write_document
    result = create_pdf(runtime_db, doc, b'plain text', 'txt') if case == 'pdf_completion_on_txt' else create_pdf(runtime_db, doc)
    job = claim(processor_db, result)
    outcome = (service.complete_job(processor_db, job['job_id'], job['lease_token']) if case == 'txt_completion'
               else complete_pdf(processor_db, job, digest='0' * 64 if case == 'digest' else None))
    processor_db.commit()
    assert (outcome['status'], outcome['error_code']) == ('failed', 'invalid_source')
    assert pointer(admin_db, doc) == doc['old']


@pytest.mark.parametrize('change,code', [('inactive', 'actor_inactive'), ('archived', 'archived'),
    ('ownership', 'ownership_changed'), ('target_owner', 'ownership_changed'),
    ('base', 'base_conflict'), ('not_ready', 'version_unavailable'), ('expired', 'lease_expired')])
def test_pdf_completion_revalidates_authority(runtime_db, processor_db, admin_db, write_document, change, code):
    doc = write_document
    result = create_pdf(runtime_db, doc)
    job = claim(processor_db, result)
    if change == 'inactive':
        admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (doc['owner']['id'],))
    elif change == 'archived':
        admin_db.execute("UPDATE app_core.documents SET status='archived' WHERE id=%s", (doc['id'],))
    elif change == 'ownership':
        admin_db.execute('UPDATE app_core.documents SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'], doc['id']))
    elif change == 'target_owner':
        admin_db.execute('UPDATE app_core.document_versions SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'], result['version_id']))
    elif change == 'not_ready':
        admin_db.execute("UPDATE app_core.document_versions SET status='failed' WHERE id=%s", (doc['old'],))
    elif change == 'expired':
        admin_db.execute("UPDATE app_core.version_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (job['job_id'],))
    else:
        admin_db.execute('UPDATE app_core.documents SET current_version_id=NULL WHERE id=%s', (doc['id'],))
    before = pointer(admin_db, doc)
    outcome = complete_pdf(processor_db, job)
    processor_db.commit()
    assert (outcome['status'], outcome['error_code']) == ('failed', code)
    assert pointer(admin_db, doc) == before


@pytest.mark.parametrize('code', PDF_ERRORS)
def test_pdf_failure_stores_only_safe_code(runtime_db, processor_db, admin_db, write_document, code):
    doc = write_document
    result = create_pdf(runtime_db, doc)
    job = claim(processor_db, result)
    outcome = processor_db.execute('SELECT app_core.fail_pdf_version_job(%s,%s,%s) AS result',
                                   (job['job_id'], job['lease_token'], code)).fetchone()['result']
    processor_db.commit()
    assert (outcome['status'], outcome['error_code']) == ('failed', code)
    assert pointer(admin_db, doc) == doc['old']


@pytest.mark.parametrize('code', [None, 'unknown', 'secret document contents', 'processing_failed'])
def test_unknown_pdf_error_is_not_persisted(runtime_db, processor_db, admin_db, write_document, code):
    result = create_pdf(runtime_db, write_document)
    job = claim(processor_db, result)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        processor_db.execute('SELECT app_core.fail_pdf_version_job(%s,%s,%s)', (job['job_id'], job['lease_token'], code))
    processor_db.rollback()
    assert admin_db.execute('SELECT status,error_code FROM app_core.version_jobs WHERE id=%s',
                            (job['job_id'],)).fetchone() == ('processing', None)


@pytest.mark.parametrize('source,format,error', [(b'plain text', 'pdf', psycopg.errors.InvalidParameterValue),
    (b'', 'pdf', psycopg.errors.InvalidParameterValue), (SOURCE, 'PDF', psycopg.errors.InvalidParameterValue),
    (SOURCE, None, psycopg.errors.InvalidParameterValue), (SOURCE, 'docx', psycopg.errors.InvalidParameterValue),
    (SOURCE + b'a' * 2097152, 'pdf', psycopg.errors.ProgramLimitExceeded),
    (SOURCE, 'txt', psycopg.errors.InvalidParameterValue)])
def test_invalid_source_format_does_not_allocate(runtime_db, admin_db, write_document, source, format, error):
    with pytest.raises(error):
        create_pdf(runtime_db, write_document, source, format)
    runtime_db.rollback()
    assert admin_db.execute('SELECT count(*) FROM app_core.version_jobs WHERE document_id=%s',
                            (write_document['id'],)).fetchone() == (0,)


def test_legacy_txt_fingerprint_and_new_txt_overload_are_identical(runtime_db, processor_db, admin_db, write_document):
    doc = write_document
    result = service.create_version(runtime_db, doc['owner'], doc['id'], doc['old'], b'legacy body', 'pdf_acceptance_0001')
    runtime_db.commit()
    assert create_pdf(runtime_db, doc, b'legacy body', 'txt') == result
    expected = hashlib.sha256((hashlib.sha256(b'legacy body').hexdigest() + ':TXT:' + str(doc['old'])).encode()).hexdigest()
    assert admin_db.execute('SELECT request_fingerprint FROM app_core.version_jobs WHERE id=%s',
                            (result['job_id'],)).fetchone() == (expected,)
    job = claim(processor_db, result)
    assert job['source_format'] == 'txt'
    service.complete_job(processor_db, job['job_id'], job['lease_token'])
    processor_db.commit()
    assert admin_db.execute('SELECT content,source_pages,parser_version FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == ('legacy body', None, None)


def test_pdf_capabilities_have_no_public_or_web_publish_grants(admin_db, runtime_db, processor_db, write_document):
    for role in ('rag_app', 'rag_worker', 'rag_version_processor'):
        for signature in ('complete_pdf_version_job(uuid,uuid,text,jsonb)', 'fail_pdf_version_job(uuid,uuid,text)'):
            assert admin_db.execute('SELECT has_function_privilege(%s,%s,\'EXECUTE\')',
                                    (role, 'app_core.' + signature)).fetchone() == (role != 'rag_app',)
    for signature in ('validate_version_pdf_pages(jsonb)', 'finish_pdf_version_job(uuid,uuid,text,jsonb,text)'):
        for role in ('rag_app', 'rag_worker', 'rag_version_processor'):
            assert admin_db.execute('SELECT has_function_privilege(%s,%s,\'EXECUTE\')',
                                    (role, 'app_core.' + signature)).fetchone() == (False,)
    result = create_pdf(runtime_db, write_document)
    with pytest.raises(psycopg.errors.InvalidParameterValue):
        admin_db.execute("UPDATE app_core.version_sources SET source_format='txt' WHERE version_id=%s", (result['version_id'],))
    for connection in (runtime_db, processor_db):
        for statement in ('SELECT source_bytes FROM app_core.version_sources',
                          "UPDATE app_core.document_versions SET source_pages='[]',parser_version='forged' WHERE id=%s"):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement, (result['version_id'],) if '%s' in statement else None)
            connection.rollback()
    assert admin_db.execute("""SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
        CROSS JOIN LATERAL aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
        WHERE n.nspname='app_core' AND p.proname LIKE '%pdf%' AND a.grantee=0 AND a.privilege_type='EXECUTE'""").fetchone() == (0,)


def test_upgrade_keeps_queued_txt_source_fingerprint_and_claim(admin_db):
    name = 'test_pdf_upgrade_' + uuid.uuid4().hex
    admin_db.execute(sql.SQL('CREATE DATABASE {}').format(sql.Identifier(name)))
    try:
        with psycopg.connect(os.environ['DATABASE_ADMIN_URL'], dbname=name) as connection:
            connection.execute('CREATE SCHEMA app_core')
            for path in sorted(bootstrap.MIGRATIONS_DIR.glob('*.sql')):
                if path.name.startswith('006'):
                    break
                connection.execute(path.read_text(encoding='utf-8'))
            connection.execute("""INSERT INTO app_core.tenants(name) VALUES('PDF upgrade');
                INSERT INTO app_core.users(tenant_id,username,display_name,password_hash,role)
                VALUES(1,'upgrade.owner','owner','hash','employee');
                INSERT INTO app_core.documents(tenant_id,owner_id,title,category) VALUES(1,1,'old','test');
                INSERT INTO app_core.document_versions(tenant_id,document_id,version_number,owner_id,status,content)
                VALUES(1,1,1,1,'ready','old');
                UPDATE app_core.documents SET current_version_id=1""")
            original = connection.execute("SELECT app_core.create_version(1,1,1,1,'old queued text'::bytea,'upgrade_pdf_00001')").fetchone()[0]
            fingerprint = connection.execute('SELECT request_fingerprint FROM app_core.version_jobs').fetchone()
            connection.execute((bootstrap.MIGRATIONS_DIR / '006_pdf_processing.sql').read_text(encoding='utf-8'))
            assert connection.execute('SELECT source_format FROM app_core.version_sources').fetchone() == ('txt',)
            assert connection.execute('SELECT request_fingerprint FROM app_core.version_jobs').fetchone() == fingerprint
            assert connection.execute("SELECT app_core.create_version(1,1,1,1,'old queued text'::bytea,'upgrade_pdf_00001','txt')").fetchone()[0] == original
            job = connection.execute('SELECT app_core.claim_version_job()').fetchone()[0]
            assert job['source_format'] == 'txt'
            assert connection.execute('SELECT app_core.complete_version_job(%s,%s)', (job['job_id'], job['lease_token'])).fetchone()[0]['status'] == 'ready'
            assert connection.execute('SELECT content,source_pages,parser_version FROM app_core.document_versions WHERE id=2').fetchone() == ('old queued text', None, None)
    finally:
        admin_db.execute(sql.SQL('DROP DATABASE {}').format(sql.Identifier(name)))
