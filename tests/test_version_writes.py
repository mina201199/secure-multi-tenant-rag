"""Independent M2 TXT write acceptance against real runtime and processor roles."""
import hashlib
import inspect
import os
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor

import psycopg
import pytest
from fastapi import HTTPException
from psycopg.rows import dict_row

from app import version_service as service
from app import repository
from tests.helpers import login


@pytest.fixture
def write_document(admin_db):
    users = {row[0]: {'id': row[1], 'tenant_id': row[2]} for row in admin_db.execute(
        'SELECT username,id,tenant_id FROM app_core.users')}
    owner = users['a.owner']
    document_id = admin_db.execute(
        "INSERT INTO app_core.documents(tenant_id,owner_id,title,category) VALUES(%s,%s,%s,'acceptance') RETURNING id",
        (owner['tenant_id'], owner['id'], 'TXT acceptance ' + uuid.uuid4().hex)).fetchone()[0]
    old = admin_db.execute(
        """INSERT INTO app_core.document_versions(tenant_id,document_id,version_number,owner_id,status,content,visibility)
           VALUES(%s,%s,1,%s,'ready','old acceptance content','company') RETURNING id""",
        (owner['tenant_id'], document_id, owner['id'])).fetchone()[0]
    admin_db.execute('UPDATE app_core.documents SET current_version_id=%s WHERE id=%s', (old, document_id))
    admin_db.execute('INSERT INTO app_core.version_readers VALUES(%s,%s,%s)',
                     (owner['tenant_id'], old, users['a.reader']['id']))
    try:
        yield {'id': document_id, 'old': old, 'owner': owner, 'users': users}
    finally:
        admin_db.execute('UPDATE app_core.users SET is_active=true WHERE id=%s', (owner['id'],))
        admin_db.execute('UPDATE app_core.documents SET current_version_id=NULL WHERE id=%s', (document_id,))
        admin_db.execute('DELETE FROM app_core.version_jobs WHERE document_id=%s', (document_id,))
        admin_db.execute('DELETE FROM app_core.version_sources WHERE document_id=%s', (document_id,))
        admin_db.execute('DELETE FROM app_core.version_readers WHERE version_id IN '
                         '(SELECT id FROM app_core.document_versions WHERE document_id=%s)', (document_id,))
        admin_db.execute('DELETE FROM app_core.document_versions WHERE document_id=%s', (document_id,))
        admin_db.execute('DELETE FROM app_core.documents WHERE id=%s', (document_id,))


@pytest.fixture
def processor_db():
    with psycopg.connect(os.environ['DATABASE_ADMIN_URL'], row_factory=dict_row) as connection:
        connection.execute('SET ROLE rag_version_processor')
        assert connection.execute('SELECT current_user AS role').fetchone()['role'] == 'rag_version_processor'
        connection.commit()
        yield connection


def create(connection, doc, source=b'new acceptance content', key='acceptance_key_0001', expected=None, identity=None):
    result = service.create_version(connection, identity or doc['owner'], doc['id'],
                                    doc['old'] if expected is None else expected, source, key)
    connection.commit()
    return result


def pointer(admin_db, doc):
    return admin_db.execute('SELECT current_version_id FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone()[0]


def claim(processor_db, result):
    job = service.claim_job(processor_db)
    processor_db.commit()
    assert str(job['job_id']) == str(result['job_id'])
    return job


def expect_http(connection, status, operation):
    with pytest.raises(HTTPException) as caught:
        operation()
    connection.rollback()
    assert caught.value.status_code == status


def test_owner_creates_private_unpublished_source_without_copying_acl(runtime_db, admin_db, write_document):
    doc = write_document
    source = b'\xef\xbb\xbf' + 'new text 中文\r\n'.encode()
    result = create(runtime_db, doc, source)
    assert result == {'document_id': doc['id'], 'version_id': result['version_id'], 'version_number': 2,
                      'processing_status': 'queued', 'is_current': False, 'job_id': result['job_id']}
    assert pointer(admin_db, doc) == doc['old']
    assert admin_db.execute('SELECT status,visibility,owner_id,content FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == ('queued', 'private', doc['owner']['id'], None)
    assert admin_db.execute('SELECT count(*) FROM app_core.version_readers WHERE version_id=%s',
                            (result['version_id'],)).fetchone() == (0,)
    stored = admin_db.execute('SELECT source_bytes,source_sha256 FROM app_core.version_sources WHERE version_id=%s',
                              (result['version_id'],)).fetchone()
    assert bytes(stored[0]) == source
    assert stored[1] in (hashlib.sha256(source).hexdigest(), hashlib.sha256(source).digest())
    assert service.get_version_management(runtime_db, doc['owner'], doc['id'])['current_version_id'] == doc['old']
    status = service.get_job(runtime_db, doc['owner'], result['job_id'])
    assert set(status) <= {'job_id', 'version_id', 'status', 'error_code', 'retryable'}
    assert status['status'] == 'queued'


@pytest.mark.parametrize('username', ['a.reader', 'a.staff', 'a.admin', 'a.manager', 'b.owner'])
def test_only_document_owner_can_create_or_manage(runtime_db, admin_db, write_document, username):
    doc = write_document
    identity = doc['users'][username]
    assert service.get_version_management(runtime_db, identity, doc['id']) is None
    expect_http(runtime_db, 404, lambda: create(runtime_db, doc, identity=identity))
    assert admin_db.execute('SELECT count(*) FROM app_core.document_versions WHERE document_id=%s',
                            (doc['id'],)).fetchone() == (1,)


def test_version_owner_alone_does_not_authorize_document_management(runtime_db, admin_db, write_document):
    doc = write_document
    admin_db.execute('UPDATE app_core.documents SET owner_id=%s WHERE id=%s',
                     (doc['users']['a.staff']['id'], doc['id']))
    expect_http(runtime_db, 404, lambda: create(runtime_db, doc))
    result = create(runtime_db, doc, identity=doc['users']['a.staff'])
    assert admin_db.execute('SELECT owner_id FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == (doc['users']['a.staff']['id'],)


def test_mismatched_actor_tenant_cannot_create(runtime_db, write_document):
    doc = write_document
    forged = {'id': doc['owner']['id'], 'tenant_id': doc['users']['b.owner']['tenant_id']}
    expect_http(runtime_db, 404, lambda: create(runtime_db, doc, identity=forged))


@pytest.mark.parametrize('change', ['inactive', 'archived', 'not_ready', 'no_current'])
def test_unavailable_document_cannot_allocate_new_work(runtime_db, admin_db, write_document, change):
    doc = write_document
    if change == 'inactive':
        admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (doc['owner']['id'],))
    elif change == 'archived':
        admin_db.execute("UPDATE app_core.documents SET status='archived' WHERE id=%s", (doc['id'],))
    elif change == 'not_ready':
        admin_db.execute("UPDATE app_core.document_versions SET status='failed' WHERE id=%s", (doc['old'],))
    else:
        admin_db.execute('UPDATE app_core.documents SET current_version_id=NULL WHERE id=%s', (doc['id'],))
    expect_http(runtime_db, 404, lambda: create(runtime_db, doc))
    assert admin_db.execute('SELECT count(*) FROM app_core.version_jobs WHERE document_id=%s', (doc['id'],)).fetchone() == (0,)


def test_replay_before_and_after_publish_returns_original_response(runtime_db, processor_db, write_document):
    doc = write_document
    result = create(runtime_db, doc)
    assert create(runtime_db, doc) == result
    job = claim(processor_db, result)
    service.complete_job(processor_db, job['job_id'], job['lease_token'])
    processor_db.commit()
    assert create(runtime_db, doc) == result
    assert service.get_job(runtime_db, doc['owner'], result['job_id'])['status'] == 'ready'


@pytest.mark.parametrize('change', ['source', 'base', 'key'])
def test_fingerprint_and_pending_conflicts(runtime_db, admin_db, write_document, change):
    doc = write_document
    create(runtime_db, doc)
    kwargs = {'source': b'changed'} if change == 'source' else (
        {'expected': doc['old'] + 999999} if change == 'base' else {'key': 'different_key_00001'})
    expect_http(runtime_db, 409, lambda: create(runtime_db, doc, **kwargs))
    assert admin_db.execute('SELECT count(*) FROM app_core.version_jobs WHERE document_id=%s', (doc['id'],)).fetchone() == (1,)


def test_stale_base_rejected_without_allocating_work(runtime_db, admin_db, write_document):
    expect_http(runtime_db, 409, lambda: create(runtime_db, write_document, expected=999999999))
    assert admin_db.execute('SELECT count(*) FROM app_core.version_jobs WHERE document_id=%s',
                            (write_document['id'],)).fetchone() == (0,)


def test_failure_preserves_old_content_acl_and_never_reuses_version_number(runtime_db, processor_db, admin_db, write_document):
    doc = write_document
    first = create(runtime_db, doc)
    job = claim(processor_db, first)
    service.fail_job(processor_db, job['job_id'], job['lease_token'])
    processor_db.commit()
    assert pointer(admin_db, doc) == doc['old']
    assert service.get_job(runtime_db, doc['owner'], first['job_id'])['status'] == 'failed'
    assert admin_db.execute('SELECT content FROM app_core.document_versions WHERE id=%s', (doc['old'],)).fetchone() == ('old acceptance content',)
    assert admin_db.execute('SELECT count(*) FROM app_core.version_readers WHERE version_id=%s', (doc['old'],)).fetchone() == (1,)
    assert create(runtime_db, doc, key='second_key_0000001')['version_number'] == 3


def test_publish_decodes_saved_bytes_and_commits_pointer_atomically(runtime_db, processor_db, admin_db, client, write_document):
    doc = write_document
    result = create(runtime_db, doc, b'\xef\xbb\xbf' + 'trusted saved text 中文'.encode())
    login(client, 'a.reader')
    assert client.get(f"/api/documents/{doc['id']}").status_code == 200
    job = claim(processor_db, result)
    assert job['source_bytes'] == b'\xef\xbb\xbf' + 'trusted saved text 中文'.encode()
    assert list(inspect.signature(service.complete_job).parameters) == ['connection', 'job_id', 'lease_token']
    service.complete_job(processor_db, job['job_id'], job['lease_token'])
    # A separate database session must still see the entire old state before commit.
    assert pointer(admin_db, doc) == doc['old']
    assert admin_db.execute('SELECT status FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == ('queued',)
    processor_db.commit()
    assert pointer(admin_db, doc) == result['version_id']
    assert admin_db.execute('SELECT status,content FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == ('ready', 'trusted saved text 中文')
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
    assert client.get(f"/documents/{doc['id']}").status_code == 404


def test_rolled_back_publication_leaves_no_partial_pointer_or_ready_version(runtime_db, processor_db, admin_db, write_document):
    doc = write_document
    result = create(runtime_db, doc)
    job = claim(processor_db, result)
    service.complete_job(processor_db, job['job_id'], job['lease_token'])
    processor_db.rollback()
    assert pointer(admin_db, doc) == doc['old']
    assert admin_db.execute('SELECT status,content FROM app_core.document_versions WHERE id=%s',
                            (result['version_id'],)).fetchone() == ('queued', None)
    assert admin_db.execute('SELECT status FROM app_core.version_jobs WHERE id=%s', (job['job_id'],)).fetchone() == ('processing',)
    service.complete_job(processor_db, job['job_id'], job['lease_token'])
    processor_db.commit()
    assert pointer(admin_db, doc) == result['version_id']


def test_saved_source_and_digest_cannot_be_overwritten(runtime_db, admin_db, write_document):
    result = create(runtime_db, write_document)
    before = admin_db.execute('SELECT source_bytes,source_sha256 FROM app_core.version_sources WHERE version_id=%s',
                              (result['version_id'],)).fetchone()
    for assignment in ("source_bytes='tampered'::bytea", "source_sha256='forged'"):
        with pytest.raises(psycopg.Error):
            admin_db.execute(f'UPDATE app_core.version_sources SET {assignment} WHERE version_id=%s', (result['version_id'],))
        assert admin_db.execute('SELECT source_bytes,source_sha256 FROM app_core.version_sources WHERE version_id=%s',
                                (result['version_id'],)).fetchone() == before


@pytest.mark.parametrize('content', [None, '', '   '])
def test_ready_version_requires_nonempty_body(admin_db, write_document, content):
    doc = write_document
    with pytest.raises(psycopg.errors.CheckViolation):
        admin_db.execute("""INSERT INTO app_core.document_versions
            (tenant_id,document_id,version_number,owner_id,status,content)
            VALUES(%s,%s,2,%s,'ready',%s)""", (doc['owner']['tenant_id'], doc['id'], doc['owner']['id'], content))


@pytest.mark.parametrize('change', ['inactive', 'archived', 'ownership', 'base', 'not_ready'])
def test_publish_revalidates_authority_and_base(runtime_db, processor_db, admin_db, write_document, change):
    doc = write_document
    result = create(runtime_db, doc)
    job = claim(processor_db, result)
    if change == 'inactive':
        admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (doc['owner']['id'],))
    elif change == 'archived':
        admin_db.execute("UPDATE app_core.documents SET status='archived' WHERE id=%s", (doc['id'],))
    elif change == 'ownership':
        admin_db.execute('UPDATE app_core.documents SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'], doc['id']))
    elif change == 'not_ready':
        admin_db.execute("UPDATE app_core.document_versions SET status='failed' WHERE id=%s", (doc['old'],))
    else:
        replacement = admin_db.execute(
            """INSERT INTO app_core.document_versions(tenant_id,document_id,version_number,owner_id,status,content)
               VALUES(%s,%s,3,%s,'ready','other publication') RETURNING id""",
            (doc['owner']['tenant_id'], doc['id'], doc['owner']['id'])).fetchone()[0]
        admin_db.execute('UPDATE app_core.documents SET current_version_id=%s WHERE id=%s', (replacement, doc['id']))
    previous = pointer(admin_db, doc)
    document_state = admin_db.execute('SELECT status,owner_id FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone()
    service.complete_job(processor_db, job['job_id'], job['lease_token'])
    processor_db.commit()
    assert pointer(admin_db, doc) == previous
    assert admin_db.execute('SELECT status,owner_id FROM app_core.documents WHERE id=%s', (doc['id'],)).fetchone() == document_state
    assert admin_db.execute('SELECT status FROM app_core.version_jobs WHERE id=%s', (result['job_id'],)).fetchone() == ('failed',)
    assert admin_db.execute('SELECT status FROM app_core.document_versions WHERE id=%s', (result['version_id'],)).fetchone() == ('failed',)


@pytest.mark.parametrize('change', ['inactive', 'archived', 'ownership'])
def test_replay_and_job_status_reauthorize_current_owner(runtime_db, admin_db, write_document, change):
    doc = write_document
    result = create(runtime_db, doc)
    if change == 'inactive':
        admin_db.execute('UPDATE app_core.users SET is_active=false WHERE id=%s', (doc['owner']['id'],))
    elif change == 'archived':
        admin_db.execute("UPDATE app_core.documents SET status='archived' WHERE id=%s", (doc['id'],))
    else:
        admin_db.execute('UPDATE app_core.documents SET owner_id=%s WHERE id=%s', (doc['users']['a.staff']['id'], doc['id']))
    expect_http(runtime_db, 404, lambda: create(runtime_db, doc))
    expect_http(runtime_db, 404, lambda: service.get_job(runtime_db, doc['owner'], result['job_id']))


def test_stale_lease_does_not_change_work(runtime_db, processor_db, admin_db, write_document):
    doc = write_document
    result = create(runtime_db, doc)
    job = claim(processor_db, result)
    for operation in (service.complete_job, service.fail_job):
        expect_http(processor_db, 409, lambda: operation(processor_db, job['job_id'], str(uuid.uuid4())))
    assert pointer(admin_db, doc) == doc['old']
    assert admin_db.execute('SELECT status FROM app_core.version_jobs WHERE id=%s', (job['job_id'],)).fetchone() == ('processing',)
    service.complete_job(processor_db, job['job_id'], job['lease_token'])
    processor_db.commit()
    assert pointer(admin_db, doc) == result['version_id']


@pytest.mark.parametrize('username', ['a.reader', 'a.admin', 'b.owner'])
def test_job_status_is_not_accessible_to_other_actors(runtime_db, write_document, username):
    doc = write_document
    result = create(runtime_db, doc)
    expect_http(runtime_db, 404, lambda: service.get_job(runtime_db, doc['users'][username], result['job_id']))


@pytest.mark.parametrize('source,status', [(b'', 422), (b' \r\n\t', 422), (b'%PDF-1.4 fake', 422),
                                         (b'bad\xff', 422), (b'binary\x00text', 422),
                                         ('\u00a0\u3000'.encode(), 422), (b'good\x01text', 422),
                                         ('good\u0085text'.encode(), 422),
                                         (b'a' * (1024 * 1024 + 1), 413), (b'a' * 200001, 413)])
def test_invalid_txt_never_creates_work(runtime_db, admin_db, write_document, source, status):
    doc = write_document
    expect_http(runtime_db, status, lambda: create(runtime_db, doc, source))
    assert admin_db.execute('SELECT count(*) FROM app_core.document_versions WHERE document_id=%s', (doc['id'],)).fetchone() == (1,)
    assert admin_db.execute('SELECT count(*) FROM app_core.version_sources WHERE document_id=%s', (doc['id'],)).fetchone() == (0,)


@pytest.mark.parametrize('key', ['short', 'x' * 129, 'a' * 16 + '/', '中文字元不合法測試000000000'])
def test_invalid_idempotency_key_rejected(runtime_db, write_document, key):
    expect_http(runtime_db, 422, lambda: create(runtime_db, write_document, key=key))


def test_runtime_and_processor_cannot_rewrite_source_or_publish_directly(runtime_db, processor_db, write_document):
    doc = write_document
    result = create(runtime_db, doc)
    assert runtime_db.execute('SELECT current_user AS role').fetchone()['role'] == 'rag_app'
    runtime_db.commit()
    for connection in (runtime_db, processor_db):
        for statement in (
            'SELECT source_bytes FROM app_core.version_sources WHERE version_id=%s',
            "UPDATE app_core.version_sources SET source_bytes='tamper'::bytea WHERE version_id=%s",
            'DELETE FROM app_core.version_sources WHERE version_id=%s',
        ):
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                connection.execute(statement, (result['version_id'],))
            connection.rollback()
    for function, parameters in (
        ('claim_version_job', ()), ('complete_version_job', (result['job_id'], str(uuid.uuid4()))),
        ('fail_version_job', (result['job_id'], str(uuid.uuid4()))),
    ):
        placeholders = ','.join(['%s::uuid'] * len(parameters))
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime_db.execute(f'SELECT app_core.{function}({placeholders})', parameters)
        runtime_db.rollback()
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        runtime_db.execute('UPDATE app_core.documents SET current_version_id=%s WHERE id=%s', (result['version_id'], doc['id']))
    runtime_db.rollback()
    for statement in (
        "INSERT INTO app_core.document_versions(tenant_id,document_id,version_number,owner_id,status,content) VALUES(%s,%s,99,%s,'ready','forged')",
        "INSERT INTO app_core.version_sources(tenant_id,document_id,version_id,source_bytes) VALUES(%s,%s,%s,'forged'::bytea)",
    ):
        values = (doc['owner']['tenant_id'], doc['id'], doc['owner']['id'] if 'document_versions(' in statement else result['version_id'])
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            runtime_db.execute(statement, values)
        runtime_db.rollback()


def test_security_definer_roles_are_narrow_and_public_cannot_execute(admin_db):
    for name in ('rag_version_writer', 'rag_version_processor'):
        assert admin_db.execute('SELECT rolcanlogin,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=%s', (name,)).fetchone() == (False, False, False)
        assert admin_db.execute("SELECT pg_has_role('rag_app',%s,'MEMBER')", (name,)).fetchone() == (False,)
    rows = admin_db.execute("""SELECT p.oid, p.prosecdef,r.rolname,p.proconfig,
        EXISTS(SELECT 1 FROM aclexplode(COALESCE(p.proacl,acldefault('f',p.proowner))) a
               WHERE a.grantee=0 AND a.privilege_type='EXECUTE')
        FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace JOIN pg_roles r ON r.oid=p.proowner
        WHERE n.nspname='app_core' AND p.proname IN
        ('create_version','get_version_management','get_job','claim_version_job','complete_version_job','fail_version_job')""").fetchall()
    assert len(rows) == 7
    for _, definer, owner, settings, public_execute in rows:
        assert definer and owner == 'rag_version_writer'
        assert any(setting.startswith('search_path=') for setting in settings or [])
        assert public_execute is False
    assert admin_db.execute("""SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        JOIN pg_roles r ON r.oid=c.relowner WHERE n.nspname='app_core' AND c.relkind='r'
        AND r.rolname='rag_version_writer'""").fetchone() == (0,)


def test_tenant_composite_foreign_keys_protect_sources_and_jobs(runtime_db, admin_db, write_document):
    result = create(runtime_db, write_document)
    other_tenant = write_document['users']['b.owner']['tenant_id']
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_db.execute('UPDATE app_core.version_jobs SET tenant_id=%s WHERE id=%s', (other_tenant, result['job_id']))
    # Source rows may reject every UPDATE before FK evaluation; verify the actual composite FK as well.
    for table in ('version_sources', 'version_jobs'):
        definitions = [row[0] for row in admin_db.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conrelid=%s::regclass AND contype='f'",
            ('app_core.' + table,))]
        assert any('FOREIGN KEY (tenant_id, document_id, version_id)' in definition for definition in definitions)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        admin_db.execute('INSERT INTO app_core.version_sources(tenant_id,document_id,version_id,source_bytes) VALUES(%s,%s,-1,%s)',
                         (other_tenant, write_document['id'], b'orphan'))


def test_expired_matching_lease_fails_without_publishing(runtime_db, processor_db, admin_db, write_document):
    doc = write_document
    result = create(runtime_db, doc)
    job = claim(processor_db, result)
    admin_db.execute("UPDATE app_core.version_jobs SET lease_expires_at=now()-interval '1 second' WHERE id=%s", (job['job_id'],))
    service.complete_job(processor_db, job['job_id'], job['lease_token'])
    processor_db.commit()
    assert pointer(admin_db, doc) == doc['old']
    assert admin_db.execute('SELECT status,error_code FROM app_core.version_jobs WHERE id=%s',
                            (job['job_id'],)).fetchone() == ('failed', 'lease_expired')


@pytest.mark.parametrize('same_key', [False, True])
def test_two_actual_runtime_connections_serialize_create(admin_db, write_document, same_key):
    doc = write_document
    barrier = threading.Barrier(2)
    def attempt(index):
        with psycopg.connect(os.environ['DATABASE_URL'], row_factory=dict_row) as connection:
            connection.execute("SET statement_timeout='8s'")
            barrier.wait(timeout=5)
            try:
                return (202, create(connection, doc, key='concurrency_key_000' if same_key else f'concurrency_key_00{index}'))
            except HTTPException as error:
                connection.rollback()
                return (error.status_code, None)
    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = list(executor.map(attempt, (1, 2)))
    assert sorted(code for code, _ in outcomes) == ([202, 202] if same_key else [202, 409])
    if same_key:
        assert outcomes[0][1] == outcomes[1][1]
    assert admin_db.execute('SELECT count(*) FROM app_core.version_jobs WHERE document_id=%s', (doc['id'],)).fetchone() == (1,)
    assert admin_db.execute('SELECT count(*) FROM app_core.document_versions WHERE document_id=%s', (doc['id'],)).fetchone() == (2,)


@pytest.mark.parametrize('waiting_operation', ['share', 'publish'])
def test_publish_and_share_serialize_on_outgoing_version(runtime_db, processor_db, admin_db, write_document, waiting_operation):
    """Prove a waiter really blocked, then exercise the conflicting committed operation."""
    doc = write_document
    result = create(runtime_db, doc)
    job = claim(processor_db, result)
    name = 'version-acceptance-' + uuid.uuid4().hex
    outcome = []
    def waiting():
        url = os.environ['DATABASE_URL' if waiting_operation == 'share' else 'DATABASE_ADMIN_URL']
        with psycopg.connect(url, row_factory=dict_row, application_name=name) as connection:
            connection.execute("SET statement_timeout='8s'")
            if waiting_operation == 'publish':
                connection.execute('SET ROLE rag_version_processor')
            try:
                if waiting_operation == 'share':
                    repository.replace_sharing(connection, doc['owner'], doc['id'], doc['old'],
                                               'specified', [doc['users']['a.staff']['id']])
                else:
                    service.complete_job(connection, job['job_id'], job['lease_token'])
                connection.commit()
                outcome.append(200)
            except HTTPException as error:
                connection.rollback()
                outcome.append(error.status_code)
    worker = threading.Thread(target=waiting, daemon=True)
    with psycopg.connect(os.environ['DATABASE_ADMIN_URL'], row_factory=dict_row) as holder:
        holder.execute('SELECT id FROM app_core.document_versions WHERE id=%s FOR UPDATE', (doc['old'],))
        worker.start()
        try:
            deadline = time.monotonic() + 5
            blocked = False
            while time.monotonic() < deadline:
                admin_db.execute('SELECT pg_stat_clear_snapshot()')
                blocked = admin_db.execute("SELECT EXISTS(SELECT 1 FROM pg_stat_activity WHERE application_name=%s AND wait_event_type='Lock')", (name,)).fetchone()[0]
                if blocked:
                    break
                time.sleep(0.02)
            assert blocked, 'operation did not wait on the outgoing version lock'
            if waiting_operation == 'share':
                holder.execute('SET LOCAL ROLE rag_version_processor')
                service.complete_job(holder, job['job_id'], job['lease_token'])
            else:
                holder.execute('SET LOCAL ROLE rag_app')
                repository.replace_sharing(holder, doc['owner'], doc['id'], doc['old'],
                                           'specified', [doc['users']['a.staff']['id']])
            holder.commit()
        finally:
            holder.rollback()
            worker.join(timeout=10)
    assert not worker.is_alive()
    assert outcome == ([409] if waiting_operation == 'share' else [200])
    assert pointer(admin_db, doc) == result['version_id']
    assert admin_db.execute('SELECT count(*) FROM app_core.version_readers WHERE version_id=%s', (result['version_id'],)).fetchone() == (0,)
